"""Normalisation des annonces de location residentielle (Casablanca).

Toutes les fonctions sont pures et testables unitairement : elles prennent du texte
brut et renvoient soit une valeur normalisee, soit None. Aucune ne fait d'I/O reseau.

Deux invariants tiennent tout le module :

1. Aucune donnee personnelle ne sort d'ici. `scrub_pii` est appliquee a tout texte
   libre avant stockage, et le hash de contenu est calcule sur le texte expurge.
2. Un champ qu'on n'a pas su lire vaut None, jamais une valeur devinee. Le score
   `qualite` porte la tracabilite de ce qui a ete lu directement et de ce qui a ete
   extrait au jugement.
"""

from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

# --------------------------------------------------------------------------------------
# Bornes de validite (cf. METHODO.md, regles d'exclusion)
# --------------------------------------------------------------------------------------

SURFACE_MIN, SURFACE_MAX = 15.0, 500.0
LOYER_MIN, LOYER_MAX = 1_000.0, 100_000.0
PIECES_MIN, PIECES_MAX = 1, 8
CHAMBRES_MIN, CHAMBRES_MAX = 0, 7
ETAGE_MAX = 30

# Plafonds du score qualite. Modifiables d'un seul endroit : la regle "surface extraite
# du texte -> 1" est severe et peut ecarter une grosse part de l'echantillon si Avito
# n'expose pas de champ surface structure. A trancher sur donnees reelles (cf. README).
CAP_QUARTIER_FUZZY = 2
CAP_SURFACE_TEXTE = 1

# Seuils du matching fuzzy de quartier.
FUZZY_SEUIL = 88            # score minimal rapidfuzz (0-100)
FUZZY_MARGE = 4             # ecart minimal avec le meilleur score d'un AUTRE quartier
FUZZY_LONGUEUR_MIN = 5      # on ne fuzzy-matche pas un alias court ("cil", "polo")


# --------------------------------------------------------------------------------------
# Texte
# --------------------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_PONCT_RE = re.compile(r"[^\w\s؀-ۿ]+")


def strip_accents(texte: str) -> str:
    """Retire les diacritiques latins. Laisse l'arabe intact."""
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def flatten_text(texte: str | None) -> str:
    """Minuscule, sans accents, espaces normalises, mais ponctuation preservee.

    Necessaire partout ou la ponctuation porte du sens : `S+2`, `m²`, `3eme etage`.
    """
    if not texte:
        return ""
    return _WS_RE.sub(" ", strip_accents(texte).lower()).strip()


def normalize_text(texte: str | None) -> str:
    """Minuscule, sans accents, sans ponctuation, espaces normalises.

    Sert de base a la comparaison de chaines (quartiers, hash de contenu, mots-cles).
    """
    if not texte:
        return ""
    texte = texte.replace("’", " ").replace("'", " ").replace("_", " ")
    texte = strip_accents(texte).lower()
    texte = _PONCT_RE.sub(" ", texte)
    return _WS_RE.sub(" ", texte).strip()


# --------------------------------------------------------------------------------------
# Expurgation des donnees personnelles
# --------------------------------------------------------------------------------------

# Numeros marocains : 06xxxxxxxx / 07 / 05, +212 6..., avec separateurs varies.
_TEL_RE = re.compile(
    r"(?:(?:\+|00)\s?212[\s.\-/]?|\b0)\s?[5-7](?:[\s.\-/]?\d){8}\b"
)
# Suites de 9 a 12 chiffres separes, qui ne peuvent pas etre un loyer ni une surface.
_TEL_LACHE_RE = re.compile(r"\b\d{2}(?:[\s.\-]?\d{2}){4,5}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+|\bwa\.me/\S+|\bt\.me/\S+")


def scrub_pii(texte: str | None) -> str:
    """Remplace telephones, emails et URLs par des marqueurs.

    Appelee avant tout stockage de texte libre. Le remplacement (plutot que la
    suppression) garde la phrase lisible et rend l'expurgation verifiable a l'oeil.
    """
    if not texte:
        return ""
    texte = _EMAIL_RE.sub("[email]", texte)
    texte = _URL_RE.sub("[url]", texte)
    texte = _TEL_RE.sub("[tel]", texte)
    texte = _TEL_LACHE_RE.sub("[tel]", texte)
    return _WS_RE.sub(" ", texte).strip()


