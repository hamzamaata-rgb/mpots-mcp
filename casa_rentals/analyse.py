"""Analyse en coupe : couverture, loyers au m2 par cellule, durees de mise en ligne.

La logique vit ici plutot que dans le notebook : elle est ainsi testable, rejouable en
ligne de commande, et le notebook se contente de l'appeler et de tracer.

    python analyse.py                 # tableau de couverture + cellules sous le seuil
    python analyse.py --cellules      # loyer median au m2 par cellule
    python analyse.py --durees        # durees de mise en ligne (si la periode le permet)

Regle transversale : **toutes les sorties filtrent sur `qualite >= 2`**, excluent les
doublons (`duplicate_of IS NULL`), les exclusions analytiques (vente, courte duree) et,
par defaut, les quartiers hors commune de Casablanca.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from db import DB_PATH, connect

QUALITE_MIN = 2
SEUIL_MEDIANE = 30      # n minimal avant de publier une mediane
SEUIL_IC = 80           # n minimal pour un intervalle de confiance credible
TRIMESTRES_MIN_DUREES = 2   # la duree de mise en ligne n'a pas de sens avant

TYPOLOGIES = ["studio_T2", "T3", "T4+"]


# --------------------------------------------------------------------------------------
# Chargement
# --------------------------------------------------------------------------------------

def typologie(nb_pieces: float | None, nb_chambres: float | None) -> str | None:
    """Meme regle que normalize.derive_typologie, appliquee en vectoriel."""
    from normalize import derive_typologie
    pieces = int(nb_pieces) if pd.notna(nb_pieces) else None
    chambres = int(nb_chambres) if pd.notna(nb_chambres) else None
    return derive_typologie(pieces, chambres)


def charger(
    conn: sqlite3.Connection,
    qualite_min: int = QUALITE_MIN,
    perimetre: str | None = "casablanca",
    inclure_doublons: bool = False,
) -> pd.DataFrame:
    """Charge les annonces exploitables, jointes au referentiel des quartiers.

    Renvoie un DataFrame vide (mais aux bonnes colonnes) si la base ne contient encore
    rien : les notebooks doivent pouvoir s'executer avant la premiere collecte.
    """
    conditions = ["l.qualite >= ?", "l.exclusion IS NULL", "l.loyer_mad IS NOT NULL",
                  "l.surface_m2 IS NOT NULL"]
    params: list = [qualite_min]
    if not inclure_doublons:
        conditions.append("l.duplicate_of IS NULL")
    if perimetre:
        conditions.append("COALESCE(q.perimetre, 'casablanca') = ?")
        params.append(perimetre)

    df = pd.read_sql_query(
        f"""
        SELECT l.id, l.source, l.quartier_norm, l.surface_m2, l.nb_pieces, l.nb_chambres,
               l.etage, l.meuble, l.is_pro, l.loyer_mad, l.first_seen, l.last_seen,
               l.statut, l.qualite, l.date_publication,
               q.arrondissement, q.segment, q.perimetre
        FROM listings l
        LEFT JOIN quartiers q ON q.nom = l.quartier_norm
        WHERE {' AND '.join(conditions)}
        """,
        conn, params=params,
    )

    if df.empty:
        for colonne in ("typologie", "loyer_m2"):
            df[colonne] = pd.Series(dtype="object" if colonne == "typologie" else "float64")
        return df

    df["typologie"] = df.apply(lambda r: typologie(r["nb_pieces"], r["nb_chambres"]), axis=1)
    df["loyer_m2"] = df["loyer_mad"] / df["surface_m2"]
    df["trimestre"] = pd.PeriodIndex(pd.to_datetime(df["first_seen"], errors="coerce"),
                                     freq="Q").astype(str)
    return df


# --------------------------------------------------------------------------------------
# Couverture
# --------------------------------------------------------------------------------------

def table_couverture(df: pd.DataFrame) -> pd.DataFrame:
    """Effectif par cellule quartier x typologie. C'est le tableau qui pilote tout :
    une mediane ne se publie pas sous 30 observations."""
    if df.empty:
        return pd.DataFrame(columns=TYPOLOGIES, index=pd.Index([], name="quartier_norm"))
    table = (df.dropna(subset=["typologie"])
               .pivot_table(index="quartier_norm", columns="typologie", values="id",
                            aggfunc="count", fill_value=0))
    for t in TYPOLOGIES:
        if t not in table.columns:
            table[t] = 0
    table = table[TYPOLOGIES]
    table["total"] = table.sum(axis=1)
    return table.sort_values("total", ascending=False)


def cellules_sous_seuil(df: pd.DataFrame, seuil: int = SEUIL_MEDIANE) -> pd.DataFrame:
    """Cellules dont l'effectif interdit de publier une mediane."""
    table = table_couverture(df)
    if table.empty:
        return pd.DataFrame(columns=["quartier_norm", "typologie", "n", "manque"])
    lignes = [
        {"quartier_norm": quartier, "typologie": t, "n": int(table.loc[quartier, t]),
         "manque": seuil - int(table.loc[quartier, t])}
        for quartier in table.index for t in TYPOLOGIES
        if table.loc[quartier, t] < seuil
    ]
    return (pd.DataFrame(lignes).sort_values(["manque", "quartier_norm"])
            .reset_index(drop=True))


