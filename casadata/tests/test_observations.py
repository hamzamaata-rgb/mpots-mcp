from datetime import datetime, timedelta, timezone

from casadata.db import start_run
from casadata.ingest.base import RawRecord
from casadata.ingest.observations import ingest_record, mark_disappeared

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def rec(price, at, external_id="A1", **kw):
    defaults = dict(
        source_code="mubawab", external_id=external_id, transaction_type="sale",
        observed_at=at, price=price, surface_m2=95.0, rooms=3, bedrooms=2,
        raw_location="Maârif", property_type="apartment",
        seller_external_id="seller-42", seller_type="agence", agency_name="Agence X",
    )
    defaults.update(kw)
    return RawRecord(**defaults)


def test_price_trajectory_is_preserved(conn):
    """Le scénario du cahier des charges : 1.5M -> 1.45M -> 1.4M -> disparu.
    Chaque étape doit rester une observation distincte."""
    run = start_run(conn, "mubawab", "http")
    r1 = ingest_record(conn, run, rec(1_500_000, T0))
    assert r1["events"] == ["first_seen"]

    r2 = ingest_record(conn, run, rec(1_450_000, T0 + timedelta(days=14)))
    assert "price_change" in r2["events"]

    # passage sans changement: observation ajoutée, pas d'événement prix
    r3 = ingest_record(conn, run, rec(1_450_000, T0 + timedelta(days=20)))
    assert r3["events"] == []

    ingest_record(conn, run, rec(1_400_000, T0 + timedelta(days=30)))

    n_obs = conn.execute("SELECT count(*) FROM listing_observation").fetchone()[0]
    assert n_obs == 4  # JAMAIS de mise à jour destructive

    history = conn.execute(
        "SELECT price FROM price_history ORDER BY observed_at"
    ).fetchall()
    assert [h[0] for h in history] == [1_500_000, 1_450_000, 1_400_000]

    # disparition (annonce non revue dans un run complet)
    gone = mark_disappeared(conn, "mubawab", set(), T0 + timedelta(days=61), "sale")
    assert gone == 1
    status = conn.execute("SELECT status FROM listing").fetchone()[0]
    assert status == "disappeared"

    # réapparition -> événement dédié, même listing
    r5 = ingest_record(conn, run, rec(1_400_000, T0 + timedelta(days=90)))
    assert "reappeared" in r5["events"]
    assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 1

    events = [e[0] for e in conn.execute(
        "SELECT event_type FROM listing_event ORDER BY event_at").fetchall()]
    assert events == ["first_seen", "price_change", "price_change", "disappeared", "reappeared"]


def test_geo_normalisation_applied(conn):
    run = start_run(conn, "mubawab", "http")
    ingest_record(conn, run, rec(1_000_000, T0))
    row = conn.execute(
        """SELECT loc.slug, l.raw_location, l.geo_confidence
           FROM listing l JOIN location loc USING (location_id)"""
    ).fetchone()
    assert row[0] == "maarif"
    assert row[1] == "Maârif"  # le brut est toujours conservé
    assert row[2] > 0.5


def test_seller_privacy(conn):
    """Identifiant plateforme hashé, nom conservé seulement pour les agences."""
    run = start_run(conn, "mubawab", "http")
    ingest_record(conn, run, rec(1_000_000, T0))
    ingest_record(conn, run, rec(2_000_000, T0, external_id="A2",
                                 seller_external_id="private-99",
                                 seller_type="particulier", agency_name="Doit Disparaître"))
    rows = conn.execute(
        "SELECT external_hash, seller_type, agency_name FROM seller ORDER BY seller_id"
    ).fetchall()
    assert rows[0][1] == "agence" and rows[0][2] == "Agence X"
    assert rows[1][1] == "particulier" and rows[1][2] is None
    assert "seller-42" not in rows[0][0] and "private-99" not in rows[1][0]


def test_lifecycle_view(conn):
    run = start_run(conn, "mubawab", "http")
    ingest_record(conn, run, rec(2_000_000, T0))
    ingest_record(conn, run, rec(1_800_000, T0 + timedelta(days=40)))
    mark_disappeared(conn, "mubawab", set(), T0 + timedelta(days=50), "sale")
    row = conn.execute(
        """SELECT n_price_changes, price_drop_pct, days_on_market
           FROM listing_lifecycle"""
    ).fetchone()
    assert row[0] == 1
    assert abs(row[1] - (-0.10)) < 1e-9
    assert row[2] == 50


def test_quality_flags_stored_not_dropped(conn):
    run = start_run(conn, "mubawab", "http")
    bad = rec(500, T0, external_id="weird", surface_m2=2.0)  # prix et surface absurdes
    ingest_record(conn, run, bad)
    flags = conn.execute("SELECT quality_flags FROM listing_observation").fetchone()[0]
    assert "price_out_of_range" in flags and "surface_out_of_range" in flags
    assert conn.execute("SELECT count(*) FROM listing_observation").fetchone()[0] == 1
