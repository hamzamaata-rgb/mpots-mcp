"""Parser Avito : extraction des annonces depuis le HTML d'une page de resultats.

Le parsing est pur (HTML -> liste de dicts bruts), sans I/O reseau : `collect.py`
s'occupe du telechargement, ce module ne fait que lire. C'est ce qui permet de le
reutiliser tel quel sur les captures Wayback (volet A).

Trois strategies sont essayees dans l'ordre, de la plus structuree a la plus fragile :

  1. `__NEXT_DATA__`  - Avito est une application Next.js : la page embarque en JSON
     l'integralite des donnees du rendu. C'est la source la plus fiable et la plus
     stable dans le temps.
  2. JSON-LD          - balises `application/ld+json` (ItemList / Product), souvent
     presentes pour le referencement.
  3. DOM              - repli sur les liens d'annonces et le texte des cartes.

Aucune strategie n'est privilegiee par des selecteurs devines : la 1 et la 2 explorent
l'arbre JSON a la recherche d'objets ayant la *forme* d'une annonce (une URL et un prix),
ce qui resiste aux renommages de chemins. La strategie 3 est explicitement un repli et
signale sa propre fragilite via le champ `_strategie`.

IMPORTANT : les selecteurs DOM et les URLs de recherche n'ont pas pu etre verifies
contre une page reelle (egress reseau ferme lors de l'ecriture). `collect.py --probe`
telecharge une page, ecrit le HTML dans `samples/` et affiche ce que chaque strategie
en tire : c'est l'etape de calibration a passer avant la premiere vraie collecte.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Iterator

from selectolax.parser import HTMLParser

# --------------------------------------------------------------------------------------
# URLs de recherche
# --------------------------------------------------------------------------------------

# Candidates a verifier au premier --probe ; la premiere qui repond 200 avec des annonces
# est retenue et doit ensuite etre figee ici.
RECHERCHE_CANDIDATES = [
    "https://www.avito.ma/fr/casablanca/appartements-à_louer?o={page}",
    "https://www.avito.ma/fr/casablanca/appartements-à_louer?page={page}",
    "https://www.avito.ma/fr/casablanca/immobilier-à_louer?o={page}",
]

# Motif d'URL d'une annonce individuelle (sert aussi a filtrer les captures Wayback).
URL_ANNONCE_RE = re.compile(r"avito\.ma/(?:fr|ar)/[^/]+/[^/]*/([\w-]+?)(?:_\d+)?\.htm", re.I)
ID_ANNONCE_RE = re.compile(r"_(\d+)\.htm")


def url_recherche(page: int, gabarit: str = RECHERCHE_CANDIDATES[0]) -> str:
    return gabarit.format(page=page)


def est_url_annonce(url: str) -> bool:
    return bool(URL_ANNONCE_RE.search(url or ""))


def extraire_source_id(url: str) -> str | None:
    m = ID_ANNONCE_RE.search(url or "")
    return m.group(1) if m else None


# --------------------------------------------------------------------------------------
# Dates relatives
# --------------------------------------------------------------------------------------

_UNITES = {
    "minute": 0, "minutes": 0, "heure": 0, "heures": 0,
    "jour": 1, "jours": 1, "semaine": 7, "semaines": 7, "mois": 30, "an": 365, "ans": 365,
}
_RELATIF_RE = re.compile(r"il\s*y\s*a\s*(\d+)\s*(\w+)", re.IGNORECASE)


def parse_date_publication(texte: str | None, aujourdhui: date | None = None) -> str | None:
    """Convertit 'il y a 3 jours', 'aujourd'hui', 'hier' en date ISO.

    Retourne None si le texte n'est pas interpretable : on ne devine pas.
    """
    if not texte:
        return None
    aujourdhui = aujourdhui or date.today()
    plat = texte.strip().lower()

    if "aujourd" in plat or "maintenant" in plat:
        return aujourdhui.isoformat()
    if "hier" in plat:
        return (aujourdhui - timedelta(days=1)).isoformat()

    m = _RELATIF_RE.search(plat)
    if m and m.group(2) in _UNITES:
        return (aujourdhui - timedelta(days=int(m.group(1)) * _UNITES[m.group(2)])).isoformat()

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", plat)
    if m:
        return m.group(0)
    return None


# --------------------------------------------------------------------------------------
# Exploration d'arbres JSON
# --------------------------------------------------------------------------------------

# Cles rencontrees selon les versions du site ; on accepte toutes les variantes plutot
# que de figer un chemin qui changera.
CLES_URL = ("url", "friendlyurl", "friendly_url", "href", "link", "permalink", "canonicalurl")
CLES_PRIX = ("price", "prix", "pricevalue", "price_value", "amount", "rawprice")
CLES_TITRE = ("subject", "title", "titre", "name", "headline")
CLES_DESC = ("description", "body", "text", "content", "descriptif")
CLES_LIEU = ("area", "areaname", "neighborhood", "quartier", "district", "location",
             "locationname", "city", "cityname", "adresse", "address", "zone")
CLES_SURFACE = ("surface", "surfacetotale", "superficie", "area_size", "size", "m2",
                "surface_habitable", "livingarea")
CLES_PIECES = ("rooms", "nbrooms", "pieces", "nbpieces", "nb_pieces", "roomcount")
CLES_CHAMBRES = ("bedrooms", "nbbedrooms", "chambres", "nbchambres", "bedroomcount")
CLES_DATE = ("listtime", "list_time", "date", "createdat", "created_at", "publishedat",
             "datepublication", "insertiondate")
CLES_PRO = ("isshop", "is_shop", "shop", "professional", "ispro", "is_pro", "storeid",
            "seller_type", "sellertype", "accounttype")


def _iter_dicts(noeud: Any) -> Iterator[dict]:
    """Parcourt recursivement un arbre JSON et rend tous les dictionnaires."""
    if isinstance(noeud, dict):
        yield noeud
        for valeur in noeud.values():
            yield from _iter_dicts(valeur)
    elif isinstance(noeud, list):
        for valeur in noeud:
            yield from _iter_dicts(valeur)


def _cle_insensible(d: dict, cles: tuple[str, ...]) -> Any:
    """Retourne la premiere valeur non vide parmi `cles`, insensible a la casse."""
    normalise = {str(k).lower().replace(" ", ""): v for k, v in d.items()}
    for cle in cles:
        valeur = normalise.get(cle)
        if valeur not in (None, "", [], {}):
            return valeur
    return None


def _cle_profonde(d: dict, cles: tuple[str, ...], profondeur: int = 2) -> Any:
    """Comme `_cle_insensible`, mais descend dans les dictionnaires imbriques.

    Necessaire parce que les payloads rangent souvent la valeur utile un cran plus bas :
    `offers.price` en JSON-LD, `priceInfo.value` cote Next.js.
    """
    valeur = _cle_insensible(d, cles)
    if valeur is not None or profondeur <= 1:
        return valeur
    for enfant in d.values():
        if isinstance(enfant, dict):
            trouve = _cle_profonde(enfant, cles, profondeur - 1)
            if trouve is not None:
                return trouve
    return None


def _aplatir(valeur: Any) -> Any:
    """Ramene {'value': x} / {'name': x} / [x] a x. Les payloads imbriquent beaucoup."""
    if isinstance(valeur, dict):
        for cle in ("value", "name", "label", "text", "title", "raw"):
            if cle in valeur and not isinstance(valeur[cle], (dict, list)):
                return valeur[cle]
        return None
    if isinstance(valeur, list):
        return _aplatir(valeur[0]) if valeur else None
    return valeur


def _params_imbriques(d: dict) -> dict:
    """Avito range les caracteristiques dans des listes de {key, value}.

    Les remonte a plat pour que la recherche par cle les trouve.
    """
    plat: dict[str, Any] = {}
    for valeur in d.values():
        if isinstance(valeur, list):
            for item in valeur:
                if isinstance(item, dict):
                    cle = item.get("key") or item.get("name") or item.get("label")
                    val = item.get("value") if "value" in item else item.get("val")
                    if isinstance(cle, str) and val is not None and not isinstance(val, (dict, list)):
                        plat[str(cle).lower().replace(" ", "")] = val
        elif isinstance(valeur, dict):
            for cle, val in valeur.items():
                if not isinstance(val, (dict, list)):
                    plat.setdefault(str(cle).lower().replace(" ", ""), val)
    return plat


def _nombre(valeur: Any) -> float | None:
    valeur = _aplatir(valeur)
    if isinstance(valeur, (int, float)):
        return float(valeur)
    if isinstance(valeur, str):
        m = re.search(r"\d[\d\s.,]*", valeur)
        if m:
            try:
                return float(re.sub(r"[\s,]", "", m.group(0)).rstrip("."))
            except ValueError:
                return None
    return None


def _entier(valeur: Any) -> int | None:
    n = _nombre(valeur)
    return int(n) if n is not None else None


def _booleen_pro(valeur: Any) -> int | None:
    valeur = _aplatir(valeur)
    if isinstance(valeur, bool):
        return int(valeur)
    if isinstance(valeur, (int, float)):
        return 1 if valeur else 0
    if isinstance(valeur, str):
        plat = valeur.strip().lower()
        if plat in {"shop", "pro", "professional", "agence", "store", "true", "1"}:
            return 1
        if plat in {"private", "particulier", "individual", "false", "0"}:
            return 0
    return None


def _annonce_depuis_dict(d: dict, aujourdhui: date | None = None) -> dict | None:
    """Reconnait un objet 'annonce' a sa forme : une URL d'annonce et un prix."""
    url = _aplatir(_cle_insensible(d, CLES_URL))
    if not isinstance(url, str):
        return None
    if url.startswith("/"):
        url = "https://www.avito.ma" + url
    if not est_url_annonce(url):
        return None

    prix = _cle_profonde(d, CLES_PRIX)
    titre = _aplatir(_cle_insensible(d, CLES_TITRE))
    if prix is None and titre is None:
        return None

    params = _params_imbriques(d)
    fusion = {**params, **{str(k).lower().replace(" ", ""): v for k, v in d.items()}}

    return {
        "url": url.split("?")[0],
        "source_id": extraire_source_id(url),
        "titre": _aplatir(titre) if titre is not None else None,
        "description": _aplatir(_cle_insensible(d, CLES_DESC)),
        "quartier_raw": _aplatir(_cle_profonde(d, CLES_LIEU)),
        "loyer_mad": _nombre(prix),
        "surface_m2": _nombre(_cle_insensible(fusion, CLES_SURFACE)),
        "nb_pieces": _entier(_cle_insensible(fusion, CLES_PIECES)),
        "nb_chambres": _entier(_cle_insensible(fusion, CLES_CHAMBRES)),
        "date_publication": parse_date_publication(
            str(_aplatir(_cle_insensible(d, CLES_DATE)) or ""), aujourdhui
        ),
        "is_pro": _booleen_pro(_cle_insensible(d, CLES_PRO)),
    }


