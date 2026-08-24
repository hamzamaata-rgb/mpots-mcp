import json
from datetime import datetime

from casadata.ingest.datasets import ingest_dataset


CSV = """prix,surface,quartier,chambres,type,date_annonce
1500000,100,Maarif,2,appartement,2020-03-15
"1 250 000",85,Gauthier,2,appartement,2020-06-01
980000,72,Ain Chock,,appartement,
"""


def test_generic_csv_ingestion(conn, tmp_path):
    csv_path = tmp_path / "seed.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    manifest = {
        "source_code": "generic_csv",
        "original_url": "https://example.org/dataset",
        "license": "CC-BY-4.0",
        "period_start": "2019-01-01",
        "period_end": "2021-12-31",
        "confidence": 0.8,
        "transaction_type": "sale",
        "observed_at_column": "date_annonce",
        "columns": {
            "price": "prix", "surface_m2": "surface", "raw_location": "quartier",
            "bedrooms": "chambres", "property_type": "type",
        },
    }
    (tmp_path / "seed.csv.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = ingest_dataset(conn, csv_path)
    assert result["parsed"] == 3 and result["failed"] == 0

    # prix avec espaces correctement parsé
    prices = sorted(r[0] for r in conn.execute(
        "SELECT price FROM listing_observation").fetchall())
    assert prices == [980_000.0, 1_250_000.0, 1_500_000.0]

    # date par ligne quand dispo, milieu de période sinon
    dates = sorted(r[0] for r in conn.execute(
        "SELECT observed_at FROM listing_observation").fetchall())
    assert dates[0] == datetime(2020, 3, 15)
    assert dates[-1].year == 2020  # milieu 2019-2021

    # provenance enregistrée
    manifest_row = conn.execute(
        "SELECT license, row_count, original_url FROM dataset_manifest").fetchone()
    assert manifest_row[0] == "CC-BY-4.0" and manifest_row[1] == 3

    # geo normalisée
    slugs = {r[0] for r in conn.execute(
        "SELECT loc.slug FROM listing l JOIN location loc USING (location_id)").fetchall()}
    assert {"maarif", "gauthier", "ain-chock"} <= slugs


def test_manifest_required(conn, tmp_path):
    csv_path = tmp_path / "orphan.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    try:
        ingest_dataset(conn, csv_path)
        raise AssertionError("un import sans manifest doit échouer")
    except FileNotFoundError:
        pass