def content_hash(titre: str | None, description: str | None) -> str:
    """Hash stable du couple titre + description, calcule sur le texte expurge."""
    base = f"{normalize_text(scrub_pii(titre))}|{normalize_text(scrub_pii(description))}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# Surface
# --------------------------------------------------------------------------------------

_NOMBRE = r"(\d{1,4}(?:[.,]\d{1,2})?)"
# Unites sans ambiguite : le nombre qui precede est une surface.
_SURFACE_FORTE_RE = re.compile(
    _NOMBRE + r"\s*(?:m\s*[²2]|m\s*carre|metres?\s*carres?|metre\s*carre|متر\s*مربع)",
    re.IGNORECASE,
)
# "75 metres" seul : ambigu ("a 200 metres de la plage"). Accepte sous conditions.
_SURFACE_FAIBLE_RE = re.compile(_NOMBRE + r"\s*(?:metres?|m)\b", re.IGNORECASE)
_DISTANCE_AVANT_RE = re.compile(r"\b(?:a|de|environ|dans|situe[e]?\s+a)\s*$", re.IGNORECASE)
_DISTANCE_APRES_RE = re.compile(r"^\s*(?:de|du|des|d\s|a\s)", re.IGNORECASE)


def _to_float(brut: str) -> float | None:
    try:
        return float(brut.replace(",", "."))
    except ValueError:
        return None


def surface_valide(valeur: float | None) -> float | None:
    """Applique les bornes [15, 500] m2."""
    if valeur is None:
        return None
    return valeur if SURFACE_MIN <= valeur <= SURFACE_MAX else None


def parse_surface(texte: str | None) -> float | None:
    """Extrait une surface en m2 depuis du texte libre (titre ou description).

    Retourne la PREMIERE valeur plausible : dans un titre ou une description
    d'annonce, la surface du bien est enoncee avant celle de la terrasse ou du salon.
    Les valeurs hors [15, 500] sont ignorees (et non pas ramenees aux bornes).
    """
    if not texte:
        return None
    plat = flatten_text(texte)

    for m in _SURFACE_FORTE_RE.finditer(plat):
        valeur = surface_valide(_to_float(m.group(1)))
        if valeur is not None:
            return valeur

    # Repli sur l'unite ambigue, en ecartant les tournures de distance.
    for m in _SURFACE_FAIBLE_RE.finditer(plat):
        avant = plat[max(0, m.start() - 20): m.start()]
        apres = plat[m.end(): m.end() + 6]
        if _DISTANCE_AVANT_RE.search(avant) or _DISTANCE_APRES_RE.match(apres):
            continue
        valeur = surface_valide(_to_float(m.group(1)))
        if valeur is not None:
            return valeur
    return None


# --------------------------------------------------------------------------------------
# Loyer
# --------------------------------------------------------------------------------------

_PRIX_RE = re.compile(r"(\d[\d\s.,  ']*\d|\d)")
_PERIODICITE_COURTE_RE = re.compile(
    r"\b(?:par\s+)?(?:jour|jours|nuit|nuitee|nuits|journalier|journee|semaine|hebdomadaire|weekend|week end)\b"
    r"|/\s*(?:j|jour|nuit|semaine)\b",
    re.IGNORECASE,
)
_VENTE_RE = re.compile(r"\b(?:a\s+vendre|prix\s+de\s+vente|vente|vend\b|cession)\b", re.IGNORECASE)