def diagnostic_couverture(df: pd.DataFrame) -> dict:
    table = table_couverture(df)
    cellules = 0 if table.empty else len(table) * len(TYPOLOGIES)
    if table.empty:
        return {"observations": 0, "cellules": 0, "publiables": 0, "avec_ic": 0,
                "sous_seuil": 0}
    valeurs = table[TYPOLOGIES].values
    return {
        "observations": int(len(df)),
        "cellules": cellules,
        "publiables": int((valeurs >= SEUIL_MEDIANE).sum()),
        "avec_ic": int((valeurs >= SEUIL_IC).sum()),
        "sous_seuil": int((valeurs < SEUIL_MEDIANE).sum()),
    }


# --------------------------------------------------------------------------------------
# Loyers par cellule
# --------------------------------------------------------------------------------------

def stats_par_cellule(df: pd.DataFrame, seuil: int = SEUIL_MEDIANE,
                      masquer_sous_seuil: bool = True) -> pd.DataFrame:
    """Loyer median au m2 par cellule, avec quartiles, IQR et effectif.

    Les cellules sous le seuil sont conservees mais leurs statistiques sont mises a NaN
    quand `masquer_sous_seuil` : on voit qu'elles existent et pourquoi elles sont vides,
    plutot que de publier une mediane sur 4 observations.
    """
    colonnes = ["quartier_norm", "typologie", "n", "loyer_m2_median", "q1", "q3", "iqr",
                "loyer_median", "surface_mediane", "publiable"]
    if df.empty:
        return pd.DataFrame(columns=colonnes)

    groupes = df.dropna(subset=["typologie"]).groupby(["quartier_norm", "typologie"])
    table = groupes.agg(
        n=("id", "count"),
        loyer_m2_median=("loyer_m2", "median"),
        q1=("loyer_m2", lambda s: s.quantile(0.25)),
        q3=("loyer_m2", lambda s: s.quantile(0.75)),
        loyer_median=("loyer_mad", "median"),
        surface_mediane=("surface_m2", "median"),
    ).reset_index()

    table["iqr"] = table["q3"] - table["q1"]
    table["publiable"] = table["n"] >= seuil
    if masquer_sous_seuil:
        a_masquer = ~table["publiable"]
        table.loc[a_masquer, ["loyer_m2_median", "q1", "q3", "iqr", "loyer_median",
                              "surface_mediane"]] = pd.NA
    return table[colonnes].sort_values(["quartier_norm", "typologie"]).reset_index(drop=True)


def stats_par_segment(df: pd.DataFrame) -> pd.DataFrame:
    """Repli quand les cellules quartier x typologie sont trop fines.

    Agreger au segment fait perdre du detail mais rend les effectifs exploitables ;
    c'est le compromis a documenter plutot qu'a subir.
    """
    if df.empty:
        return pd.DataFrame(columns=["segment", "typologie", "n", "loyer_m2_median"])
    return (df.dropna(subset=["typologie", "segment"])
              .groupby(["segment", "typologie"])
              .agg(n=("id", "count"), loyer_m2_median=("loyer_m2", "median"),
                   q1=("loyer_m2", lambda s: s.quantile(0.25)),
                   q3=("loyer_m2", lambda s: s.quantile(0.75)))
              .reset_index())


# --------------------------------------------------------------------------------------
# Durees de mise en ligne
# --------------------------------------------------------------------------------------

def profondeur_collecte(conn: sqlite3.Connection) -> dict:
    """Depuis combien de temps collecte-t-on ? Conditionne l'usage des durees."""
    ligne = conn.execute(
        "SELECT MIN(first_seen) AS debut, MAX(last_seen) AS fin, COUNT(*) AS n "
        "FROM listings"
    ).fetchone()
    if ligne is None or ligne["n"] == 0 or not ligne["debut"]:
        return {"jours": 0, "trimestres": 0.0, "debut": None, "fin": None, "n": 0}
    debut, fin = pd.to_datetime(ligne["debut"]), pd.to_datetime(ligne["fin"])
    jours = max((fin - debut).days, 0)
    return {"jours": jours, "trimestres": round(jours / 91.0, 2),
            "debut": str(debut.date()), "fin": str(fin.date()), "n": ligne["n"]}