# --------------------------------------------------------------------------------------
# Strategies
# --------------------------------------------------------------------------------------

def strategie_next_data(html: str, aujourdhui: date | None = None) -> list[dict]:
    """Lit le payload JSON de Next.js (`<script id="__NEXT_DATA__">`)."""
    arbre = HTMLParser(html)
    noeud = arbre.css_first('script#__NEXT_DATA__')
    if noeud is None:
        return []
    try:
        donnees = json.loads(noeud.text())
    except (json.JSONDecodeError, ValueError):
        return []
    return _collecter(donnees, aujourdhui, "next_data")


def strategie_jsonld(html: str, aujourdhui: date | None = None) -> list[dict]:
    """Lit les balises `application/ld+json` (ItemList, Product, Offer)."""
    resultats: list[dict] = []
    for noeud in HTMLParser(html).css('script[type="application/ld+json"]'):
        try:
            donnees = json.loads(noeud.text())
        except (json.JSONDecodeError, ValueError):
            continue
        resultats.extend(_collecter(donnees, aujourdhui, "jsonld"))
    return _dedoublonner(resultats)


def _collecter(donnees: Any, aujourdhui: date | None, strategie: str) -> list[dict]:
    trouves: list[dict] = []
    for d in _iter_dicts(donnees):
        annonce = _annonce_depuis_dict(d, aujourdhui)
        if annonce:
            annonce["_strategie"] = strategie
            trouves.append(annonce)
    return _dedoublonner(trouves)


