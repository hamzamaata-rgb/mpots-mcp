"""CLI casadata.

  casadata init                         initialise la base + gazetteer
  casadata collect portal mubawab sale  collecte un portail (live)
  casadata collect wayback mubawab      harvest historique archive.org
  casadata ingest-dataset f.csv         importe un dataset historique (manifest requis)
  casadata ingest-aggregates ipai f.csv importe des séries agrégées (IPAI, référentiels)
  casadata dedupe                       recalcul des liens annonces<->biens
  casadata stats                        état de la base
  casadata quartiers [sale|rent]        prix/m² par quartier
  casadata estimate-rent maarif 95 2    loyer estimé par comparables
  casadata export                       export Parquet
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import SETTINGS
from .db import connect
from .geo.casablanca import sync_locations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casadata", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="chemin de la base (défaut: data/casadata.duckdb)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    p = sub.add_parser("collect")
    p.add_argument("mode", choices=["portal", "wayback"])
    p.add_argument("source", help="mubawab | avito | sarouty")
    p.add_argument("transaction", nargs="?", choices=["sale", "rent"], default="sale")
    p.add_argument("--limit", type=int, help="nb max d'annonces (test)")
    p.add_argument("--max-pages", type=int)
    p.add_argument("--mark-missing", action="store_true",
                   help="run complet: marque 'disappeared' les annonces non revues")
    p.add_argument("--from-year", type=int, default=2012, help="(wayback)")
    p.add_argument("--to-year", type=int, help="(wayback)")

    p = sub.add_parser("ingest-dataset")
    p.add_argument("csv_path")

    p = sub.add_parser("ingest-aggregates")
    p.add_argument("source", help="ipai | agenz | yakeey | hcp")
    p.add_argument("csv_path")

    sub.add_parser("dedupe")
    sub.add_parser("stats")

    p = sub.add_parser("quartiers")
    p.add_argument("transaction", nargs="?", choices=["sale", "rent"], default="sale")

    p = sub.add_parser("estimate-rent")
    p.add_argument("quartier_slug")
    p.add_argument("surface", type=float)
    p.add_argument("bedrooms", type=int, nargs="?")
    p.add_argument("--price", type=float, help="prix demandé -> calcule aussi le rendement brut")

    sub.add_parser("export")

    args = parser.parse_args(argv)
    SETTINGS.ensure_dirs()
    conn = connect(args.db)

    if args.cmd == "init":
        n = sync_locations(conn)
        print(f"Base initialisée: {args.db or SETTINGS.db_path} — {n} localisations gazetteer.")

    elif args.cmd == "collect":
        sync_locations(conn)
        if args.mode == "portal":
            from .collect.portal import crawl_portal
            result = crawl_portal(conn, args.source, args.transaction,
                                  max_pages=args.max_pages, limit=args.limit,
                                  mark_missing=args.mark_missing)
        else:
            from .collect.wayback import harvest
            result = harvest(conn, args.source, from_year=args.from_year,
                             to_year=args.to_year, limit=args.limit or 500)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.cmd == "ingest-dataset":
        sync_locations(conn)
        from .ingest.datasets import ingest_dataset
        print(json.dumps(ingest_dataset(conn, args.csv_path), ensure_ascii=False, indent=2))

    elif args.cmd == "ingest-aggregates":
        from .collect.institutional import ingest_aggregates_csv
        print(json.dumps(ingest_aggregates_csv(conn, args.source, args.csv_path),
                         ensure_ascii=False, indent=2))

    elif args.cmd == "dedupe":
        from .dedup.matcher import run_dedup
        print(json.dumps(run_dedup(conn), ensure_ascii=False, indent=2))

    elif args.cmd == "stats":
        from .analytics.metrics import market_stats
        stats = market_stats(conn)
        by_source = stats.pop("by_source")
        date_range = stats.pop("date_range")
        for k, v in stats.items():
            print(f"{k:20} {v}")
        print(f"{'période observée':20} {date_range[0]} -> {date_range[1]}")
        for code, tx, n in by_source:
            print(f"  {code:22} {tx:5} {n}")

    elif args.cmd == "quartiers":
        from .analytics.metrics import quartier_stats
        rows = quartier_stats(conn, args.transaction)
        unit = "MAD/m²" if args.transaction == "sale" else "MAD/m²/mois"
        print(f"{'quartier':28} {'n':>5} {'médian':>9} {'p25':>8} {'p75':>8}  ({unit})")
        for q, n, med, p25, p75, _surf in rows:
            print(f"{q:28} {n:>5} {med:>9} {p25:>8} {p75:>8}")

    elif args.cmd == "estimate-rent":
        from .analytics.comparables import estimate_rent, gross_yield
        est = estimate_rent(conn, args.quartier_slug, args.surface, args.bedrooms)
        out = est.__dict__
        if args.price and est.rent_monthly:
            out["yield"] = gross_yield(args.price, est.rent_monthly)
        print(json.dumps(out, ensure_ascii=False, indent=2))

    elif args.cmd == "export":
        from .analytics.metrics import export_parquet
        for path in export_parquet(conn, SETTINGS.export_dir):
            print(path)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
