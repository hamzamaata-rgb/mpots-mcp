"""Format pivot d'ingestion.

Chaque collecteur/adaptateur produit des RawRecord ; l'ingestion (observations.py)
ne connaît que ce format. Cela découple totalement 'récupérer' de 'stocker'.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime


PROPERTY_TYPES = {
    "apartment", "house", "villa", "studio", "riad", "duplex",
    "land", "commercial", "office", "room", "other",
}

# Clés d'attributs booléens/valeur attendues dans attrs (non exhaustif, libre).
KNOWN_ATTRS = [
    "elevator", "parking", "garage", "balcony", "terrace", "garden", "pool",
    "air_conditioning", "heating", "equipped_kitchen", "furnished", "security",
    "cellar", "service_room", "view", "orientation", "availability",
    "lease_duration", "charges_included", "new_build",
]


@dataclass
class RawRecord:
    # identité
    source_code: str                      # 'mubawab', 'avito', ...
    external_id: str                      # id portail ; si absent: hash d'URL via from_url()
    transaction_type: str                 # 'sale' | 'rent'
    observed_at: datetime                 # horodatage de l'OBSERVATION (pas de l'import)
    # cœur marché
    price: float | None = None            # MAD ; location = loyer
    rent_period: str | None = None        # 'month' par défaut côté adaptateur location
    surface_m2: float | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    floor: int | None = None
    floors_total: int | None = None
    property_type: str | None = None
    condition: str | None = None
    age_years: str | None = None
    furnished: bool | None = None
    charges_included: bool | None = None
    # géo
    raw_location: str | None = None
    lat: float | None = None
    lon: float | None = None
    # meta annonce
    url: str | None = None
    title: str | None = None
    description: str | None = None
    published_at: datetime | None = None
    photos_count: int | None = None
    photo_urls: list[str] = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    # vendeur (identifiant plateforme brut: hashé à l'ingestion, jamais stocké en clair)
    seller_external_id: str | None = None
    seller_type: str | None = None        # 'particulier' | 'agence' | 'promoteur'
    agency_name: str | None = None        # conservé uniquement si agence/promoteur
    # provenance
    confidence: float = 1.0
    raw_ref: str | None = None            # pointeur vers le JSONL brut

    @staticmethod
    def id_from_url(url: str) -> str:
        return "url:" + hashlib.sha256(url.encode()).hexdigest()[:20]

    def description_hash(self) -> str | None:
        if not self.description:
            return None
        return hashlib.sha256(self.description.strip().lower().encode()).hexdigest()[:24]

    def to_json(self) -> str:
        d = asdict(self)
        for k in ("observed_at", "published_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        return json.dumps(d, ensure_ascii=False)
