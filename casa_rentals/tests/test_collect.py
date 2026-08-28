"""Tests du parser Avito, de la boucle de collecte et du dedoublonnage.

Les fixtures HTML sont synthetiques : elles reproduisent la *forme* des trois sources
de donnees (payload Next.js, JSON-LD, DOM) pour valider la logique d'extraction.
Elles ne valident pas les selecteurs contre le vrai site — cela demande une page reelle,
d'ou l'etape `collect.py --probe`.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

import avito  # noqa: E402
import collect  # noqa: E402
import dedup  # noqa: E402
from db import charger_referentiel, init_db, sync_quartiers  # noqa: E402

AUJOURDHUI = date(2026, 8, 28)


# ------------------------------------------------------------------------------ fixtures

ANNONCES_BRUTES = [
    {
        "id": 101,
        "subject": "Appartement 85 m² meublé à Maârif",
        "friendlyUrl": "https://www.avito.ma/fr/maarif/appartements/Appartement_meuble_101.htm",
        "price": 9000,
        "description": "Bel appartement S+2, 3eme etage, ascenseur. Tel 0612345678",
        "area": {"name": "Maarif"},
        "listTime": "il y a 2 jours",
        "isShop": True,
        "params": [{"key": "surface", "value": "85"}, {"key": "rooms", "value": 3}],
    },
    {
        "id": 102,
        "subject": "Studio Gauthier",
        "friendlyUrl": "/fr/gauthier/appartements/Studio_lumineux_102.htm",
        "price": 4500,
        "description": "Studio 32 m2 non meuble, RDC.",
        "area": {"name": "Gauthier"},
        "listTime": "aujourd'hui",
        "isShop": False,
        "params": [{"key": "surface", "value": "32"}],
    },
]


def html_next_data(annonces=None) -> str:
    payload = {"props": {"pageProps": {"searchResult": {"ads": annonces or ANNONCES_BRUTES}}}}
    return (
        "<html><body><div id='__next'>rendu</div>"
        f"<script id='__NEXT_DATA__' type='application/json'>{json.dumps(payload)}</script>"
        "</body></html>"
    )


def html_jsonld() -> str:
    payload = {
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "Product",
                "name": "Appartement 70 m2 Racine",
                "url": "https://www.avito.ma/fr/racine/appartements/Appart_Racine_201.htm",
                "description": "Appartement 70 m2, 2 chambres, ascenseur.",
                "offers": {"@type": "Offer", "price": "7500", "priceCurrency": "MAD"},
            }
        ],
    }
    return f"<html><head><script type='application/ld+json'>{json.dumps(payload)}</script></head></html>"


def html_dom() -> str:
    return """
    <html><body>
      <div class="listing">
        <div class="card">
          <a href="/fr/bourgogne/appartements/Appart_Bourgogne_301.htm">Appartement 90 m2 Bourgogne</a>
          <span class="price">8 200 DH</span>
          <span class="time">il y a 3 jours</span>
        </div>
        <div class="card">
          <a href="https://www.avito.ma/fr/oasis/appartements/Appart_Oasis_302.htm">Appartement Oasis 110 m2</a>
          <span class="price">11 000 DH</span>
        </div>
        <a href="/fr/aide/contact">Contact</a>
      </div>
    </body></html>
    """


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    c = init_db(tmp_path / "test.db")
    sync_quartiers(c, RACINE / "quartiers_seed.csv")
    return c


@pytest.fixture()
def ref(conn):
    return charger_referentiel(conn)


@pytest.fixture(autouse=True)
def _unmatched_isole(tmp_path, monkeypatch):
    """Empeche les tests d'ecrire unmatched_quartiers.csv dans le depot."""
    monkeypatch.setattr(collect, "UNMATCHED_CSV", tmp_path / "unmatched.csv")


# -------------------------------------------------------------------------------- parser

