"""Collecteur de portail générique + configurations Mubawab / Avito / Sarouty.

Le squelette est complet et conforme (robots.txt, politesse, raw store,
cycle d'observation, détection de disparition). Les regex d'extraction des
liens d'annonces sont fournies pour la structure d'URL connue de chaque
portail et DOIVENT être validées lors du premier run live (`--limit 3`),
l'environnement de développement n'ayant pas accès à ces domaines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..db import finish_run, start_run
from ..ingest.base import RawRecord
from ..ingest.observations import ingest_record, mark_disappeared
from . import parsing
from .http import PoliteClient, RawStore


@dataclass
class PortalConfig:
    source_code: str
    # modèles de pages de résultats par (transaction_type -> liste d'URLs paginées)
    search_urls: dict[str, list[str]]        # {'sale': [...page {page}...], 'rent': [...]}
    listing_link_re: str                     # regex -> URLs de pages annonces
    external_id_re: str                      # regex -> id dans l'URL d'annonce
    max_pages: int = 50
    confidence: float = 1.0
    notes: str = ""
    default_property_type: str | None = None
    extra: dict = field(default_factory=dict)


MUBAWAB = PortalConfig(
    source_code="mubawab",
    search_urls={
        "sale": [
            "https://www.mubawab.ma/fr/st/casablanca/appartements-a-vendre:p:{page}",
            "https://www.mubawab.ma/fr/st/casablanca/maisons-a-vendre:p:{page}",
            "https://www.mubawab.ma/fr/st/casablanca/villas-et-maisons-de-luxe-a-vendre:p:{page}",
        ],
        "rent": [
            "https://www.mubawab.ma/fr/st/casablanca/appartements-a-louer:p:{page}",
            "https://www.mubawab.ma/fr/st/casablanca/maisons-a-louer:p:{page}",
        ],
    },
    listing_link_re=r'https?://www\.mubawab\.ma/fr/a/\d+[^"\'\s<>]*',
    external_id_re=r"/fr/a/(\d+)",
    notes="Vertical leader; pages annonces avec GPS et tags structurés.",
)

AVITO = PortalConfig(
    source_code="avito",
    search_urls={
        "sale": ["https://www.avito.ma/fr/casablanca/appartements-%C3%A0_vendre?o={page}",
                 "https://www.avito.ma/fr/casablanca/maisons_et_villas-%C3%A0_vendre?o={page}"],
        "rent": ["https://www.avito.ma/fr/casablanca/appartements-%C3%A0_louer?o={page}",
                 "https://www.avito.ma/fr/casablanca/maisons_et_villas-%C3%A0_louer?o={page}"],
    },
    listing_link_re=r'https?://www\.avito\.ma/fr/[^"\'\s<>]+_\d+\.htm',
    external_id_re=r"_(\d+)\.htm",
    notes="Généraliste, volume max ; état JSON embarqué dans les pages (à parser au run live).",
)

SAROUTY = PortalConfig(
    source_code="sarouty",
    search_urls={
        "sale": ["https://www.sarouty.ma/fr/recherche?c=1&l=35&page={page}"],
        "rent": ["https://www.sarouty.ma/fr/recherche?c=2&l=35&page={page}"],
    },
    listing_link_re=r'https?://www\.sarouty\.ma/fr/annonce/[^"\'\s<>]+',
    external_id_re=r"-(\d+)\.html|/annonce/([\w-]+)",
    confidence=1.0,
    notes="Property Finder Maroc ; l=35 ~ Casablanca, à confirmer au premier run.",
)

PORTALS = {c.source_code: c for c in (MUBAWAB, AVITO, SAROUTY)}


def parse_listing_page(cfg: PortalConfig, url: str, html: str,
                       transaction_type: str, observed_at: datetime,
                       raw_ref: str | None = None) -> RawRecord | None:
    """Page annonce -> RawRecord (couches génériques ; hors-ligne testable)."""
    m = re.search(cfg.external_id_re, url)
    external_id = next((g for g in (m.groups() if m else []) if g), None) if m else None
    if external_id is None:
        external_id = RawRecord.id_from_url(url)

    blocks = parsing.extract_jsonld(html)
    meta = parsing.extract_meta(html)
    text = re.sub(r"<[^>]+>", " ", html)
    features = parsing.extract_features(text)
    price = parsing.jsonld_offer_price(blocks) or parsing.extract_price_mad(text)
    lat, lon = parsing.jsonld_geo(blocks)

    title = meta.get("og:title")
    description = meta.get("og:description") or meta.get("description")
    raw_location = None
    if title:
        # les titres portails finissent souvent par '... à <quartier>' / '- <quartier>'
        mloc = re.search(r"(?:à|a|in|-)\s+([A-Za-zÀ-ÿ' ]{3,40})\s*$", title)
        if mloc:
            raw_location = mloc.group(1).strip()

    attrs = features.pop("attrs", {})
    return RawRecord(
        source_code=cfg.source_code,
        external_id=str(external_id),
        transaction_type=transaction_type,
        observed_at=observed_at,
        price=price,
        rent_period="month" if transaction_type == "rent" else None,
        raw_location=raw_location,
        lat=lat, lon=lon,
        url=url,
        title=title,
        description=description,
        attrs=attrs,
        confidence=cfg.confidence,
        raw_ref=raw_ref,
        **{k: v for k, v in features.items()
           if k in ("surface_m2", "rooms", "bedrooms", "bathrooms", "floor")},
    )


def crawl_portal(conn, source_code: str, transaction_type: str,
                 max_pages: int | None = None, limit: int | None = None,
                 mark_missing: bool = False) -> dict:
    """Run complet : pages de résultats -> pages annonces -> ingestion.

    mark_missing=True uniquement pour un run à périmètre complet (sinon les
    annonces non visitées seraient faussement 'disparues').
    """
    cfg = PORTALS[source_code]
    client = PoliteClient()
    store = RawStore(source_code)
    run_id = start_run(conn, source_code, method="http",
                       scope=f"casablanca/{transaction_type}", raw_path=str(store.path))
    observed_at = datetime.now(timezone.utc)
    seen: set[str] = set()
    parsed = failed = 0
    try:
        listing_urls: list[str] = []
        for template in cfg.search_urls.get(transaction_type, []):
            for page in range(1, (max_pages or cfg.max_pages) + 1):
                resp = client.get(template.format(page=page))
                if resp is None:
                    break
                store.write(str(resp.url), resp.text, resp.status_code, {"kind": "search"})
                found = re.findall(cfg.listing_link_re, resp.text)
                if not found:
                    break
                listing_urls.extend(found)
                if limit and len(set(listing_urls)) >= limit:
                    break
            if limit and len(set(listing_urls)) >= limit:
                break

        for url in dict.fromkeys(listing_urls):  # dédoublonne, ordre conservé
            if limit and parsed >= limit:
                break
            resp = client.get(url)
            if resp is None:
                failed += 1
                continue
            raw_ref = store.write(str(resp.url), resp.text, resp.status_code, {"kind": "listing"})
            rec = parse_listing_page(cfg, url, resp.text, transaction_type,
                                     observed_at, raw_ref)
            if rec is None or rec.price is None and rec.surface_m2 is None:
                failed += 1
                continue
            ingest_record(conn, run_id, rec)
            seen.add(rec.external_id)
            parsed += 1

        disappeared = 0
        if mark_missing and not limit:
            disappeared = mark_disappeared(conn, source_code, seen, observed_at,
                                           transaction_type)
        status = "success" if failed == 0 else "partial"
        notes = f"disappeared={disappeared}; robots_blocked={client.stats['blocked_robots']}"
        finish_run(conn, run_id, status, client.stats["fetched"], parsed, failed, notes)
        return {"run_id": run_id, "parsed": parsed, "failed": failed,
                "disappeared": disappeared, **client.stats}
    except Exception as exc:
        finish_run(conn, run_id, "failed", client.stats["fetched"], parsed, failed, repr(exc))
        raise
    finally:
        store.close()
        client.close()
