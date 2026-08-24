"""Contrôles qualité : on FLAGGE, on ne supprime jamais.

Bornes volontairement larges — le but est de marquer l'improbable, pas de
censurer les extrêmes réels du marché.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..ingest.base import RawRecord

# Vente (MAD/m²) — Casablanca : marché courant ~6k-45k ; bornes d'alerte larges.
SALE_PPM2_MIN, SALE_PPM2_MAX = 1_500, 120_000
# Location (MAD/m²/mois)
RENT_PPM2_MIN, RENT_PPM2_MAX = 20, 500
SURFACE_MIN, SURFACE_MAX = 8, 5_000
SALE_PRICE_MIN, SALE_PRICE_MAX = 50_000, 200_000_000
RENT_PRICE_MIN, RENT_PRICE_MAX = 500, 500_000


def quality_flags_for(rec: "RawRecord") -> list[str]:
    flags: list[str] = []
    price, surface = rec.price, rec.surface_m2

    if price is None:
        flags.append("price_missing")
    elif rec.transaction_type == "sale":
        if not (SALE_PRICE_MIN <= price <= SALE_PRICE_MAX):
            flags.append("price_out_of_range")
    elif rec.transaction_type == "rent":
        if not (RENT_PRICE_MIN <= price <= RENT_PRICE_MAX):
            flags.append("price_out_of_range")

    if surface is None:
        flags.append("surface_missing")
    elif not (SURFACE_MIN <= surface <= SURFACE_MAX):
        flags.append("surface_out_of_range")

    if price and surface and surface > 0:
        ppm2 = price / surface
        if rec.transaction_type == "sale" and not (SALE_PPM2_MIN <= ppm2 <= SALE_PPM2_MAX):
            flags.append("ppm2_outlier")
        if rec.transaction_type == "rent" and not (RENT_PPM2_MIN <= ppm2 <= RENT_PPM2_MAX):
            flags.append("ppm2_outlier")

    if not rec.raw_location:
        flags.append("location_missing")
    if rec.bedrooms is not None and rec.rooms is not None and rec.bedrooms > rec.rooms:
        flags.append("bedrooms_gt_rooms")
    if rec.lat is not None and rec.lon is not None:
        # boîte englobante large du Grand Casablanca
        if not (33.30 <= rec.lat <= 33.75 and -7.95 <= rec.lon <= -7.30):
            flags.append("coords_outside_casablanca")
    return flags
