"""Statistiques descriptives — volontairement simples à ce stade.

La priorité est DATA -> CLEAN -> HISTORY -> GEO -> DEDUP -> VALIDATION ;
les modèles (hédonique, liquidité, fair value) viendront sur une base saine.
Ces requêtes posent déjà les métriques cibles : prix/m², volumes, durée
d'exposition, baisses de prix par quartier.
"""
from __future__ import annotations


def market_stats(conn) -> dict:
    """Vue d'ensemble de la base."""
    out: dict = {}
    for label, query in {
        "sources": "SELECT count(*) FROM source",
        "runs": "SELECT count(*) FROM scrape_run",
        "listings": "SELECT count(*) FROM listing",
        "listings_active": "SELECT count(*) FROM listing WHERE status = 'active'",
        "observations": "SELECT count(*) FROM listing_observation",
        "events": "SELECT count(*) FROM listing_event",
        "properties": "SELECT count(*) FROM property",
        "aggregates": "SELECT count(*) FROM market_aggregate",
        "sellers": "SELECT count(*) FROM seller",
    }.items():
        out[label] = conn.execute(query).fetchone()[0]
    out["by_source"] = conn.execute(
        """SELECT s.code, l.transaction_type, count(*) AS n
           FROM listing l JOIN source s USING (source_id)
           GROUP BY 1, 2 ORDER BY 1, 2"""
    ).fetchall()
    out["date_range"] = conn.execute(
        "SELECT min(observed_at), max(observed_at) FROM listing_observation"
    ).fetchone()
    return out


def quartier_stats(conn, transaction_type: str = "sale", min_obs: int = 5) -> list[tuple]:
    """Prix/m² médian et volumes par quartier (dernière observation par annonce,
    observations flaggées exclues des agrégats mais jamais de la base)."""
    return conn.execute(
        """
        SELECT loc.quartier,
               count(*)                                        AS n_listings,
               round(median(o.price / o.surface_m2))           AS ppm2_median,
               round(quantile_cont(o.price / o.surface_m2, 0.25)) AS ppm2_p25,
               round(quantile_cont(o.price / o.surface_m2, 0.75)) AS ppm2_p75,
               round(median(o.surface_m2))                     AS surface_median
        FROM listing l
        JOIN latest_observation o ON o.listing_id = l.listing_id
        JOIN location loc ON loc.location_id = l.location_id
        WHERE l.transaction_type = ?
          AND o.price IS NOT NULL AND o.surface_m2 > 0
          AND (o.quality_flags IS NULL
               OR NOT list_contains(CAST(o.quality_flags AS VARCHAR[]), 'ppm2_outlier'))
          AND loc.quartier IS NOT NULL
        GROUP BY loc.quartier
        HAVING count(*) >= ?
        ORDER BY ppm2_median DESC
        """,
        [transaction_type, min_obs],
    ).fetchall()


def liquidity_stats(conn, transaction_type: str = "sale") -> list[tuple]:
    """Durée d'exposition et baisses par quartier (annonces disparues)."""
    return conn.execute(
        """
        SELECT loc.quartier,
               count(*)                              AS n_disappeared,
               round(median(lc.days_on_market), 1)   AS days_median,
               round(avg(lc.n_price_changes), 2)     AS avg_price_changes,
               round(100 * avg(lc.price_drop_pct), 2) AS avg_drop_pct
        FROM listing_lifecycle lc
        JOIN location loc ON loc.location_id = lc.location_id
        WHERE lc.transaction_type = ? AND lc.status = 'disappeared'
          AND loc.quartier IS NOT NULL
        GROUP BY loc.quartier
        HAVING count(*) >= 3
        ORDER BY days_median
        """,
        [transaction_type],
    ).fetchall()


def export_parquet(conn, export_dir) -> list[str]:
    """Exporte les tables analytiques en Parquet (partage/notebooks)."""
    from pathlib import Path

    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table in ("listing", "listing_observation", "listing_event",
                  "property", "property_link", "location", "market_aggregate"):
        dest = export_dir / f"{table}.parquet"
        conn.execute(f"COPY {table} TO '{dest}' (FORMAT PARQUET)")
        written.append(str(dest))
    return written
