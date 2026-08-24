"""Harvest historique via la Wayback Machine (Internet Archive).

L'API CDX liste les snapshots archivés d'un motif d'URL ; on récupère ensuite
les pages archivées (https://web.archive.org/web/<ts>/<url>) et on les parse
avec les mêmes parseurs que le live. Les observations sont datées du
timestamp DU SNAPSHOT (pas d'aujourd'hui) avec confidence 0.7.

C'est la voie principale vers un historique 2012-2026 au niveau annonce en
dehors des datasets déjà constitués. Politesse : archive.org tolère un crawl
lent ; on garde le PoliteClient (4s+) et on borne chaque session.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..db import finish_run, start_run
from ..ingest.observations import ingest_record
from .http import PoliteClient, RawStore
from .portal import PORTALS, parse_listing_page

CDX_URL = (
    "https://web.archive.org/cdx/search/cdx"
    "?url={pattern}&output=json&filter=statuscode:200&collapse=digest"
    "&from={from_ts}&to={to_ts}&limit={limit}"
)

# Motifs de pages annonces par portail (URLs historiques).
CDX_PATTERNS = {
    "mubawab": ["mubawab.ma/fr/a/*"],
    "avito": ["avito.ma/fr/*htm"],
    "sarouty": ["sarouty.ma/fr/annonce/*"],
}


def snapshot_index(client: PoliteClient, pattern: str, from_year: int,
                   to_year: int, limit: int = 5000) -> list[tuple[str, str]]:
    """Retourne [(timestamp, original_url), ...] depuis l'API CDX."""
    url = CDX_URL.format(pattern=pattern, from_ts=f"{from_year}0101",
                         to_ts=f"{to_year}1231", limit=limit)
    resp = client.get(url)
    if resp is None:
        return []
    try:
        rows = json.loads(resp.text)
    except json.JSONDecodeError:
        return []
    if not rows:
        return []
    header, *data = rows
    idx_ts = header.index("timestamp")
    idx_orig = header.index("original")
    return [(r[idx_ts], r[idx_orig]) for r in data]


def _guess_transaction_type(url: str, html: str) -> str:
    text = (url + " " + html[:5000]).lower()
    if any(k in text for k in ("louer", "location", "rent", "كراء")):
        return "rent"
    return "sale"


def harvest(conn, portal_code: str, from_year: int = 2012, to_year: int | None = None,
            limit: int = 500) -> dict:
    """Récupère jusqu'à `limit` snapshots d'annonces du portail et les ingère."""
    to_year = to_year or datetime.now(timezone.utc).year
    cfg = PORTALS[portal_code]
    client = PoliteClient()
    store = RawStore("wayback", f"{portal_code}-{from_year}-{to_year}")
    run_id = start_run(conn, "wayback", method="wayback",
                       scope=f"{portal_code}/{from_year}-{to_year}", raw_path=str(store.path))
    parsed = failed = 0
    try:
        snapshots: list[tuple[str, str]] = []
        for pattern in CDX_PATTERNS.get(portal_code, []):
            snapshots.extend(snapshot_index(client, pattern, from_year, to_year, limit))
        # une seule capture par URL et par mois (économie de requêtes)
        seen_keys: set[str] = set()
        for ts, original in snapshots:
            if parsed + failed >= limit:
                break
            key = original + ts[:6]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            snap_url = f"https://web.archive.org/web/{ts}/{original}"
            resp = client.get(snap_url)
            if resp is None:
                failed += 1
                continue
            html = resp.text
            raw_ref = store.write(snap_url, html, resp.status_code, {"ts": ts, "original": original})
            observed_at = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            # retire la barre d'outils wayback pour ne pas polluer le parsing
            html = re.sub(r"<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->",
                          "", html, flags=re.DOTALL)
            rec = parse_listing_page(cfg, original, html,
                                     _guess_transaction_type(original, html),
                                     observed_at, raw_ref)
            if rec is None or (rec.price is None and rec.surface_m2 is None):
                failed += 1
                continue
            rec.confidence = 0.7  # source archive : échantillon biaisé, parsing d'époque
            ingest_record(conn, run_id, rec)
            parsed += 1
        finish_run(conn, run_id, "success" if parsed else "partial",
                   client.stats["fetched"], parsed, failed)
        return {"run_id": run_id, "parsed": parsed, "failed": failed, **client.stats}
    except Exception as exc:
        finish_run(conn, run_id, "failed", client.stats["fetched"], parsed, failed, repr(exc))
        raise
    finally:
        store.close()
        client.close()