def test_next_data_extrait_les_annonces():
    annonces = avito.strategie_next_data(html_next_data(), AUJOURDHUI)
    assert len(annonces) == 2
    a = next(x for x in annonces if x["source_id"] == "101")
    assert a["titre"] == "Appartement 85 m² meublé à Maârif"
    assert a["loyer_mad"] == 9000.0
    assert a["surface_m2"] == 85.0
    assert a["nb_pieces"] == 3
    assert a["quartier_raw"] == "Maarif"
    assert a["is_pro"] == 1
    assert a["date_publication"] == "2026-08-26"


def test_next_data_normalise_les_urls_relatives():
    annonces = avito.strategie_next_data(html_next_data(), AUJOURDHUI)
    b = next(x for x in annonces if x["source_id"] == "102")
    assert b["url"].startswith("https://www.avito.ma/fr/gauthier/")
    assert b["is_pro"] == 0
    assert b["date_publication"] == "2026-08-28"


def test_jsonld():
    annonces = avito.strategie_jsonld(html_jsonld(), AUJOURDHUI)
    assert len(annonces) == 1
    assert annonces[0]["loyer_mad"] == 7500.0
    assert annonces[0]["source_id"] == "201"


def test_dom_ignore_les_liens_non_annonces():
    annonces = avito.strategie_dom(html_dom(), AUJOURDHUI)
    urls = {a["source_id"] for a in annonces}
    assert urls == {"301", "302"}
    a = next(x for x in annonces if x["source_id"] == "301")
    assert a["loyer_mad"] == 8200.0
    assert a["date_publication"] == "2026-08-25"


def test_ordre_des_strategies():
    """Next.js prime sur le DOM quand les deux sont disponibles."""
    _, strategie = avito.parse_page_resultats(html_next_data() + html_dom(), AUJOURDHUI)
    assert strategie == "next_data"


def test_page_illisible_ne_renvoie_rien():
    """Une page qu'on ne sait pas lire doit se voir, pas passer pour une page vide."""
    annonces, strategie = avito.parse_page_resultats("<html><body>bloque</body></html>")
    assert (annonces, strategie) == ([], None)


def test_diagnostic():
    rapport = avito.diagnostic(html_next_data(), AUJOURDHUI)
    assert rapport["next_data"] == 2 and rapport["jsonld"] == 0


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("il y a 2 jours", "2026-08-26"),
        ("il y a 1 semaine", "2026-08-21"),
        ("Aujourd'hui 14:30", "2026-08-28"),
        ("hier", "2026-08-27"),
        ("2026-07-15", "2026-07-15"),
        ("bientot", None),
        (None, None),
    ],
)
def test_parse_date_publication(texte, attendu):
    assert avito.parse_date_publication(texte, AUJOURDHUI) == attendu


def test_est_url_annonce():
    assert avito.est_url_annonce("https://www.avito.ma/fr/maarif/appartements/Appart_101.htm")
    assert not avito.est_url_annonce("https://www.avito.ma/fr/aide/contact")
    assert not avito.est_url_annonce("")


# --------------------------------------------------------------------------- reseau

def _transport(reponses: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        for motif, reponse in reponses.items():
            if motif in str(request.url):
                return reponse
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_client_arrete_sur_403():
    transport = _transport({
        "robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /"),
        "casablanca": httpx.Response(403, text="forbidden"),
    })
    with collect.Client(delai=(0, 0), transport=transport) as client:
        with pytest.raises(collect.CollecteArretee, match="403"):
            client.get("https://www.avito.ma/fr/casablanca/appartements")


def test_client_arrete_sur_429():
    transport = _transport({
        "robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /"),
        "casablanca": httpx.Response(429, text="slow down"),
    })
    with collect.Client(delai=(0, 0), transport=transport) as client:
        with pytest.raises(collect.CollecteArretee, match="429"):
            client.get("https://www.avito.ma/fr/casablanca/appartements")


