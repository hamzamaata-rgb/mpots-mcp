"""Comparables locatifs et rendement brut.

Estimation de loyer par comparables : même quartier, surface similaire,
chambres similaires. C'est l'étape 'statistiques simples sur base saine' —
le modèle hédonique complet viendra quand le volume le justifiera.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RentEstimate:
    rent_monthly: float | None
    rent_per_m2: float | None
    n_comparables: int
    quartier: str | None
    method: str
    confidence: str  # 'high' | 'medium' | 'low' | 'none'


def estimate_rent(conn, quartier_slug: str, surface_m2: float,
                  bedrooms: int | None = None) -> RentEstimate:
    """Loyer mensuel probable par comparables (médiane du loyer/m² appliquée
    à la surface). Élargit les critères tant qu'il n'y a pas assez de comparables."""
    tiers = [
        # (delta surface relatif, filtre chambres, confiance)
        (0.15, True, "high"),
        (0.25, True, "medium"),
        (0.35, False, "medium"),
        (0.60, False, "low"),
    ]
    for delta, use_bedrooms, confidence in tiers:
        query = """
            SELECT o.price / o.surface_m2 AS rent_ppm2
            FROM listing l
            JOIN latest_observation o ON o.listing_id = l.listing_id
            JOIN location loc ON loc.location_id = l.location_id
            WHERE l.transaction_type = 'rent'
              AND (o.rent_period = 'month' OR o.rent_period IS NULL)
              AND loc.slug = ?
              AND o.price IS NOT NULL AND o.surface_m2 > 0
              AND o.surface_m2 BETWEEN ? AND ?
              AND (o.quality_flags IS NULL
                   OR NOT list_contains(CAST(o.quality_flags AS VARCHAR[]), 'ppm2_outlier'))
        """
        params: list = [quartier_slug, surface_m2 * (1 - delta), surface_m2 * (1 + delta)]
        if use_bedrooms and bedrooms is not None:
            query += " AND o.bedrooms BETWEEN ? AND ?"
            params += [bedrooms - 1, bedrooms + 1]
        rows = [r[0] for r in conn.execute(query, params).fetchall()]
        if len(rows) >= 3:
            rows.sort()
            median = rows[len(rows) // 2] if len(rows) % 2 else \
                (rows[len(rows) // 2 - 1] + rows[len(rows) // 2]) / 2
            return RentEstimate(
                rent_monthly=round(median * surface_m2),
                rent_per_m2=round(median, 1),
                n_comparables=len(rows),
                quartier=quartier_slug,
                method=f"comparables±{int(delta * 100)}%"
                       + ("+chambres" if use_bedrooms and bedrooms is not None else ""),
                confidence=confidence,
            )
    return RentEstimate(None, None, 0, quartier_slug, "none", "none")


def gross_yield(price: float, rent_monthly: float,
                acquisition_costs_rate: float = 0.075) -> dict:
    """Rendement brut = loyer annuel / coût total d'acquisition.
    acquisition_costs_rate ~7,5 % (droits d'enregistrement, conservation
    foncière, notaire — ordre de grandeur Maroc, à affiner par dossier)."""
    total_cost = price * (1 + acquisition_costs_rate)
    return {
        "gross_yield_pct": round(100 * rent_monthly * 12 / total_cost, 2),
        "total_acquisition_cost": round(total_cost),
        "annual_rent": rent_monthly * 12,
    }
