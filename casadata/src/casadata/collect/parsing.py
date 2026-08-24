"""Extraction générique depuis le HTML des portails.

Stratégie en couches, testable hors-ligne :
1. blocs JSON-LD schema.org (Product/Offer/Residence/RealEstateListing) ;
2. balises meta OpenGraph ;
3. heuristiques regex FR/AR (prix en MAD/DH, surface en m², pièces/chambres).

Les collecteurs par source raffinent ensuite (sélecteurs spécifiques) — à
valider sur pages réelles lors du premier run live.
"""
from __future__ import annotations

import json
import re

RE_JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
RE_META = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](og:[\w:]+|description)["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE,
)
RE_PRICE = re.compile(
    r"(\d[\d\s .,]{3,15})\s*(?:MAD|DH|Dhs?|درهم)", re.IGNORECASE
)
RE_SURFACE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*m\s*[²2]", re.IGNORECASE)
RE_ROOMS = re.compile(r"(\d{1,2})\s*pi[èe]ces?", re.IGNORECASE)
RE_BEDROOMS = re.compile(r"(\d{1,2})\s*chambres?", re.IGNORECASE)
RE_BATHROOMS = re.compile(r"(\d{1,2})\s*salles?\s*de\s*bains?", re.IGNORECASE)
RE_FLOOR = re.compile(r"[ée]tage\s*:?\s*(\d{1,2})|(\d{1,2})\s*(?:e|ème|er)\s*[ée]tage", re.IGNORECASE)

ATTR_KEYWORDS = {
    "elevator": ["ascenseur", "مصعد"],
    "parking": ["parking", "place de parking"],
    "garage": ["garage"],
    "balcony": ["balcon", "شرفة"],
    "terrace": ["terrasse", "تراس"],
    "garden": ["jardin", "حديقة"],
    "pool": ["piscine", "مسبح"],
    "air_conditioning": ["climatisation", "clim ", "مكيف"],
    "heating": ["chauffage", "تدفئة"],
    "equipped_kitchen": ["cuisine equipee", "cuisine équipée", "مطبخ مجهز"],
    "furnished": ["meuble", "meublé", "مفروش"],
    "security": ["securite", "sécurité", "gardiennage", "concierge", "حراسة"],
    "cellar": ["cave"],
    "service_room": ["chambre de service", "chambre de bonne"],
    "new_build": ["neuf", "nouveau projet", "جديد"],
}


def _to_number(text: str) -> float | None:
    cleaned = re.sub(r"[\s ]", "", text).replace(",", ".")
    # 1.250.000 -> 1250000 ; garde une décimale finale légitime
    if cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_jsonld(html: str) -> list[dict]:
    out = []
    for m in RE_JSONLD.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def extract_meta(html: str) -> dict[str, str]:
    return {k: v for k, v in RE_META.findall(html)}


def extract_price_mad(text: str) -> float | None:
    m = RE_PRICE.search(text)
    return _to_number(m.group(1)) if m else None


def extract_features(text: str) -> dict:
    """surface, pièces, chambres, SDB, étage + attributs booléens détectés."""
    out: dict = {}
    if m := RE_SURFACE.search(text):
        out["surface_m2"] = _to_number(m.group(1))
    if m := RE_ROOMS.search(text):
        out["rooms"] = int(m.group(1))
    if m := RE_BEDROOMS.search(text):
        out["bedrooms"] = int(m.group(1))
    if m := RE_BATHROOMS.search(text):
        out["bathrooms"] = int(m.group(1))
    if m := RE_FLOOR.search(text):
        out["floor"] = int(m.group(1) or m.group(2))
    lower = text.lower()
    attrs = {key: True for key, kws in ATTR_KEYWORDS.items() if any(k in lower for k in kws)}
    if attrs:
        out["attrs"] = attrs
    return out


def jsonld_offer_price(blocks: list[dict]) -> float | None:
    for block in blocks:
        offers = block.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = offers.get("price") or block.get("price")
        if price is not None:
            try:
                return float(str(price).replace(",", "").replace(" ", ""))
            except ValueError:
                continue
    return None


def jsonld_geo(blocks: list[dict]) -> tuple[float | None, float | None]:
    for block in blocks:
        geo = block.get("geo") or {}
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon)
            except (TypeError, ValueError):
                continue
    return None, None
