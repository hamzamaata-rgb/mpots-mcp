"""Séries institutionnelles et agrégats de calibration.

- IPAI (Bank Al-Maghrib x ANCFCC) : indice trimestriel des prix des actifs
  immobiliers, 2006->aujourd'hui, par ville (dont Casablanca) et type d'actif,
  + nombre de transactions. Publié en PDF/tableaux trimestriels sur bkam.ma
  et ancfcc.gov.ma. Les valeurs ne sont PAS embarquées dans le code (pas de
  données inventées) : on ingère un CSV transcrit/exporté des publications.
- Référentiels de prix par quartier (Agenz, Yakeey) : mêmes mécanique et table.

Format CSV attendu (voir data/incoming/README) :
series_code,geo_level,geo_slug,period_start,period_end,metric,value,unit
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..db import finish_run, source_id, start_run

REQUIRED_COLUMNS = {"series_code", "geo_level", "period_start", "period_end",
                    "metric", "value"}


def ingest_aggregates_csv(conn, source_code: str, csv_path: str | Path,
                          raw_ref: str | None = None) -> dict:
    """Ingère un CSV de séries agrégées dans market_aggregate (idempotent)."""
    path = Path(csv_path)
    run_id = start_run(conn, source_code, method="dataset_import",
                       scope=f"aggregates:{path.name}", raw_path=str(path))
    inserted = skipped = failed = 0
    sid = source_id(conn, source_code)
    try:
        with path.open(encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"colonnes manquantes dans {path.name}: {sorted(missing)}")
            for row in reader:
                try:
                    n = conn.execute(
                        """
                        INSERT INTO market_aggregate (source_id, series_code, geo_level,
                            geo_slug, period_start, period_end, metric, value, unit, raw_ref)
                        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        WHERE NOT EXISTS (
                            SELECT 1 FROM market_aggregate
                            WHERE source_id = ? AND series_code = ?
                              AND geo_level = ? AND geo_slug IS NOT DISTINCT FROM ?
                              AND period_start = ? AND period_end = ? AND metric = ?
                        )
                        """,
                        [sid, row["series_code"], row["geo_level"], row.get("geo_slug") or None,
                         row["period_start"], row["period_end"], row["metric"],
                         float(row["value"]), row.get("unit") or None,
                         raw_ref or str(path),
                         sid, row["series_code"], row["geo_level"], row.get("geo_slug") or None,
                         row["period_start"], row["period_end"], row["metric"]],
                    ).rowcount
                    if n:
                        inserted += 1
                    else:
                        skipped += 1
                except (ValueError, KeyError):
                    failed += 1
        finish_run(conn, run_id, "success" if failed == 0 else "partial",
                   0, inserted, failed, f"skipped_existing={skipped}")
        return {"run_id": run_id, "inserted": inserted, "skipped": skipped, "failed": failed}
    except Exception as exc:
        finish_run(conn, run_id, "failed", 0, inserted, failed, repr(exc))
        raise


# URLs de départ pour récupérer les publications IPAI (à télécharger depuis un
# environnement avec accès Internet complet, puis transcrire en CSV) :
IPAI_LANDING_PAGES = [
    "https://www.bkam.ma/Statistiques/Prix/Publications-ipai/Indice-des-prix-des-actifs-immobiliers",
    "https://www.ancfcc.gov.ma",  # rubrique publications IPAI trimestrielles
]
