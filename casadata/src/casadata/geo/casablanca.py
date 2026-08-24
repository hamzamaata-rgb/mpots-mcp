"""Normalisation géographique Casablanca.

Le gazetteer (gazetteer.json) est le référentiel pivot :
raw_location (texte libre des portails) -> slug normalisé + confiance.

Règles :
- on conserve TOUJOURS raw_location en base ; la normalisation est additive ;
- correspondance exacte d'alias  -> confiance 0.95 ;
- alias contenu dans le texte    -> confiance 0.80 (le plus long gagne) ;
- rien trouvé mais 'casablanca'  -> slug 'casablanca', confiance 0.30 ;
- rien du tout                   -> None.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files


def normalize_text(text: str) -> str:
    """minuscule, sans accents ni ponctuation, espaces compactés."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^\w\s؀-ۿ]", " ", text)  # garde lettres latines + arabes
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class GeoMatch:
    slug: str
    level: str
    name: str
    arrondissement: str | None
    prefecture: str | None
    confidence: float


class Gazetteer:
    def __init__(self, data: dict | None = None):
        if data is None:
            data = json.loads(
                files("casadata").joinpath("geo/gazetteer.json").read_text(encoding="utf-8")
            )
        self.data = data
        self.by_slug: dict[str, dict] = {loc["slug"]: loc for loc in data["locations"]}
        # index alias normalisé -> slug (les alias les plus longs d'abord pour le scan)
        self._alias_index: dict[str, str] = {}
        for loc in data["locations"]:
            for alias in [loc["name"], *loc.get("aliases", [])]:
                norm = normalize_text(alias)
                if norm and norm not in self._alias_index:
                    self._alias_index[norm] = loc["slug"]
        self._aliases_sorted = sorted(self._alias_index, key=len, reverse=True)

    def match(self, raw_location: str | None) -> GeoMatch | None:
        if not raw_location:
            return None
        norm = normalize_text(raw_location)
        if not norm:
            return None
        # 1. exact
        if norm in self._alias_index:
            return self._to_match(self._alias_index[norm], 0.95)
        # 2. contenu (l'alias le plus long présent comme mot(s) entiers gagne)
        padded = f" {norm} "
        for alias in self._aliases_sorted:
            if len(alias) < 3:
                continue
            if f" {alias} " in padded:
                slug = self._alias_index[alias]
                if slug == "casablanca":
                    continue  # géré en 3, confiance plus basse
                return self._to_match(slug, 0.80)
        # 3. fallback ville
        if "casablanca" in norm or " casa " in padded:
            return self._to_match("casablanca", 0.30)
        return None

    def _to_match(self, slug: str, confidence: float) -> GeoMatch:
        loc = self.by_slug[slug]
        return GeoMatch(
            slug=slug,
            level=loc["level"],
            name=loc["name"],
            arrondissement=loc.get("arrondissement"),
            prefecture=loc.get("prefecture"),
            confidence=confidence,
        )

    def iter_locations(self):
        yield from self.data["locations"]


@lru_cache(maxsize=1)
def default_gazetteer() -> Gazetteer:
    return Gazetteer()


def sync_locations(conn) -> int:
    """Insère le gazetteer dans la table location (idempotent)."""
    gz = default_gazetteer()
    n = 0
    for loc in gz.iter_locations():
        quartier = loc["name"] if loc["level"] in ("quartier", "micro_quartier") else None
        micro = loc["name"] if loc["level"] == "micro_quartier" else None
        if micro and loc.get("parent"):
            parent = gz.by_slug.get(loc["parent"])
            quartier = parent["name"] if parent else quartier
        conn.execute(
            """
            INSERT INTO location (slug, city, prefecture, arrondissement, quartier,
                                  micro_quartier, level, geo_source)
            SELECT ?, 'casablanca', ?, ?, ?, ?, ?, 'gazetteer'
            WHERE NOT EXISTS (SELECT 1 FROM location WHERE slug = ?)
            """,
            [loc["slug"], loc.get("prefecture"), loc.get("arrondissement"),
             quartier, micro, loc["level"], loc["slug"]],
        )
        n += 1
    return n