def parse_loyer(brut: str | float | int | None, contexte: str | None = None) -> tuple[float | None, str | None]:
    """Parse un loyer mensuel en MAD.

    Retourne (valeur, motif_rejet). `motif_rejet` vaut None quand la valeur est
    exploitable, sinon il documente l'exclusion pour le journal de collecte :
      - 'illisible'      : aucun nombre exploitable
      - 'sous_plancher'  : < 1 000 MAD (prix partiel, "a partir de", erreur de saisie)
      - 'vente'          : > 100 000 MAD ou vocabulaire de vente -> mauvaise categorie
      - 'courte_duree'   : prix a la nuit / a la semaine, hors champ de l'etude

    `contexte` (titre + description) sert uniquement a detecter vente et courte duree.
    """
    if brut is None or (isinstance(brut, str) and not brut.strip()):
        return None, "illisible"

    ctx = normalize_text(f"{brut if isinstance(brut, str) else ''} {contexte or ''}")

    if isinstance(brut, (int, float)):
        valeur: float | None = float(brut)
    else:
        m = _PRIX_RE.search(brut)
        if not m:
            return None, "illisible"
        # En MA les milliers s'ecrivent avec espace, point ou apostrophe : on retire tout.
        valeur = _to_float(re.sub(r"[\s.,  ']", "", m.group(1)))

    if valeur is None:
        return None, "illisible"

    if _PERIODICITE_COURTE_RE.search(ctx):
        return None, "courte_duree"
    if valeur > LOYER_MAX:
        return None, "vente"
    if _VENTE_RE.search(ctx):
        return None, "vente"
    if valeur < LOYER_MIN:
        return None, "sous_plancher"
    return valeur, None


# --------------------------------------------------------------------------------------
# Typologie
# --------------------------------------------------------------------------------------

_STUDIO_RE = re.compile(r"\bstudio\b|\bستوديو\b", re.IGNORECASE)
_SPLUS_RE = re.compile(r"\bs\s*\+\s*(\d)\b", re.IGNORECASE)         # S+2 = salon + 2 chambres
_FT_RE = re.compile(r"\b[ft]\s*(\d)\b", re.IGNORECASE)              # F3 / T3 = 3 pieces
_PIECES_RE = re.compile(r"(\d{1,2})\s*(?:pieces?|pces?|pcs)\b", re.IGNORECASE)
_CHAMBRES_RE = re.compile(
    r"(\d{1,2})\s*(?:chambres?|chbre?s?|chb|ch\b|غرف|غرفة)", re.IGNORECASE
)


def _borne(valeur: int | None, mini: int, maxi: int) -> int | None:
    if valeur is None:
        return None
    return valeur if mini <= valeur <= maxi else None


def parse_chambres(texte: str | None) -> int | None:
    """Nombre de chambres. `studio` -> 0, `S+2` -> 2."""
    if not texte:
        return None
    plat = flatten_text(texte)
    m = _CHAMBRES_RE.search(plat)
    if m:
        return _borne(int(m.group(1)), CHAMBRES_MIN, CHAMBRES_MAX)
    m = _SPLUS_RE.search(plat)
    if m:
        return _borne(int(m.group(1)), CHAMBRES_MIN, CHAMBRES_MAX)
    if _STUDIO_RE.search(plat):
        return 0
    return None


def parse_pieces(texte: str | None) -> int | None:
    """Nombre de pieces, salon compris (convention marocaine : S+2 = 3 pieces)."""
    if not texte:
        return None
    plat = flatten_text(texte)
    if _STUDIO_RE.search(plat):
        return 1
    m = _PIECES_RE.search(plat)
    if m:
        return _borne(int(m.group(1)), PIECES_MIN, PIECES_MAX)
    m = _SPLUS_RE.search(plat)
    if m:
        return _borne(int(m.group(1)) + 1, PIECES_MIN, PIECES_MAX)
    m = _FT_RE.search(plat)
    if m:
        return _borne(int(m.group(1)), PIECES_MIN, PIECES_MAX)
    return None


def derive_typologie(nb_pieces: int | None, nb_chambres: int | None) -> str | None:
    """Cellule d'analyse : 'studio_T2' | 'T3' | 'T4+'.

    A defaut de nombre de pieces, on le deduit des chambres (+1 pour le salon).
    La deduction est signalee par le champ `nb_pieces_derive` de `normalize_listing`.
    """
    pieces = nb_pieces
    if pieces is None and nb_chambres is not None:
        pieces = nb_chambres + 1
    if pieces is None:
        return None
    if pieces <= 2:
        return "studio_T2"
    if pieces == 3:
        return "T3"
    return "T4+"


# --------------------------------------------------------------------------------------
# Etage et equipements
# --------------------------------------------------------------------------------------