def _dedoublonner(annonces: list[dict]) -> list[dict]:
    """Une meme annonce apparait plusieurs fois dans l'arbre JSON ; on garde la
    occurrence la plus renseignee."""
    par_url: dict[str, dict] = {}
    for a in annonces:
        existante = par_url.get(a["url"])
        if existante is None or _remplissage(a) > _remplissage(existante):
            par_url[a["url"]] = a
    return list(par_url.values())


def _remplissage(annonce: dict) -> int:
    return sum(1 for k, v in annonce.items() if not k.startswith("_") and v not in (None, ""))


def strategie_dom(html: str, aujourdhui: date | None = None) -> list[dict]:
    """Repli : lit les cartes d'annonces du DOM.

    Volontairement generique — on part des liens vers des annonces et on lit le texte
    du bloc parent, plutot que de dependre de noms de classes qui changent a chaque
    refonte. Moins precis, mais ne casse pas silencieusement.
    """
    arbre = HTMLParser(html)
    par_url: dict[str, dict] = {}

    for lien in arbre.css("a[href]"):
        href = lien.attributes.get("href", "")
        if href.startswith("/"):
            href = "https://www.avito.ma" + href
        if not est_url_annonce(href):
            continue

        bloc = lien
        for _ in range(3):                       # on remonte au bloc de la carte
            if bloc.parent is None:
                break
            bloc = bloc.parent

        texte = " ".join(bloc.text(separator=" ", strip=True).split())
        url = href.split("?")[0]
        annonce = {
            "url": url,
            "source_id": extraire_source_id(url),
            "titre": " ".join(lien.text(separator=" ", strip=True).split()) or None,
            "description": None,
            "quartier_raw": None,
            "loyer_mad": _prix_depuis_texte(texte),
            "surface_m2": None,
            "nb_pieces": None,
            "nb_chambres": None,
            "date_publication": parse_date_publication(texte, aujourdhui),
            "is_pro": None,
            "_strategie": "dom",
            "_texte_carte": texte,
        }
        existante = par_url.get(url)
        if existante is None or _remplissage(annonce) > _remplissage(existante):
            par_url[url] = annonce
    return list(par_url.values())


