from datetime import datetime, timezone

from casadata.db import start_run
from casadata.dedup.matcher import run_dedup, score_pair, ListingFacts
from casadata.ingest.base import RawRecord
from casadata.ingest.observations import ingest_record

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)

DESC = ("Magnifique appartement lumineux de 95m2 au coeur du Maarif, "
        "troisième étage avec ascenseur, deux chambres, salon, cuisine équipée, "
        "proche commerces et transports")


def _ingest(conn, source, external_id, **kw):
    run = start_run(conn, source, "http")
    defaults = dict(
        source_code=source, external_id=external_id, transaction_type="sale",
        observed_at=T0, price=1_350_000, surface_m2=95.0, rooms=3, bedrooms=2,
        floor=3, raw_location="Maârif", property_type="apartment", description=DESC,
    )
    defaults.update(kw)
    return ingest_record(conn, run, RawRecord(**defaults))


def test_same_description_cross_portal_merged(conn):
    """Même bien publié sur deux portails (description identique) -> un seul property."""
    _ingest(conn, "mubawab", "M1")
    _ingest(conn, "avito", "AV1", price=1_360_000)
    stats = run_dedup(conn)
    assert stats["listings"] == 2
    assert stats["properties"] == 1


def test_similar_but_not_same_is_not_merged(conn):
    """Caractéristiques proches mais descriptions et prix différents:
    on ne fusionne PAS agressivement."""
    _ingest(conn, "mubawab", "M1")
    _ingest(conn, "avito", "AV2", price=1_150_000, surface_m2=98.0, floor=1,
            description="Appartement à vendre quartier Maarif bien situé titre foncier")
    stats = run_dedup(conn)
    assert stats["properties"] == 2


def test_incompatible_surface_scores_zero():
    a = ListingFacts(1, 1, "sale", "apartment", 5, None, None, 1_000_000, 95.0,
                     3, 2, 3, None, DESC, None)
    b = ListingFacts(2, 2, "sale", "apartment", 5, None, None, 1_000_000, 140.0,
                     3, 2, 3, None, DESC, None)
    assert score_pair(a, b) == 0.0


def test_rent_and_sale_never_merged():
    a = ListingFacts(1, 1, "sale", "apartment", 5, None, None, 1_000_000, 95.0,
                     3, 2, 3, "h1", DESC, None)
    b = ListingFacts(2, 2, "rent", "apartment", 5, None, None, 7_000, 95.0,
                     3, 2, 3, "h1", DESC, None)
    assert score_pair(a, b) == 0.0


def test_convergent_signals_auto_link():
    """Surface quasi identique + prix proche + même étage/chambres + geo 60m
    + agence identique -> lien automatique."""
    a = ListingFacts(1, 1, "sale", "apartment", 5, 33.5850, -7.6320, 1_350_000,
                     95.0, 3, 2, 3, "hA", DESC, "Agence X")
    b = ListingFacts(2, 2, "sale", "apartment", 5, 33.5853, -7.6325, 1_340_000,
                     94.0, 3, 2, 3, "hB", DESC + " réf 4521", "Agence X")
    assert score_pair(a, b) >= 0.80


def test_dedup_idempotent(conn):
    _ingest(conn, "mubawab", "M1")
    _ingest(conn, "avito", "AV1")
    s1 = run_dedup(conn)
    s2 = run_dedup(conn)
    assert s1["properties"] == s2["properties"]