def test_client_respecte_robots_txt():
    transport = _transport({
        "robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /fr/casablanca"),
        "casablanca": httpx.Response(200, text="<html></html>"),
    })
    with collect.Client(delai=(0, 0), transport=transport) as client:
        with pytest.raises(collect.CollecteArretee, match="robots.txt"):
            client.get("https://www.avito.ma/fr/casablanca/appartements")


def test_client_attend_entre_deux_requetes(monkeypatch):
    """Le debit est limite : une pause est demandee avant chaque requete suivante."""
    pauses: list[float] = []
    monkeypatch.setattr(collect.time, "sleep", pauses.append)
    monkeypatch.setattr(collect.random, "uniform", lambda a, b: b)

    transport = _transport({
        "robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /"),
        "avito.ma": httpx.Response(200, text="<html></html>"),
    })
    with collect.Client(delai=(3.0, 5.0), transport=transport) as client:
        client.get("https://www.avito.ma/fr/casablanca/appartements?o=1")
        client.get("https://www.avito.ma/fr/casablanca/appartements?o=2")

    assert pauses, "aucune pause entre les requetes"
    assert all(p <= 5.0 for p in pauses)
    assert max(pauses) > 2.9


def test_delai_minimal_non_contournable():
    """Le garde-fou est dans l'interface : --delai-min < 3 est refuse."""
    with pytest.raises(SystemExit):
        sys.argv = ["collect.py", "--delai-min", "0.1"]
        collect.main()


def test_user_agent_honnete():
    assert "casa-rentals-research" in collect.UA
    assert "Mozilla" not in collect.UA          # pas de deguisement en navigateur


# ------------------------------------------------------------------------ ecriture en base

def _ligne(ref, url="https://www.avito.ma/fr/maarif/appartements/A_1.htm", **kw):
    from normalize import normalize_listing
    brute = {
        "url": url, "source": "avito", "titre": "Appartement 85 m2 Maarif",
        "description": "Bel appartement S+2, ascenseur.", "quartier_raw": "Maarif",
        "surface_m2": 85.0, "loyer_mad": 9000, **kw,
    }
    return normalize_listing(brute, ref)


def test_enregistrer_annonce_insere_puis_met_a_jour(conn, ref):
    assert collect.enregistrer_annonce(conn, _ligne(ref), "2026-08-28", 0) == "neuve"
    assert collect.enregistrer_annonce(conn, _ligne(ref), "2026-08-28", 0) == "revue"
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 1
    # le UNIQUE(listing_id, date_vue) absorbe la relance du meme jour
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_snapshot_par_jour_et_changement_de_prix(conn, ref):
    collect.enregistrer_annonce(conn, _ligne(ref), "2026-08-28", 0)
    collect.enregistrer_annonce(conn, _ligne(ref, loyer_mad=8500), "2026-08-29", 2)

    snaps = conn.execute("SELECT date_vue, loyer_mad, position FROM snapshots "
                         "ORDER BY date_vue").fetchall()
    assert [(s["date_vue"], s["loyer_mad"], s["position"]) for s in snaps] == [
        ("2026-08-28", 9000.0, 0), ("2026-08-29", 8500.0, 2)
    ]
    ligne = conn.execute("SELECT first_seen, last_seen, loyer_mad FROM listings").fetchone()
    assert ligne["first_seen"] == "2026-08-28"     # jamais reecrit
    assert ligne["last_seen"] == "2026-08-29"
    assert ligne["loyer_mad"] == 8500.0            # le listing porte le prix courant


def test_marquer_disparues(conn, ref):
    collect.enregistrer_annonce(conn, _ligne(ref, url="u_ancienne"), "2026-08-10", 0)
    collect.enregistrer_annonce(conn, _ligne(ref, url="u_recente"), "2026-08-27", 1)

    assert collect.marquer_disparues(conn, AUJOURDHUI) == 1
    statuts = dict(conn.execute("SELECT url, statut FROM listings").fetchall())
    assert statuts["u_ancienne"] == "disparue"
    assert statuts["u_recente"] == "active"