_RDC_RE = re.compile(r"\b(?:rdc|r\.d\.c|rez[\s\-]*de[\s\-]*chaussee)\b", re.IGNORECASE)
_ETAGE_RE = re.compile(
    r"(?:(\d{1,2})\s*(?:er|ere|eme|e)?\s*etage)|(?:etage\s*(?:n\s*)?:?\s*(\d{1,2}))",
    re.IGNORECASE,
)


def parse_etage(texte: str | None) -> int | None:
    """Etage. RDC -> 0. 'dernier etage' -> None (non chiffrable)."""
    if not texte:
        return None
    plat = flatten_text(texte)
    m = _ETAGE_RE.search(plat)
    if m:
        brut = m.group(1) or m.group(2)
        return _borne(int(brut), 0, ETAGE_MAX)
    if _RDC_RE.search(plat):
        return 0
    return None


def _detecte(texte: str | None, positifs: tuple[str, ...], negatifs: tuple[str, ...]) -> int | None:
    """Detection booleenne par mots-cles : negatifs d'abord, puis positifs, sinon None."""
    if not texte:
        return None
    plat = normalize_text(texte)
    arabe = texte  # les motifs arabes ne survivent pas a la normalisation latine
    for motif in negatifs:
        if motif in plat or motif in arabe:
            return 0
    for motif in positifs:
        if motif in plat or motif in arabe:
            return 1
    return None


def detect_meuble(texte: str | None) -> int | None:
    return _detecte(
        texte,
        positifs=("meuble", "meublee", "furnished", "مفروش", "equipee et meublee"),
        negatifs=("non meuble", "non meublee", "sans meuble", "vide", "non equipe",
                  "unfurnished", "not furnished", "غير مفروش"),
    )


def detect_ascenseur(texte: str | None) -> int | None:
    return _detecte(
        texte,
        positifs=("ascenseur", "elevator", "مصعد"),
        negatifs=("sans ascenseur", "pas d ascenseur", "no elevator", "بدون مصعد"),
    )


def detect_parking(texte: str | None) -> int | None:
    return _detecte(
        texte,
        positifs=("parking", "garage", "place de voiture", "موقف", "كراج"),
        negatifs=("sans parking", "pas de parking", "sans garage", "بدون موقف"),
    )


def detect_charges_incluses(texte: str | None) -> int | None:
    return _detecte(
        texte,
        positifs=("charges comprises", "charges incluses", "charges inclus", "cc inclus",
                  "syndic inclus", "toutes charges comprises"),
        negatifs=("charges non comprises", "hors charges", "charges en sus",
                  "charges non incluses", "sans les charges"),
    )


# --------------------------------------------------------------------------------------
# Quartiers
# --------------------------------------------------------------------------------------

_STOPWORDS_LIEU = {
    "casablanca", "casa", "maroc", "morocco", "quartier", "secteur", "a", "louer",
    "location", "appartement", "appart", "studio", "villa", "duplex", "residence",
    "immeuble", "grand", "belle", "beau", "dans", "au", "aux", "le", "la", "les", "de",
    "du", "des", "sur", "et", "pour", "avec",
}


@dataclass(frozen=True)
class Quartier:
    nom: str
    aliases: tuple[str, ...]
    arrondissement: str | None = None
    segment: str | None = None
    perimetre: str = "casablanca"


@dataclass(frozen=True)
class QuartierMatch:
    nom: str | None
    methode: str | None       # 'exact' | 'alias' | 'fuzzy' | None
    score: float | None = None


