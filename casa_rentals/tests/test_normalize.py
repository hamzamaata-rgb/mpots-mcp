"""Tests unitaires de normalize.py.

Les cas de test sont ecrits a partir de formulations reelles d'annonces marocaines
(francais, arabe, transliterations). Chaque piege identifie a son test de non-regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import normalize as N  # noqa: E402

RACINE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ref() -> N.Referentiel:
    return N.Referentiel.depuis_csv(RACINE / "quartiers_seed.csv")


# ---------------------------------------------------------------------------- texte

def test_normalize_text_accents_et_ponctuation():
    assert N.normalize_text("Maârif — Extension, Casablanca!") == "maarif extension casablanca"


def test_normalize_text_preserve_arabe():
    assert "مفروش" in N.normalize_text("شقة مفروشة مفروش")


def test_normalize_text_vide():
    assert N.normalize_text(None) == ""
    assert N.normalize_text("   ") == ""


# ------------------------------------------------------------------------------ PII

@pytest.mark.parametrize(
    "brut",
    [
        "Contact 0612345678 pour visiter",
        "tel : 06 12 34 56 78",
        "Appelez au +212 6 12 34 56 78",
        "whatsapp 0661-234567 dispo",
        "0700.11.22.33",
    ],
)
def test_scrub_pii_telephones(brut):
    sortie = N.scrub_pii(brut)
    assert "[tel]" in sortie
    assert not any(c.isdigit() for c in sortie)


def test_scrub_pii_email_et_url():
    sortie = N.scrub_pii("ecrire a agence@immo.ma ou https://immo.ma/profil/123")
    assert "[email]" in sortie and "[url]" in sortie
    assert "immo.ma/profil" not in sortie


def test_scrub_pii_preserve_prix_et_surface():
    """Le loyer et la surface ne doivent jamais etre pris pour un numero."""
    sortie = N.scrub_pii("Appartement 85 m2, loyer 6 500 DH, 3eme etage")
    assert "85 m2" in sortie and "6 500 DH" in sortie and "[tel]" not in sortie


def test_content_hash_stable_et_insensible_au_telephone():
    """Deux republications identiques au numero pres ont le meme hash."""
    a = N.content_hash("Appart Maarif", "Joli 80m2, tel 0612345678")
    b = N.content_hash("appart  maârif", "Joli 80m2, tel 0655443322")
    assert a == b
    assert a != N.content_hash("Appart Gauthier", "Joli 80m2")


# -------------------------------------------------------------------------- surface

@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("Appartement 75m2 a louer", 75.0),
        ("Superficie 75 m²", 75.0),
        ("75 metres carres habitables", 75.0),
        ("120 M2 avec terrasse", 120.0),
        ("surface 82,5 m2", 82.5),
        ("شقة 90 متر مربع", 90.0),
    ],
)
def test_parse_surface_formes_courantes(texte, attendu):
    assert N.parse_surface(texte) == attendu


def test_parse_surface_hors_bornes_rejetee():
    assert N.parse_surface("chambre de 8 m2") is None
    assert N.parse_surface("plateau de 1200 m2") is None


def test_parse_surface_ignore_les_distances():
    """'a 200 metres de la plage' n'est pas une surface."""
    assert N.parse_surface("Appartement a 200 metres de la plage") is None
    assert N.parse_surface("A 300 metres du tramway, appartement 70 m2") == 70.0


def test_parse_surface_prend_la_premiere_valeur_plausible():
    assert N.parse_surface("Appartement 90 m2 avec terrasse de 25 m2") == 90.0


def test_parse_surface_absente():
    assert N.parse_surface("Bel appartement lumineux au calme") is None
    assert N.parse_surface(None) is None


# ---------------------------------------------------------------------------- loyer

@pytest.mark.parametrize(
    "brut,attendu",
    [
        ("6500", 6500.0),
        ("6 500 DH", 6500.0),
        ("6.500 DH/mois", 6500.0),
        ("12 000 MAD", 12000.0),
        (8000, 8000.0),
        ("Loyer : 4500 dhs par mois", 4500.0),
    ],
)
def test_parse_loyer_formats(brut, attendu):
    valeur, motif = N.parse_loyer(brut)
    assert (valeur, motif) == (attendu, None)


def test_parse_loyer_sous_plancher():
    assert N.parse_loyer("800 DH") == (None, "sous_plancher")


def test_parse_loyer_vente_par_le_montant():
    """Une annonce de vente mal categorisee : 1 200 000 MAD n'est pas un loyer."""
    assert N.parse_loyer("1 200 000 DH") == (None, "vente")


def test_parse_loyer_vente_par_le_contexte():
    valeur, motif = N.parse_loyer("9000 DH", contexte="Appartement a vendre, prix de vente negociable")
    assert (valeur, motif) == (None, "vente")


def test_parse_loyer_courte_duree_exclue():
    valeur, motif = N.parse_loyer("1200 DH", contexte="Location par nuit, ideal vacances")
    assert (valeur, motif) == (None, "courte_duree")
    assert N.parse_loyer("2500 DH", contexte="loyer a la semaine")[1] == "courte_duree"