def test_annonce_revue_redevient_active(conn, ref):
    collect.enregistrer_annonce(conn, _ligne(ref, url="u"), "2026-08-10", 0)
    collect.marquer_disparues(conn, AUJOURDHUI)
    collect.enregistrer_annonce(conn, _ligne(ref, url="u"), "2026-08-28", 0)
    assert conn.execute("SELECT statut FROM listings").fetchone()["statut"] == "active"


def test_vue_recemment(conn, ref):
    collect.enregistrer_annonce(conn, _ligne(ref, url="u_vieille"), "2026-08-20", 0)
    collect.enregistrer_annonce(conn, _ligne(ref, url="u_hier"), "2026-08-27", 1)
    assert collect._vue_recemment(conn, "u_vieille", AUJOURDHUI) is True
    assert collect._vue_recemment(conn, "u_hier", AUJOURDHUI) is False
    assert collect._vue_recemment(conn, "u_inconnue", AUJOURDHUI) is False


def test_journaliser_run(conn):
    collect.journaliser_run(conn, "2026-08-28T09:00:00+00:00", "avito", 5, 120, 30, 0, "ok")
    ligne = conn.execute("SELECT * FROM runs").fetchone()
    assert (ligne["pages_vues"], ligne["annonces_vues"], ligne["annonces_neuves"]) == (5, 120, 30)


# --------------------------------------------------------------------- traitement de page

def test_traiter_page_de_bout_en_bout(conn, ref):
    bilan = collect.traiter_page(conn, html_next_data(), ref, "avito", "2026-08-28", AUJOURDHUI)
    conn.commit()

    assert bilan["annonces"] == 2 and bilan["neuves"] == 2 and bilan["erreurs"] == 0
    lignes = conn.execute("SELECT * FROM listings ORDER BY source_id").fetchall()
    assert [l["quartier_norm"] for l in lignes] == ["Maârif", "Gauthier"]
    assert lignes[0]["qualite"] == 3
    assert lignes[0]["meuble"] == 1 and lignes[1]["meuble"] == 0
    # aucune PII stockee
    assert "0612345678" not in (lignes[0]["description"] or "")


def test_traiter_page_est_idempotent(conn, ref):
    for _ in range(2):
        collect.traiter_page(conn, html_next_data(), ref, "avito", "2026-08-28", AUJOURDHUI)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2


def test_traiter_page_dry_run_n_ecrit_rien(conn, ref):
    bilan = collect.traiter_page(conn, html_next_data(), ref, "avito", "2026-08-28",
                                 AUJOURDHUI, dry_run=True)
    assert bilan["annonces"] == 2
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0


def test_quartier_inconnu_va_dans_unmatched(conn, ref, tmp_path, monkeypatch):
    chemin = tmp_path / "unmatched.csv"
    monkeypatch.setattr(collect, "UNMATCHED_CSV", chemin)
    annonces = [dict(ANNONCES_BRUTES[0], area={"name": "Hay Zitoune Inconnu"})]
    collect.traiter_page(conn, html_next_data(annonces), ref, "avito", "2026-08-28", AUJOURDHUI)
    assert "Hay Zitoune Inconnu" in chemin.read_text(encoding="utf-8")


def test_collecte_s_arrete_sur_refus_du_site(conn, ref):
    transport = _transport({
        "robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /"),
        "casablanca": httpx.Response(429, text="slow down"),
    })
    with collect.Client(delai=(0, 0), transport=transport) as client:
        total = collect.collecter(conn, client, ref, pages=5,
                                  gabarit=avito.RECHERCHE_CANDIDATES[0])
    assert total["pages"] == 0 and "429" in total["note"]
    # l'arret est journalise, pas avale
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_collecte_journalise_un_parser_casse(conn, ref):
    transport = _transport({
        "robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /"),
        "casablanca": httpx.Response(200, text="<html>page inattendue</html>"),
    })
    with collect.Client(delai=(0, 0), transport=transport) as client:
        total = collect.collecter(conn, client, ref, pages=3,
                                  gabarit=avito.RECHERCHE_CANDIDATES[0])
    assert total["vues"] == 0
    assert "recalibrer" in total["note"]


