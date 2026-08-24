from datetime import datetime, timezone

from casadata.analytics.comparables import estimate_rent, gross_yield
from casadata.analytics.metrics import market_stats, quartier_stats
from casadata.db import start_run
from casadata.ingest.base import RawRecord
from casadata.ingest.observations import ingest_record

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _seed_rentals(conn, n=8):
    run = start_run(conn, "avito", "http")
    for i in range(n):
        ingest_record(conn, run, RawRecord(
            source_code="avito", external_id=f"R{i}", transaction_type="rent",
            observed_at=T0, price=7_000 + i * 150, rent_period="month",
            surface_m2=90.0 + i, bedrooms=2, rooms=3, raw_location="Maârif",
            property_type="apartment",
        ))


def test_estimate_rent_and_yield(conn):
    _seed_rentals(conn)
    est = estimate_rent(conn, "maarif", 95.0, bedrooms=2)
    assert est.n_comparables >= 3
    assert est.confidence == "high"
    assert 6_000 < est.rent_monthly < 9_500

    y = gross_yield(1_350_000, est.rent_monthly)
    assert 4.0 < y["gross_yield_pct"] < 9.0
    assert y["total_acquisition_cost"] > 1_350_000  # frais inclus


def test_estimate_rent_no_comparables(conn):
    est = estimate_rent(conn, "sidi-moumen", 95.0, bedrooms=2)
    assert est.n_comparables == 0 and est.rent_monthly is None


def test_quartier_stats_excludes_flagged(conn):
    run = start_run(conn, "mubawab", "http")
    for i in range(6):
        ingest_record(conn, run, RawRecord(
            source_code="mubawab", external_id=f"S{i}", transaction_type="sale",
            observed_at=T0, price=1_400_000 + i * 10_000, surface_m2=95.0,
            raw_location="Maârif", property_type="apartment"))
    # une aberration flaggée ppm2_outlier (200 kMAD/m²)
    ingest_record(conn, run, RawRecord(
        source_code="mubawab", external_id="OUT", transaction_type="sale",
        observed_at=T0, price=20_000_000, surface_m2=100.0,
        raw_location="Maârif", property_type="apartment"))
    rows = quartier_stats(conn, "sale", min_obs=3)
    assert len(rows) == 1
    quartier, n, med, *_ = rows[0]
    assert quartier == "Maârif"
    assert n == 6           # l'outlier est exclu des agrégats…
    stats = market_stats(conn)
    assert stats["observations"] == 7  # …mais jamais supprimé de la base


def test_market_stats_counts(conn):
    _seed_rentals(conn, 3)
    stats = market_stats(conn)
    assert stats["listings"] == 3
    assert stats["listings_active"] == 3
    assert ("avito", "rent", 3) in stats["by_source"]