def durees_exploitables(conn: sqlite3.Connection) -> tuple[bool, str]:
    """L'indicateur de tension ne se calcule pas trop tot.

    La duree de mise en ligne avant disparition ne devient interpretable qu'apres deux
    a trois trimestres : avant, elle est tronquee par la fin de la fenetre d'observation
    (les annonces encore actives n'ont pas de duree, et les plus longues sont censurees).
    """
    p = profondeur_collecte(conn)
    if p["n"] == 0:
        return False, "aucune annonce collectee."
    if p["trimestres"] < TRIMESTRES_MIN_DUREES:
        return False, (
            f"collecte sur {p['jours']} jours ({p['trimestres']} trimestre(s)) : "
            f"trop court pour interpreter les durees de mise en ligne. "
            f"Attendre {TRIMESTRES_MIN_DUREES} trimestres. "
            f"Le calcul reste possible a titre indicatif, mais les durees longues sont "
            f"censurees par la fenetre d'observation et la mediane est biaisee vers le bas."
        )
    return True, f"collecte sur {p['jours']} jours : durees exploitables."


def durees_en_ligne(conn: sqlite3.Connection, df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Duree entre first_seen et last_seen pour les annonces disparues.

    Seules les annonces passees a 'disparue' ont une duree complete ; les actives sont
    censurees a droite et sont donc exclues, ce qui doit etre rappele a chaque usage.
    """
    df = charger(conn) if df is None else df
    colonnes = ["quartier_norm", "trimestre", "n", "duree_mediane_jours", "q1", "q3"]
    if df.empty:
        return pd.DataFrame(columns=colonnes)

    disparues = df[df["statut"] == "disparue"].copy()
    if disparues.empty:
        return pd.DataFrame(columns=colonnes)

    disparues["duree_jours"] = (
        pd.to_datetime(disparues["last_seen"]) - pd.to_datetime(disparues["first_seen"])
    ).dt.days

    return (disparues.groupby(["quartier_norm", "trimestre"])
            .agg(n=("id", "count"), duree_mediane_jours=("duree_jours", "median"),
                 q1=("duree_jours", lambda s: s.quantile(0.25)),
                 q3=("duree_jours", lambda s: s.quantile(0.75)))
            .reset_index()
            .sort_values(["trimestre", "quartier_norm"]))


# --------------------------------------------------------------------------------------
# Annexe : version ponderee
# --------------------------------------------------------------------------------------

POIDS_CSV = Path(__file__).resolve().parent / "poids_arrondissements.csv"


def ponderer_par_arrondissement(df: pd.DataFrame,
                                chemin_poids: str | Path = POIDS_CSV) -> pd.DataFrame:
    """Mediane ponderee par le poids demographique des arrondissements — ANNEXE seulement.

    Les resultats principaux restent bruts par cellule. La ponderation demande un fichier
    de poids renseigne a la main (population par arrondissement) : sans lui, la fonction
    refuse de calculer plutot que d'inventer des poids.
    """
    chemin = Path(chemin_poids)
    if not chemin.exists():
        raise FileNotFoundError(
            f"{chemin.name} absent : renseigner la population par arrondissement "
            "(source RGPH/HCP) avant toute ponderation."
        )
    poids = pd.read_csv(chemin)
    if poids["population"].isna().any():
        raise ValueError(
            f"{chemin.name} incomplet : certaines populations ne sont pas renseignees. "
            "Aucune ponderation ne sera calculee sur des poids partiels."
        )
    if df.empty:
        return pd.DataFrame(columns=["typologie", "loyer_m2_moyen_pondere"])

    fusion = df.merge(poids, on="arrondissement", how="inner")
    return (fusion.groupby("typologie")
            .apply(lambda g: pd.Series({
                "n": len(g),
                "loyer_m2_moyen_pondere": (g["loyer_m2"] * g["population"]).sum()
                / g["population"].sum(),
            }), include_groups=False)
            .reset_index())


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--cellules", action="store_true")
    p.add_argument("--durees", action="store_true")
    p.add_argument("--qualite-min", type=int, default=QUALITE_MIN)
    args = p.parse_args()

    if not Path(args.db).exists():
        print(f"base introuvable : {args.db}")
        return 1

    conn = connect(args.db)
    df = charger(conn, qualite_min=args.qualite_min)

    if df.empty:
        print("Aucune annonce exploitable en base "
              f"(filtre qualite >= {args.qualite_min}, hors doublons et exclusions).")
        print("Lancer la collecte avant l'analyse : python collect.py --probe")
        return 0

    diag = diagnostic_couverture(df)
    print(f"{diag['observations']} observations valides | {diag['cellules']} cellules | "
          f"{diag['publiables']} publiables (n>={SEUIL_MEDIANE}) | "
          f"{diag['avec_ic']} avec IC credible (n>={SEUIL_IC})\n")

    if args.cellules:
        print(stats_par_cellule(df).to_string(index=False))
    elif args.durees:
        exploitable, message = durees_exploitables(conn)
        print(f"{'OK' if exploitable else 'PREMATURE'} : {message}\n")
        table = durees_en_ligne(conn, df)
        print(table.to_string(index=False) if not table.empty
              else "aucune annonce disparue : rien a mesurer.")
    else:
        print(table_couverture(df).to_string())
        sous_seuil = cellules_sous_seuil(df)
        print(f"\n{len(sous_seuil)} cellules sous le seuil de {SEUIL_MEDIANE} :")
        print(sous_seuil.head(30).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