@dataclass
class Referentiel:
    """Referentiel des quartiers, charge une fois puis interroge en memoire."""

    quartiers: list[Quartier] = field(default_factory=list)
    _index: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._index = {}
        for q in self.quartiers:
            for cle in (normalize_text(q.nom), *(normalize_text(a) for a in q.aliases)):
                if cle:
                    self._index.setdefault(cle, q.nom)

    @classmethod
    def depuis_csv(cls, chemin: str | Path) -> "Referentiel":
        quartiers: list[Quartier] = []
        with open(chemin, encoding="utf-8", newline="") as f:
            for ligne in csv.DictReader(f):
                aliases = tuple(
                    a.strip() for a in (ligne.get("aliases") or "").split("|") if a.strip()
                )
                quartiers.append(
                    Quartier(
                        nom=ligne["nom"].strip(),
                        aliases=aliases,
                        arrondissement=(ligne.get("arrondissement") or "").strip() or None,
                        segment=(ligne.get("segment") or "").strip() or None,
                        perimetre=(ligne.get("perimetre") or "casablanca").strip(),
                    )
                )
        return cls(quartiers)

    def par_nom(self, nom: str) -> Quartier | None:
        return next((q for q in self.quartiers if q.nom == nom), None)

    def match(self, brut: str | None) -> QuartierMatch:
        """Rattache un libelle de lieu brut a un quartier du referentiel.

        Trois passes, de la plus sure a la plus risquee :
          1. 'exact'  : le libelle (ou un de ses segments) est exactement un alias.
          2. 'alias'  : un alias apparait comme mot entier dans le libelle
                        ("Appartement a Maarif, Casablanca"). Le plus long gagne.
          3. 'fuzzy'  : rapidfuzz, uniquement pour les alias d'au moins 5 caracteres,
                        avec un seuil ET une marge sur le meilleur autre quartier.
                        La marge evite les confusions Ain Diab / Ain Chock / Ain Sebaa.
        """
        if not brut:
            return QuartierMatch(None, None)

        plat = normalize_text(brut)
        if not plat:
            return QuartierMatch(None, None)

        # 1. exact, sur la chaine entiere puis sur chaque segment separe.
        if plat in self._index:
            return QuartierMatch(self._index[plat], "exact", 100.0)
        segments = [normalize_text(s) for s in re.split(r"[,>/|\-–—]", brut)]
        for seg in segments:
            if seg and seg in self._index:
                return QuartierMatch(self._index[seg], "exact", 100.0)

        # 2. alias present comme mot entier ; on prend le plus long (specifique > generique).
        meilleur_alias: tuple[int, str] | None = None
        for cle, nom in self._index.items():
            if len(cle) < 3:
                continue
            if re.search(rf"(?<!\w){re.escape(cle)}(?!\w)", plat):
                if meilleur_alias is None or len(cle) > meilleur_alias[0]:
                    meilleur_alias = (len(cle), nom)
        if meilleur_alias:
            return QuartierMatch(meilleur_alias[1], "alias", 100.0)

        # 3. fuzzy sur le libelle debarrasse de ses mots vides.
        noyau = " ".join(t for t in plat.split() if t not in _STOPWORDS_LIEU)
        if not noyau:
            return QuartierMatch(None, None)

        scores: dict[str, float] = {}
        for cle, nom in self._index.items():
            if len(cle) < FUZZY_LONGUEUR_MIN:
                continue
            score = fuzz.token_sort_ratio(noyau, cle)
            if score > scores.get(nom, 0):
                scores[nom] = score
        if not scores:
            return QuartierMatch(None, None)

        classement = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        nom, score = classement[0]
        if score < FUZZY_SEUIL:
            return QuartierMatch(None, None)
        if len(classement) > 1 and score - classement[1][1] < FUZZY_MARGE:
            return QuartierMatch(None, None)  # ambigu : on prefere ne pas trancher
        return QuartierMatch(nom, "fuzzy", float(score))


def log_unmatched(brut: str, chemin: str | Path = "unmatched_quartiers.csv") -> None:
    """Accumule les libelles de lieu non rattaches, avec leur frequence.

    Fichier a reviser a la main : chaque ligne devient soit un alias d'un quartier
    existant, soit un nouveau quartier du referentiel.
    """
    chemin = Path(chemin)
    compteurs: dict[str, int] = {}
    if chemin.exists():
        with open(chemin, encoding="utf-8", newline="") as f:
            for ligne in csv.DictReader(f):
                compteurs[ligne["quartier_raw"]] = int(ligne["occurrences"])
    compteurs[brut] = compteurs.get(brut, 0) + 1
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["quartier_raw", "occurrences"])
        for cle, n in sorted(compteurs.items(), key=lambda kv: (-kv[1], kv[0])):
            w.writerow([cle, n])


# --------------------------------------------------------------------------------------
# Score de qualite
# --------------------------------------------------------------------------------------

