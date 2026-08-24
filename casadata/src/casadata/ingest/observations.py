"""Cycle d'observation — le cœur du système.

Règles :
- une annonce déjà connue n'est JAMAIS mise à jour en écrasant le prix :
  chaque passage ajoute une listing_observation (append-only) ;
- changements de prix -> listing_event('price_change') ;
- annonce absente d'un run complet -> 'disappeared' (≠ vendu !) ;
- annonce revue après disparition -> 'reappeared' ;
- toute observation porte run_id, raw_ref, confidence, quality_flags.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from ..config import SETTINGS
from ..db import source_id
from ..geo.casablanca import default_gazetteer
from ..quality.validators import quality_flags_for
from .base import RawRecord


def _seller_hash(external: str) -> str:
    return hashlib.sha256((SETTINGS.seller_hash_salt + external).encode()).hexdigest()[:32]


def _upsert_seller(conn, sid: int, rec: RawRecord) -> int | None:
    if not rec.seller_external_id:
        return None
    h = _seller_hash(rec.seller_external_id)
    seller_type = rec.seller_type or "unknown"
    # nom conservé uniquement pour les personnes morales
    agency = rec.agency_name if seller_type in ("agence", "promoteur") else None
    row = conn.execute(
        "SELECT seller_id FROM seller WHERE source_id = ? AND external_hash = ?", [sid, h]
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE seller SET last_seen_at = ?, seller_type = ?,
               agency_name = coalesce(?, agency_name) WHERE seller_id = ?""",
            [rec.observed_at, seller_type, agency, row[0]],
        )
        return row[0]
    return conn.execute(
        """INSERT INTO seller (source_id, external_hash, seller_type, agency_name,
                               first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?) RETURNING seller_id""",
        [sid, h, seller_type, agency, rec.observed_at, rec.observed_at],
    ).fetchone()[0]


def _location_id(conn, rec: RawRecord) -> tuple[int | None, float | None]:
    match = default_gazetteer().match(rec.raw_location)
    if match is None:
        return None, None
    row = conn.execute("SELECT location_id FROM location WHERE slug = ?", [match.slug]).fetchone()
    return (row[0] if row else None), match.confidence


def ingest_record(conn, run_id: int, rec: RawRecord) -> dict:
    """Ingère un RawRecord. Retourne {'listing_id', 'observation_id', 'events': [...]}."""
    sid = source_id(conn, rec.source_code)
    seller_id = _upsert_seller(conn, sid, rec)
    events: list[str] = []

    row = conn.execute(
        """SELECT listing_id, status, last_seen_at FROM listing
           WHERE source_id = ? AND external_id = ?""",
        [sid, rec.external_id],
    ).fetchone()

    if row is None:
        loc_id, geo_conf = _location_id(conn, rec)
        listing_id = conn.execute(
            """INSERT INTO listing (source_id, external_id, url, transaction_type,
                   property_type, title, seller_id, location_id, raw_location, lat, lon,
                   published_at, first_seen_at, last_seen_at, status, geo_confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
               RETURNING listing_id""",
            [sid, rec.external_id, rec.url, rec.transaction_type, rec.property_type,
             rec.title, seller_id, loc_id, rec.raw_location, rec.lat, rec.lon,
             rec.published_at, rec.observed_at, rec.observed_at, geo_conf],
        ).fetchone()[0]
        _add_event(conn, listing_id, "first_seen", rec.observed_at, None, rec.price)
        events.append("first_seen")
        prev_price = None
    else:
        listing_id, status, _last_seen = row
        prev = conn.execute(
            """SELECT price FROM listing_observation
               WHERE listing_id = ? AND price IS NOT NULL
               ORDER BY observed_at DESC, observation_id DESC LIMIT 1""",
            [listing_id],
        ).fetchone()
        prev_price = prev[0] if prev else None
        if status == "disappeared":
            conn.execute(
                "UPDATE listing SET status = 'active', disappeared_at = NULL WHERE listing_id = ?",
                [listing_id],
            )
            _add_event(conn, listing_id, "reappeared", rec.observed_at, prev_price, rec.price)
            events.append("reappeared")
        if (rec.price is not None and prev_price is not None
                and float(rec.price) != float(prev_price)):
            _add_event(conn, listing_id, "price_change", rec.observed_at, prev_price, rec.price)
            events.append("price_change")
        conn.execute(
            "UPDATE listing SET last_seen_at = ?, url = coalesce(?, url) WHERE listing_id = ?",
            [rec.observed_at, rec.url, listing_id],
        )

    flags = quality_flags_for(rec)
    observation_id = conn.execute(
        """INSERT INTO listing_observation (listing_id, run_id, observed_at, price,
               rent_period, charges_included, surface_m2, rooms, bedrooms, bathrooms,
               floor, floors_total, condition, age_years, furnished, attrs, description,
               description_hash, photos_count, photo_urls, raw_ref, confidence, quality_flags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           RETURNING observation_id""",
        [listing_id, run_id, rec.observed_at, rec.price, rec.rent_period,
         rec.charges_included, rec.surface_m2, rec.rooms, rec.bedrooms, rec.bathrooms,
         rec.floor, rec.floors_total, rec.condition, rec.age_years, rec.furnished,
         json.dumps(rec.attrs, ensure_ascii=False) if rec.attrs else None,
         rec.description, rec.description_hash(), rec.photos_count,
         json.dumps(rec.photo_urls, ensure_ascii=False) if rec.photo_urls else None,
         rec.raw_ref, rec.confidence,
         json.dumps(flags) if flags else None],
    ).fetchone()[0]

    return {"listing_id": listing_id, "observation_id": observation_id, "events": events}


def mark_disappeared(
    conn,
    source_code: str,
    seen_external_ids: set[str],
    as_of: datetime,
    transaction_type: str | None = None,
) -> int:
    """À appeler UNIQUEMENT après un run à périmètre complet : les annonces actives
    de la source non revues passent en 'disappeared' (ce qui ne veut PAS dire vendu).
    """
    sid = source_id(conn, source_code)
    query = "SELECT listing_id, external_id FROM listing WHERE source_id = ? AND status = 'active'"
    params: list = [sid]
    if transaction_type:
        query += " AND transaction_type = ?"
        params.append(transaction_type)
    n = 0
    for listing_id, external_id in conn.execute(query, params).fetchall():
        if external_id in seen_external_ids:
            continue
        conn.execute(
            "UPDATE listing SET status = 'disappeared', disappeared_at = ? WHERE listing_id = ?",
            [as_of, listing_id],
        )
        _add_event(conn, listing_id, "disappeared", as_of, None, None)
        n += 1
    return n


def _add_event(conn, listing_id: int, event_type: str, at, old_price, new_price, details=None):
    conn.execute(
        """INSERT INTO listing_event (listing_id, event_type, event_at, old_price, new_price, details)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [listing_id, event_type, at, old_price, new_price,
         json.dumps(details, ensure_ascii=False) if details else None],
    )
