"""Creation et maintenance de la base SQLite.

Le referentiel des quartiers a pour source de verite le fichier `quartiers_seed.csv`,
pas la base : on edite le CSV (versionne, relisible en diff), puis on resynchronise.

    python db.py --init             # cree data/casa_rentals.db et charge le referentiel
    python db.py --sync-quartiers   # reapplique le CSV apres une revision manuelle
    python db.py --stats            # etat de la base
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from normalize import Referentiel

RACINE = Path(__file__).resolve().parent
DB_PATH = RACINE / "data" / "casa_rentals.db"
SCHEMA_PATH = RACINE / "schema.sql"
QUARTIERS_CSV = RACINE / "quartiers_seed.csv"


def connect(chemin: str | Path = DB_PATH) -> sqlite3.Connection:
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(chemin: str | Path = DB_PATH, schema: str | Path = SCHEMA_PATH) -> sqlite3.Connection:
    """Cree les tables si elles n'existent pas. Reexecutable sans effet de bord."""
    conn = connect(chemin)
    conn.executescript(Path(schema).read_text(encoding="utf-8"))
    conn.commit()
    return conn


def sync_quartiers(conn: sqlite3.Connection, csv_path: str | Path = QUARTIERS_CSV) -> dict:
    """Applique le CSV au referentiel en base. Idempotent.

    Ne supprime rien : les quartiers presents en base mais absents du CSV sont
    seulement signales, pour ne jamais orphaliner des lignes de `listings`.
    """
    ref = Referentiel.depuis_csv(csv_path)
    avant = {r["nom"] for r in conn.execute("SELECT nom FROM quartiers")}
    for q in ref.quartiers:
        conn.execute(
            """
            INSERT INTO quartiers (nom, aliases, arrondissement, segment, perimetre)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(nom) DO UPDATE SET
                aliases = excluded.aliases,
                arrondissement = excluded.arrondissement,
                segment = excluded.segment,
                perimetre = excluded.perimetre
            """,
            (q.nom, "|".join(q.aliases), q.arrondissement, q.segment, q.perimetre),
        )
    conn.commit()
    apres = {q.nom for q in ref.quartiers}
    return {
        "total_csv": len(ref.quartiers),
        "ajoutes": sorted(apres - avant),
        "orphelins_en_base": sorted(avant - apres),
    }


def charger_referentiel(conn: sqlite3.Connection) -> Referentiel:
    """Construit un Referentiel depuis la base (source unique pour la collecte)."""
    from normalize import Quartier

    quartiers = [
        Quartier(
            nom=r["nom"],
            aliases=tuple(a for a in (r["aliases"] or "").split("|") if a),
            arrondissement=r["arrondissement"],
            segment=r["segment"],
            perimetre=r["perimetre"] or "casablanca",
        )
        for r in conn.execute("SELECT * FROM quartiers ORDER BY nom")
    ]
    return Referentiel(quartiers)


def stats(conn: sqlite3.Connection) -> dict:
    def scalaire(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "quartiers": scalaire("SELECT COUNT(*) FROM quartiers"),
        "listings": scalaire("SELECT COUNT(*) FROM listings"),
        "listings_qualite_2plus": scalaire("SELECT COUNT(*) FROM listings WHERE qualite >= 2"),
        "snapshots": scalaire("SELECT COUNT(*) FROM snapshots"),
        "runs": scalaire("SELECT COUNT(*) FROM runs"),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--init", action="store_true", help="cree la base et charge le referentiel")
    p.add_argument("--sync-quartiers", action="store_true", help="reapplique quartiers_seed.csv")
    p.add_argument("--stats", action="store_true", help="affiche l'etat de la base")
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args()

    if args.init:
        conn = init_db(args.db)
        rapport = sync_quartiers(conn)
        print(f"Base initialisee : {args.db}")
        print(f"Referentiel : {rapport['total_csv']} quartiers, "
              f"{len(rapport['ajoutes'])} ajoutes")
    elif args.sync_quartiers:
        conn = connect(args.db)
        rapport = sync_quartiers(conn)
        print(f"Referentiel resynchronise : {rapport['total_csv']} quartiers")
        if rapport["ajoutes"]:
            print("  ajoutes  :", ", ".join(rapport["ajoutes"]))
        if rapport["orphelins_en_base"]:
            print("  en base mais absents du CSV (non supprimes) :",
                  ", ".join(rapport["orphelins_en_base"]))
    elif args.stats:
        conn = connect(args.db)
        for cle, valeur in stats(conn).items():
            print(f"{cle:24} {valeur}")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
