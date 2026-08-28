"""Dedoublonnage : rattacher les republications d'une meme annonce.

Une agence remet regulierement le meme bien en ligne sous une nouvelle URL. Sans
traitement, le meme logement compte plusieurs fois dans les medianes et gonfle
artificiellement les effectifs par cellule.

Regle appliquee (deux annonces sont un doublon si tout est vrai) :
    meme quartier_norm
    surface a +/- 3 m2
    loyer a +/- 5 %
    similarite des descriptions > 0.85 (rapidfuzz, token_set_ratio)

Les doublons ne sont jamais supprimes : la colonne `duplicate_of` pointe vers la
plus ancienne occurrence (celle de premiere apparition). Les analyses filtrent sur
`duplicate_of IS NULL`, mais le comptage des republications reste possible — c'est
en soi un indicateur de rotation du stock.

    python dedup.py            # marque les doublons
    python dedup.py --rapport  # liste les groupes sans rien ecrire
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from rapidfuzz import fuzz

from db import DB_PATH, connect

TOLERANCE_SURFACE = 3.0     # m2
TOLERANCE_LOYER = 0.05      # 5 %
SEUIL_SIMILARITE = 85.0     # rapidfuzz renvoie 0-100 ; l'enonce parle de 0,85


def _texte(ligne: sqlite3.Row) -> str:
    return f"{ligne['titre'] or ''} {ligne['description'] or ''}".strip()


def sont_doublons(a: sqlite3.Row, b: sqlite3.Row, seuil: float = SEUIL_SIMILARITE) -> bool:
    """Applique la regle. Un champ manquant empeche la conclusion : on ne rattache pas."""
    if not a["quartier_norm"] or a["quartier_norm"] != b["quartier_norm"]:
        return False
    if a["surface_m2"] is None or b["surface_m2"] is None:
        return False
    if abs(a["surface_m2"] - b["surface_m2"]) > TOLERANCE_SURFACE:
        return False
    if a["loyer_mad"] is None or b["loyer_mad"] is None or not a["loyer_mad"]:
        return False
    if abs(a["loyer_mad"] - b["loyer_mad"]) / a["loyer_mad"] > TOLERANCE_LOYER:
        return False

    texte_a, texte_b = _texte(a), _texte(b)
    if not texte_a or not texte_b:
        return False
    return fuzz.token_set_ratio(texte_a, texte_b) > seuil


def marquer_doublons(conn: sqlite3.Connection, seuil: float = SEUIL_SIMILARITE,
                     ecrire: bool = True) -> list[tuple[int, int]]:
    """Compare les annonces par groupe de quartier et marque `duplicate_of`.

    La comparaison est quadratique a l'interieur d'un quartier seulement : l'ordre de
    grandeur (quelques centaines d'annonces par quartier) le permet largement, et cela
    evite de comparer des biens sans rapport.
    """
    lignes = conn.execute(
        "SELECT id, quartier_norm, surface_m2, loyer_mad, titre, description, first_seen "
        "FROM listings WHERE quartier_norm IS NOT NULL AND duplicate_of IS NULL "
        "ORDER BY quartier_norm, first_seen, id"
    ).fetchall()

    par_quartier: dict[str, list[sqlite3.Row]] = {}
    for ligne in lignes:
        par_quartier.setdefault(ligne["quartier_norm"], []).append(ligne)

    paires: list[tuple[int, int]] = []
    for groupe in par_quartier.values():
        originaux: list[sqlite3.Row] = []
        for candidate in groupe:                       # deja triees par anciennete
            original = next((o for o in originaux if sont_doublons(o, candidate, seuil)), None)
            if original is None:
                originaux.append(candidate)
            else:
                paires.append((candidate["id"], original["id"]))

    if ecrire and paires:
        conn.executemany("UPDATE listings SET duplicate_of = ? WHERE id = ?",
                         [(orig, dup) for dup, orig in paires])
        conn.commit()
    return paires


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--rapport", action="store_true", help="n'ecrit rien, liste seulement")
    p.add_argument("--seuil", type=float, default=SEUIL_SIMILARITE)
    args = p.parse_args()

    if not Path(args.db).exists():
        print(f"base introuvable : {args.db}")
        return 1

    conn = connect(args.db)
    paires = marquer_doublons(conn, args.seuil, ecrire=not args.rapport)
    total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"{len(paires)} doublons {'detectes' if args.rapport else 'marques'} "
          f"sur {total} annonces")
    for dup, orig in paires[:20]:
        print(f"  #{dup} -> #{orig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