def test_collecte_nominale(conn, ref):
    transport = _transport({
        "robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /"),
        "casablanca": httpx.Response(200, text=html_next_data()),
    })
    with collect.Client(delai=(0, 0), transport=transport) as client:
        total = collect.collecter(conn, client, ref, pages=2,
                                  gabarit=avito.RECHERCHE_CANDIDATES[0])
    assert total["pages"] == 2 and total["neuves"] == 2   # memes annonces sur les 2 pages
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 2


# ------------------------------------------------------------------------- dedoublonnage

def _inserer(conn, ref, url, first_seen, **kw):
    ligne = _ligne(ref, url=url, **kw)
    collect.enregistrer_annonce(conn, ligne, first_seen, 0)
    conn.commit()


def test_dedup_marque_la_republication(conn, ref):
    desc = "Bel appartement lumineux avec ascenseur, proche commerces et ecoles."
    _inserer(conn, ref, "u1", "2026-08-01", description=desc, surface_m2=85, loyer_mad=9000)
    _inserer(conn, ref, "u2", "2026-08-20", description=desc, surface_m2=87, loyer_mad=9200)

    paires = dedup.marquer_doublons(conn)
    assert len(paires) == 1
    lignes = dict(conn.execute("SELECT url, duplicate_of FROM listings").fetchall())
    assert lignes["u1"] is None                # la plus ancienne reste l'originale
    assert lignes["u2"] is not None


def test_dedup_epargne_les_biens_differents(conn, ref):
    _inserer(conn, ref, "u1", "2026-08-01", description="Appartement avec vue sur mer.",
             surface_m2=85, loyer_mad=9000)
    _inserer(conn, ref, "u2", "2026-08-02", description="Local commercial en rez de chaussee.",
             surface_m2=85, loyer_mad=9000)
    assert dedup.marquer_doublons(conn) == []


@pytest.mark.parametrize(
    "surface,loyer,attendu",
    [
        (87.0, 9200.0, True),      # dans les tolerances
        (90.0, 9000.0, False),     # surface a +5 m2
        (85.0, 9900.0, False),     # loyer a +10 %
    ],
)
def test_dedup_tolerances(conn, ref, surface, loyer, attendu):
    desc = "Bel appartement lumineux avec ascenseur, proche commerces et ecoles."
    _inserer(conn, ref, "u1", "2026-08-01", description=desc, surface_m2=85, loyer_mad=9000)
    _inserer(conn, ref, "u2", "2026-08-20", description=desc, surface_m2=surface,
             loyer_mad=loyer)
    assert bool(dedup.marquer_doublons(conn)) is attendu


def test_dedup_ne_conclut_pas_sans_surface(conn, ref):
    """Un champ manquant n'autorise pas le rattachement."""
    desc = "Bel appartement lumineux avec ascenseur, proche commerces et ecoles."
    _inserer(conn, ref, "u1", "2026-08-01", description=desc, surface_m2=None, loyer_mad=9000,
             titre="Appartement Maarif")
    _inserer(conn, ref, "u2", "2026-08-20", description=desc, surface_m2=None, loyer_mad=9000,
             titre="Appartement Maarif")
    assert dedup.marquer_doublons(conn) == []


def test_dedup_est_idempotent(conn, ref):
    desc = "Bel appartement lumineux avec ascenseur, proche commerces et ecoles."
    _inserer(conn, ref, "u1", "2026-08-01", description=desc, surface_m2=85, loyer_mad=9000)
    _inserer(conn, ref, "u2", "2026-08-20", description=desc, surface_m2=85, loyer_mad=9000)
    assert len(dedup.marquer_doublons(conn)) == 1
    assert dedup.marquer_doublons(conn) == []      # deja marque, plus rien a faire