_PRIX_TEXTE_RE = re.compile(r"(\d[\d\s.,]{2,})\s*(?:dh|dhs|mad|درهم)", re.IGNORECASE)


def _prix_depuis_texte(texte: str) -> float | None:
    m = _PRIX_TEXTE_RE.search(texte or "")
    if not m:
        return None
    try:
        return float(re.sub(r"[\s.,]", "", m.group(1)))
    except ValueError:
        return None


STRATEGIES = (strategie_next_data, strategie_jsonld, strategie_dom)


def parse_page_resultats(html: str, aujourdhui: date | None = None) -> tuple[list[dict], str | None]:
    """Applique les strategies dans l'ordre et retourne (annonces, strategie_retenue).

    Retourne ([], None) si aucune strategie ne trouve d'annonce : l'appelant doit
    logger l'echec et s'arreter, jamais l'interpreter comme une page vide.
    """
    for strategie in STRATEGIES:
        try:
            annonces = strategie(html, aujourdhui)
        except Exception:  # noqa: BLE001 - une strategie qui casse ne doit pas tout arreter
            continue
        if annonces:
            return annonces, annonces[0].get("_strategie")
    return [], None


def diagnostic(html: str, aujourdhui: date | None = None) -> dict:
    """Compte ce que chaque strategie trouve. Utilise par `collect.py --probe`."""
    rapport = {}
    for strategie in STRATEGIES:
        nom = strategie.__name__.replace("strategie_", "")
        try:
            rapport[nom] = len(strategie(html, aujourdhui))
        except Exception as exc:  # noqa: BLE001
            rapport[nom] = f"erreur: {type(exc).__name__}"
    return rapport
