"""Ingestion de datasets historiques déjà constitués (CSV).

Chaque import est décrit par un manifest JSON (provenance, licence, période,
confiance) + un mapping de colonnes vers RawRecord. Presets fournis pour les
datasets identifiés pendant la phase de recherche ; le preset 'generic'
couvre tout CSV via mapping explicite dans le manifest.

Manifest attendu (data/incoming/<fichier>.manifest.json) :
{
  "source_code": "university_2019_2021",     // déclaré dans config.KNOWN_SOURCES
  "original_url": "https://...",
  "license": "CC-BY-4.0 | unknown | ...",
  "period_start": "2019-01-01",
  "period_end": "2021-12-31",
  "confidence": 0.8,
  "transaction_type": "sale",                // défaut si pas de colonne
  "observed_at_column": "date",              // sinon: milieu de période
  "columns": { "price": "prix", "surface_m2": "surface", ... }
}
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..db import finish_run, source_id, start_run
from .base import RawRecord
from .observations import ingest_record

# Champs RawRecord acceptés dans "columns"
MAPPABLE = [
    "external_id", "transaction_type", "price", "surface_m2", "rooms", "bedrooms",
    "bathrooms", "floor", "floors_total", "property_type", "condition", "age_years",
    "raw_location", "lat", "lon", "url", "title", "description", "seller_type",
    "agency_name", "furnished",
]
NUMERIC = {"price", "surface_m2", "lat", "lon"}
INTEGER = {"rooms", "bedrooms", "bathrooms", "floor", "floors_total"}

# Presets de mapping pour les datasets repérés en phase de recherche.
# À AJUSTER après téléchargement réel (les noms de colonnes sont à confirmer).
PRESETS: dict[str, dict] = {
    "seed_github_chp": {
        "transaction_type": "sale",
        "confidence": 0.75,
        "columns": {
            "price": "price", "surface_m2": "area", "rooms": "rooms",
            "bedrooms": "bedrooms", "bathrooms": "bathrooms", "floor": "floor",
            "raw_location": "neighbourhood", "lat": "latitude", "lon": "longitude",
            "condition": "state", "age_years": "age", "title": "title",
            "property_type": "type",
        },
    },
    "kaggle_ma_housing": {
        "transaction_type": "sale",
        "confidence": 0.7,
        "columns": {
            "price": "price", "surface_m2": "surface", "rooms": "rooms",
            "bedrooms": "bedrooms", "bathrooms": "bathrooms",
            "raw_location": "location", "property_type": "type", "title": "title",
        },
    },
    "university_2019_2021": {
        "transaction_type": "sale",
        "confidence": 0.8,
        "columns": {},  # à remplir depuis le fichier une fois localisé (cf. STRATEGY §1.4)
    },
    "generic": {"transaction_type": "sale", "confidence": 0.6, "columns": {}},
}


def _parse_value(field: str, value: str):
    value = (value or "").strip()
    if value == "" or value.lower() in ("na", "nan", "null", "none", "-"):
        return None
    if field in NUMERIC:
        cleaned = re.sub(r"[^\d.,-]", "", value).replace(",", ".")
        if cleaned.count(".") > 1:
            cleaned = cleaned.replace(".", "", cleaned.count(".") - 1)
        try:
            return float(cleaned)
        except ValueError:
            return None
    if field in INTEGER:
        m = re.search(r"-?\d+", value)
        return int(m.group()) if m else None
    return value


def _parse_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
    return None


def load_manifest(csv_path: str | Path) -> dict:
    path = Path(str(csv_path) + ".manifest.json")
    if not path.exists():
        raise FileNotFoundError(
            f"manifest manquant: {path} — chaque import historique doit déclarer "
            "sa provenance (source_code, original_url, license, période, confiance)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_dataset(conn, csv_path: str | Path, manifest: dict | None = None) -> dict:
    csv_path = Path(csv_path)
    manifest = manifest or load_manifest(csv_path)
    source_code = manifest["source_code"]
    preset = dict(PRESETS.get(source_code, PRESETS["generic"]))
    columns = {**preset.get("columns", {}), **manifest.get("columns", {})}
    if not columns:
        raise ValueError(f"mapping de colonnes vide pour {source_code}: "
                         "renseigner manifest['columns'] (voir docstring).")
    default_tx = manifest.get("transaction_type", preset.get("transaction_type", "sale"))
    confidence = float(manifest.get("confidence", preset.get("confidence", 0.6)))
    date_col = manifest.get("observed_at_column")

    period_start = manifest.get("period_start")
    period_end = manifest.get("period_end")
    fallback_dt = datetime.now(timezone.utc)
    if period_start and period_end:
        d0, d1 = _parse_date(period_start), _parse_date(period_end)
        if d0 and d1:
            fallback_dt = d0 + (d1 - d0) / 2  # milieu de période, faute de mieux

    sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    sid = source_id(conn, source_code)
    run_id = start_run(conn, source_code, method="dataset_import",
                       scope=csv_path.name, raw_path=str(csv_path))
    parsed = failed = 0
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for i, row in enumerate(reader):
                try:
                    values: dict = {}
                    for field, col in columns.items():
                        if col in row:
                            values[field] = _parse_value(field, row[col])
                    tx = values.pop("transaction_type", None) or default_tx
                    if isinstance(tx, str):
                        tx = {"vente": "sale", "location": "rent", "louer": "rent",
                              "vendre": "sale"}.get(tx.lower().strip(), tx.lower().strip())
                    if tx not in ("sale", "rent"):
                        tx = default_tx
                    observed_at = fallback_dt
                    if date_col and row.get(date_col):
                        observed_at = _parse_date(row[date_col]) or fallback_dt
                    ext = values.pop("external_id", None) or f"{csv_path.stem}:{i}"
                    rec = RawRecord(
                        source_code=source_code,
                        external_id=str(ext),
                        transaction_type=tx,
                        observed_at=observed_at,
                        rent_period="month" if tx == "rent" else None,
                        confidence=confidence,
                        raw_ref=f"{csv_path}#{i + 2}",
                        **{k: v for k, v in values.items() if k in MAPPABLE},
                    )
                    ingest_record(conn, run_id, rec)
                    parsed += 1
                except Exception:
                    failed += 1

        conn.execute(
            """INSERT INTO dataset_manifest (source_id, file_name, sha256, original_url,
                   license, period_start, period_end, row_count, confidence, ingested_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp, ?)""",
            [sid, csv_path.name, sha, manifest.get("original_url"),
             manifest.get("license", "unknown"), period_start, period_end,
             parsed, confidence, manifest.get("notes")],
        )
        finish_run(conn, run_id, "success" if failed == 0 else "partial",
                   0, parsed, failed)
        return {"run_id": run_id, "parsed": parsed, "failed": failed, "sha256": sha}
    except Exception as exc:
        finish_run(conn, run_id, "failed", 0, parsed, failed, repr(exc))
        raise