def test_parse_loyer_illisible():
    assert N.parse_loyer(None) == (None, "illisible")
    assert N.parse_loyer("prix a debattre") == (None, "illisible")


# ------------------------------------------------------------------------ typologie

@pytest.mark.parametrize(
    "texte,pieces,chambres",
    [
        ("Studio meuble", 1, 0),
        ("Appartement S+2", 3, 2),
        ("F3 lumineux", 3, None),
        ("appartement 4 pieces", 4, None),
        ("2 chambres salon", None, 2),
    ],
)
def test_parse_pieces_et_chambres(texte, pieces, chambres):
    assert N.parse_pieces(texte) == pieces
    assert N.parse_chambres(texte) == chambres


def test_parse_pieces_hors_bornes():
    assert N.parse_pieces("appartement 15 pieces") is None


@pytest.mark.parametrize(
    "pieces,chambres,attendu",
    [
        (1, 0, "studio_T2"),
        (2, 1, "studio_T2"),
        (3, 2, "T3"),
        (5, 4, "T4+"),
        (None, 2, "T3"),      # deduction : 2 chambres + salon = 3 pieces
        (None, None, None),
    ],
)
def test_derive_typologie(pieces, chambres, attendu):
    assert N.derive_typologie(pieces, chambres) == attendu


# --------------------------------------------------------------- etage, equipements

@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("3eme etage avec ascenseur", 3),
        ("Etage : 5", 5),
        ("RDC surele", 0),
        ("rez-de-chaussee", 0),
        ("dernier etage", None),
        ("etage 45", None),
    ],
)
def test_parse_etage(texte, attendu):
    assert N.parse_etage(texte) == attendu


def test_detect_meuble():
    assert N.detect_meuble("Appartement meuble et equipe") == 1
    assert N.detect_meuble("شقة مفروشة مفروش") == 1
    assert N.detect_meuble("furnished apartment") == 1
    assert N.detect_meuble("appartement non meuble") == 0
    assert N.detect_meuble("appartement vide") == 0
    assert N.detect_meuble("bel appartement") is None


def test_detect_equipements():
    assert N.detect_ascenseur("immeuble avec ascenseur") == 1
    assert N.detect_ascenseur("immeuble sans ascenseur") == 0
    assert N.detect_parking("place de parking en sous-sol") == 1
    assert N.detect_parking("sans parking") == 0
    assert N.detect_charges_incluses("6000 dh charges comprises") == 1
    assert N.detect_charges_incluses("6000 dh hors charges") == 0
    assert N.detect_charges_incluses("6000 dh") is None


# ------------------------------------------------------------------------ quartiers

def test_referentiel_charge(ref):
    noms = {q.nom for q in ref.quartiers}
    for attendu in ["Maârif", "Gauthier", "Racine", "Bourgogne", "CIL", "Oasis", "Anfa",
                    "Aïn Diab", "Sidi Maârouf", "Californie", "Aïn Sebaâ", "Hay Hassani",
                    "Derb Ghallef", "Belvédère", "Roches Noires", "Sidi Bernoussi",
                    "2 Mars", "Beauséjour"]:
        assert attendu in noms


@pytest.mark.parametrize(
    "brut,attendu",
    [
        ("Maârif", "Maârif"),
        ("maarif", "Maârif"),
        ("MAARIF EXTENSION", "Maârif"),
        ("Ain Diab", "Aïn Diab"),
        ("Bernoussi", "Sidi Bernoussi"),
        ("CIL", "CIL"),
        ("2 Mars", "2 Mars"),
        ("corniche", "Aïn Diab"),
    ],
)
def test_match_quartier_exact(ref, brut, attendu):
    m = ref.match(brut)
    assert m.nom == attendu
    assert m.methode == "exact"


def test_match_quartier_dans_une_phrase(ref):
    m = ref.match("Casablanca > Maarif, quartier Maarif")
    assert m.nom == "Maârif"
    assert m.methode in {"exact", "alias"}


def test_match_quartier_alias_le_plus_long_gagne(ref):
    """'sidi bernoussi' doit primer sur 'bernoussi' - meme quartier, mais on verifie
    que la regle du plus long alias ne se trompe pas de quartier."""
    assert ref.match("appartement a sidi bernoussi casablanca").nom == "Sidi Bernoussi"
    assert ref.match("appartement a sidi maarouf casablanca").nom == "Sidi Maârouf"


def test_match_quartier_fuzzy(ref):
    m = ref.match("Bourgone")          # faute de frappe frequente
    assert m.nom == "Bourgogne"
    assert m.methode in {"exact", "fuzzy"}


def test_match_quartier_ambiguite_ain_non_tranchee(ref):
    """Ain Diab / Ain Chock / Ain Sebaa sont proches : mieux vaut ne pas trancher."""
    m = ref.match("Ain")
    assert m.nom is None


def test_match_quartier_inconnu(ref):
    assert ref.match("Hay Zitoune Inconnu").nom is None
    assert ref.match(None).nom is None
    assert ref.match("").nom is None