def score_qualite(
    surface_m2: float | None,
    quartier_norm: str | None,
    loyer_mad: float | None,
    quartier_method: str | None = None,
    surface_source: str | None = None,
) -> int:
    """Score 0-3 de confiance dans la ligne. Toutes les analyses filtrent sur >= 2.

    3 : surface, quartier et loyer presents, quartier rattache exactement,
        surface lue dans un champ structure.
    2 : idem mais le quartier vient d'un fuzzy match.
    1 : idem mais la surface a ete extraite du texte libre.
    0 : au moins un des trois champs manque.

    Les degradations se cumulent par le minimum : un fuzzy match ET une surface
    extraite du texte donnent 1.
    """
    if surface_m2 is None or not quartier_norm or loyer_mad is None:
        return 0
    score = 3
    if quartier_method == "fuzzy":
        score = min(score, CAP_QUARTIER_FUZZY)
    if surface_source == "texte":
        score = min(score, CAP_SURFACE_TEXTE)
    return score


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def normalize_listing(brut: dict, referentiel: Referentiel) -> dict:
    """Transforme une annonce brute (sortie de parser) en ligne prete pour `listings`.

    Entree attendue (toutes les cles sont optionnelles sauf `url`) :
        url, source, source_id, titre, description, quartier_raw, date_publication,
        surface_m2 (champ structure), nb_pieces, nb_chambres, etage, loyer_mad,
        meuble, ascenseur, parking, is_pro

    Sortie : dict aux colonnes de `listings`, plus des cles de tracabilite
    (`nb_pieces_derive`, `typologie`) qui ne sont pas stockees telles quelles.
    Les champs personnels eventuellement presents en entree sont ignores.
    """
    titre = scrub_pii(brut.get("titre"))
    description = scrub_pii(brut.get("description"))
    texte = f"{titre} {description}"

    # Surface : champ structure d'abord, texte en repli.
    surface = surface_valide(brut.get("surface_m2"))
    surface_source = "structure" if surface is not None else None
    if surface is None:
        surface = parse_surface(texte)
        surface_source = "texte" if surface is not None else None

    loyer, motif_rejet = parse_loyer(brut.get("loyer_mad"), contexte=texte)

    m = referentiel.match(brut.get("quartier_raw") or titre)

    nb_pieces = brut.get("nb_pieces") if brut.get("nb_pieces") is not None else parse_pieces(texte)
    nb_pieces = _borne(nb_pieces, PIECES_MIN, PIECES_MAX)
    nb_chambres = (
        brut.get("nb_chambres") if brut.get("nb_chambres") is not None else parse_chambres(texte)
    )
    nb_chambres = _borne(nb_chambres, CHAMBRES_MIN, CHAMBRES_MAX)
    nb_pieces_derive = 0
    if nb_pieces is None and nb_chambres is not None:
        nb_pieces = _borne(nb_chambres + 1, PIECES_MIN, PIECES_MAX)
        nb_pieces_derive = 1

    etage = brut.get("etage") if brut.get("etage") is not None else parse_etage(texte)

    def _ou_detecte(cle: str, detecteur) -> int | None:
        valeur = brut.get(cle)
        return valeur if valeur is not None else detecteur(texte)

    return {
        "source": brut.get("source"),
        "source_id": brut.get("source_id"),
        "url": brut.get("url"),
        "content_hash": content_hash(titre, description),
        "titre": titre or None,
        "description": description or None,
        "quartier_raw": brut.get("quartier_raw"),
        "quartier_norm": m.nom,
        "quartier_method": m.methode,
        "surface_m2": surface,
        "surface_source": surface_source,
        "nb_pieces": nb_pieces,
        "nb_chambres": nb_chambres,
        "nb_pieces_derive": nb_pieces_derive,
        "typologie": derive_typologie(nb_pieces, nb_chambres),
        "etage": _borne(etage, 0, ETAGE_MAX),
        "meuble": _ou_detecte("meuble", detect_meuble),
        "ascenseur": _ou_detecte("ascenseur", detect_ascenseur),
        "parking": _ou_detecte("parking", detect_parking),
        "is_pro": brut.get("is_pro"),
        "loyer_mad": loyer,
        "charges_incluses": _ou_detecte("charges_incluses", detect_charges_incluses),
        "date_publication": brut.get("date_publication"),
        "exclusion": motif_rejet if motif_rejet in {"vente", "courte_duree"} else None,
        "qualite": score_qualite(surface, m.nom, loyer, m.methode, surface_source),
    }