def test_match_quartier_ne_confond_pas_les_proches(ref):
    assert ref.match("Ain Sebaa").nom == "Aïn Sebaâ"
    assert ref.match("Ain Chock").nom == "Aïn Chock"
    assert ref.match("Ain Diab").nom == "Aïn Diab"


def test_perimetre_peripherie(ref):
    assert ref.par_nom("Bouskoura").perimetre == "peripherie"
    assert ref.par_nom("Maârif").perimetre == "casablanca"


def test_log_unmatched_accumule(tmp_path):
    chemin = tmp_path / "unmatched_quartiers.csv"
    N.log_unmatched("Hay Zitoune", chemin)
    N.log_unmatched("Hay Zitoune", chemin)
    N.log_unmatched("Lotissement X", chemin)
    contenu = chemin.read_text(encoding="utf-8").splitlines()
    assert contenu[0] == "quartier_raw,occurrences"
    assert "Hay Zitoune,2" in contenu
    assert "Lotissement X,1" in contenu


# --------------------------------------------------------------------------- qualite

def test_score_qualite_3():
    assert N.score_qualite(80.0, "Maârif", 6000.0, "exact", "structure") == 3
    assert N.score_qualite(80.0, "Maârif", 6000.0, "alias", "structure") == 3


def test_score_qualite_2_si_fuzzy():
    assert N.score_qualite(80.0, "Bourgogne", 6000.0, "fuzzy", "structure") == 2


def test_score_qualite_1_si_surface_du_texte():
    assert N.score_qualite(80.0, "Maârif", 6000.0, "exact", "texte") == 1
    # les degradations se cumulent par le minimum
    assert N.score_qualite(80.0, "Maârif", 6000.0, "fuzzy", "texte") == 1


@pytest.mark.parametrize(
    "surface,quartier,loyer",
    [(None, "Maârif", 6000.0), (80.0, None, 6000.0), (80.0, "Maârif", None)],
)
def test_score_qualite_0_si_champ_manquant(surface, quartier, loyer):
    assert N.score_qualite(surface, quartier, loyer, "exact", "structure") == 0


# --------------------------------------------------------------------- orchestration

def test_normalize_listing_complet(ref):
    brut = {
        "url": "https://www.avito.ma/fr/maarif/appartements/exemple_1.htm",
        "source": "avito",
        "source_id": "1",
        "titre": "Appartement 85 m² meublé à Maârif",
        "description": "Bel appartement S+2, 3eme etage avec ascenseur et parking, "
                       "charges comprises. Contact 0612345678 ou agence@immo.ma",
        "quartier_raw": "Maarif, Casablanca",
        "surface_m2": 85.0,
        "loyer_mad": "9 000 DH",
    }
    ligne = N.normalize_listing(brut, ref)

    assert ligne["quartier_norm"] == "Maârif"
    assert ligne["surface_m2"] == 85.0
    assert ligne["surface_source"] == "structure"
    assert ligne["loyer_mad"] == 9000.0
    assert (ligne["nb_pieces"], ligne["nb_chambres"], ligne["typologie"]) == (3, 2, "T3")
    assert ligne["etage"] == 3
    assert (ligne["meuble"], ligne["ascenseur"], ligne["parking"]) == (1, 1, 1)
    assert ligne["charges_incluses"] == 1
    assert ligne["qualite"] == 3
    assert ligne["exclusion"] is None
    # aucune PII ne subsiste
    assert "0612345678" not in ligne["description"]
    assert "agence@immo.ma" not in ligne["description"]
    # aucune colonne personnelle
    assert not {"telephone", "nom", "email", "vendeur_url"} & set(ligne)


def test_normalize_listing_surface_du_texte_degrade_la_qualite(ref):
    ligne = N.normalize_listing(
        {
            "url": "u2",
            "titre": "Appartement 70 m2 a Gauthier",
            "description": "Lumineux, proche commerces.",
            "quartier_raw": "Gauthier",
            "loyer_mad": "8000",
        },
        ref,
    )
    assert (ligne["surface_m2"], ligne["surface_source"]) == (70.0, "texte")
    assert ligne["qualite"] == N.CAP_SURFACE_TEXTE


def test_normalize_listing_vente_exclue(ref):
    ligne = N.normalize_listing(
        {
            "url": "u3",
            "titre": "Appartement 90 m2 Anfa",
            "description": "Belle opportunite",
            "quartier_raw": "Anfa",
            "loyer_mad": "1 500 000 DH",
        },
        ref,
    )
    assert (ligne["loyer_mad"], ligne["exclusion"], ligne["qualite"]) == (None, "vente", 0)


def test_normalize_listing_ignore_les_champs_personnels(ref):
    ligne = N.normalize_listing(
        {"url": "u4", "titre": "Studio Racine", "telephone": "0612345678",
         "nom_vendeur": "Agence X", "quartier_raw": "Racine", "surface_m2": 30,
         "loyer_mad": "4000"},
        ref,
    )
    assert "telephone" not in ligne and "nom_vendeur" not in ligne
    assert ligne["typologie"] == "studio_T2"
