"""Tests de l'analyse en coupe.

La base de test est peuplee de facon deterministe : ce qui est verifie, c'est le
comportement des seuils, des filtres d'exclusion et des garde-fous — pas des valeurs
de marche, qui n'existent pas encore.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

import analyse  # noqa: E402
import collect  # noqa: E402
from db import charger_referentiel, init_db, sync_quartiers  # noqa: E402
from normalize import normalize_listing  # noqa: E402

DEBUT = date(2026, 1, 5)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    c = init_db(tmp_path / "analyse.db")
    sync_quartiers(c, RACINE / "quartiers_seed.csv")
    return c


@pytest.fixture()
def ref(conn):
    return charger_referentiel(conn)


def _ajouter(conn, ref, quartier, n, loyer, surface, chambres=2, jour=0, duree=None,
             url_prefixe="a", **kw):
    """Insere n annonces identiques dans un quartier, eventuellement disparues."""
    first = (DEBUT + timedelta(days=jour)).isoformat()
    for i in range(n):
        brute = {
            "url": f"https://www.avito.ma/fr/x/appartements/{url_prefixe}_{quartier}_{i}.htm",
            "source": "avito",
            "titre": f"Appartement {surface} m2 {quartier}",
            "description": f"Appartement de {surface} m2 avec {chambres} chambres, ascenseur.",
            "quartier_raw": quartier,
            "surface_m2": surface,
            "nb_chambres": chambres,
            "loyer_mad": loyer + i,     # evite des doublons parfaits
            **kw,
        }
        ligne = normalize_listing(brute, ref)
        collect.enregistrer_annonce(conn, ligne, first, i)
        if duree is not None:
            fin = (DEBUT + timedelta(days=jour + duree)).isoformat()
            conn.execute(
                "UPDATE listings SET last_seen = ?, statut = 'disparue' WHERE url = ?",
                (fin, ligne["url"]),
            )
    conn.commit()


# ------------------------------------------------------------------------- chargement

def test_charger_base_vide(conn):
    df = analyse.charger(conn)
    assert df.empty
    assert {"typologie", "loyer_m2"} <= set(df.columns)   # colonnes presentes malgre tout


def test_charger_filtre_la_qualite(conn, ref):
    _ajouter(conn, ref, "Maarif", 3, 9000, 85)
    # une annonce sans quartier reconnu : qualite 0
    _ajouter(conn, ref, "Hay Zitoune Inconnu", 3, 7000, 70, url_prefixe="b")

    df = analyse.charger(conn)
    assert len(df) == 3
    assert set(df["quartier_norm"]) == {"Maârif"}
    assert (df["qualite"] >= 2).all()


def test_charger_exclut_doublons_et_exclusions(conn, ref):
    _ajouter(conn, ref, "Maarif", 4, 9000, 85)
    ids = [r[0] for r in conn.execute("SELECT id FROM listings ORDER BY id")]
    conn.execute("UPDATE listings SET duplicate_of = ? WHERE id = ?", (ids[0], ids[1]))
    conn.execute("UPDATE listings SET exclusion = 'vente' WHERE id = ?", (ids[2],))
    conn.commit()

    assert len(analyse.charger(conn)) == 2
    assert len(analyse.charger(conn, inclure_doublons=True)) == 3   # l'exclusion reste


def test_charger_exclut_la_peripherie(conn, ref):
    _ajouter(conn, ref, "Maarif", 2, 9000, 85)
    _ajouter(conn, ref, "Bouskoura", 2, 6000, 90, url_prefixe="b")

    assert set(analyse.charger(conn)["quartier_norm"]) == {"Maârif"}
    assert len(analyse.charger(conn, perimetre=None)) == 4


def test_loyer_m2_calcule(conn, ref):
    _ajouter(conn, ref, "Maarif", 1, 8500, 85)
    df = analyse.charger(conn)
    assert df["loyer_m2"].iloc[0] == pytest.approx(100.0)


def test_typologie_depuis_les_chambres(conn, ref):
    _ajouter(conn, ref, "Maarif", 1, 9000, 85, chambres=2)
    _ajouter(conn, ref, "Gauthier", 1, 12000, 120, chambres=4, url_prefixe="b")
    df = analyse.charger(conn).set_index("quartier_norm")
    assert df.loc["Maârif", "typologie"] == "T3"
    assert df.loc["Gauthier", "typologie"] == "T4+"


# -------------------------------------------------------------------------- couverture

def test_table_couverture(conn, ref):
    _ajouter(conn, ref, "Maarif", 35, 9000, 85, chambres=2)
    _ajouter(conn, ref, "Gauthier", 10, 12000, 120, chambres=4, url_prefixe="b")

    table = analyse.table_couverture(analyse.charger(conn))
    assert table.loc["Maârif", "T3"] == 35
    assert table.loc["Gauthier", "T4+"] == 10
    assert table.loc["Maârif", "total"] == 35
    assert list(table.columns) == analyse.TYPOLOGIES + ["total"]


def test_table_couverture_base_vide(conn):
    assert analyse.table_couverture(analyse.charger(conn)).empty


def test_cellules_sous_seuil(conn, ref):
    _ajouter(conn, ref, "Maarif", 35, 9000, 85, chambres=2)
    _ajouter(conn, ref, "Gauthier", 10, 12000, 120, chambres=4, url_prefixe="b")

    sous = analyse.cellules_sous_seuil(analyse.charger(conn))
    manquants = {(r["quartier_norm"], r["typologie"]): r["manque"]
                 for _, r in sous.iterrows()}
    assert (("Maârif", "T3")) not in manquants          # 35 >= 30, cellule publiable
    assert manquants[("Gauthier", "T4+")] == 20         # il manque 20 annonces
    assert manquants[("Maârif", "studio_T2")] == 30     # cellule vide


def test_diagnostic_couverture(conn, ref):
    _ajouter(conn, ref, "Maarif", 85, 9000, 85, chambres=2)
    _ajouter(conn, ref, "Gauthier", 35, 12000, 120, chambres=4, url_prefixe="b")

    diag = analyse.diagnostic_couverture(analyse.charger(conn))
    assert diag["observations"] == 120
    assert diag["publiables"] == 2      # Maarif/T3 et Gauthier/T4+
    assert diag["avec_ic"] == 1         # seul Maarif/T3 depasse 80


# ---------------------------------------------------------------------- loyers cellules

def test_stats_par_cellule_masque_sous_le_seuil(conn, ref):
    _ajouter(conn, ref, "Maarif", 35, 9000, 90, chambres=2)
    _ajouter(conn, ref, "Gauthier", 5, 12000, 120, chambres=4, url_prefixe="b")

    table = analyse.stats_par_cellule(analyse.charger(conn)).set_index(
        ["quartier_norm", "typologie"])

    maarif = table.loc[("Maârif", "T3")]
    assert maarif["publiable"] and maarif["loyer_m2_median"] == pytest.approx(100.2, abs=0.5)

    gauthier = table.loc[("Gauthier", "T4+")]
    assert not gauthier["publiable"]
    assert pd.isna(gauthier["loyer_m2_median"])     # cellule visible, statistique masquee
    assert gauthier["n"] == 5                       # mais l'effectif reste lisible


def test_stats_par_cellule_sans_masquage(conn, ref):
    _ajouter(conn, ref, "Gauthier", 5, 12000, 120, chambres=4)
    table = analyse.stats_par_cellule(analyse.charger(conn), masquer_sous_seuil=False)
    assert not pd.isna(table.iloc[0]["loyer_m2_median"])


def test_stats_par_cellule_iqr(conn, ref):
    for i, loyer in enumerate([6000, 7000, 8000, 9000]):
        _ajouter(conn, ref, "Maarif", 8, loyer, 80, chambres=2, url_prefixe=f"p{i}")
    table = analyse.stats_par_cellule(analyse.charger(conn)).iloc[0]
    assert table["n"] == 32
    assert table["iqr"] > 0 and table["q3"] > table["q1"]


def test_stats_par_segment(conn, ref):
    _ajouter(conn, ref, "Maarif", 10, 9000, 90, chambres=2)
    _ajouter(conn, ref, "Bernoussi", 10, 4000, 80, chambres=2, url_prefixe="b")

    table = analyse.stats_par_segment(analyse.charger(conn)).set_index("segment")
    assert table.loc["intermediaire", "n"] == 10
    assert table.loc["populaire", "loyer_m2_median"] < table.loc["intermediaire",
                                                                 "loyer_m2_median"]


# ------------------------------------------------------------------------------ durees

def test_durees_prematurees_sur_collecte_courte(conn, ref):
    _ajouter(conn, ref, "Maarif", 10, 9000, 85, duree=12)
    exploitable, message = analyse.durees_exploitables(conn)
    assert exploitable is False
    assert "trop court" in message and "censurees" in message


def test_durees_exploitables_apres_deux_trimestres(conn, ref):
    _ajouter(conn, ref, "Maarif", 10, 9000, 85, jour=0, duree=200)
    exploitable, message = analyse.durees_exploitables(conn)
    assert exploitable is True


def test_durees_base_vide(conn):
    exploitable, message = analyse.durees_exploitables(conn)
    assert exploitable is False and "aucune annonce" in message


def test_durees_en_ligne_exclut_les_actives(conn, ref):
    _ajouter(conn, ref, "Maarif", 6, 9000, 85, duree=20)          # disparues
    _ajouter(conn, ref, "Gauthier", 6, 12000, 120, url_prefixe="b")   # encore actives

    table = analyse.durees_en_ligne(conn)
    assert list(table["quartier_norm"].unique()) == ["Maârif"]
    assert table.iloc[0]["duree_mediane_jours"] == 20
    assert table.iloc[0]["n"] == 6


def test_durees_aucune_disparue(conn, ref):
    _ajouter(conn, ref, "Maarif", 5, 9000, 85)
    assert analyse.durees_en_ligne(conn).empty


def test_profondeur_collecte(conn, ref):
    _ajouter(conn, ref, "Maarif", 3, 9000, 85, jour=0, duree=100)
    p = analyse.profondeur_collecte(conn)
    assert p["jours"] == 100 and p["trimestres"] == pytest.approx(1.1, abs=0.05)


# ---------------------------------------------------------------------------- annexe

def test_ponderation_refuse_sans_poids(conn, ref, tmp_path):
    _ajouter(conn, ref, "Maarif", 5, 9000, 85)
    with pytest.raises(FileNotFoundError, match="population"):
        analyse.ponderer_par_arrondissement(analyse.charger(conn), tmp_path / "absent.csv")


def test_ponderation_refuse_des_poids_partiels(conn, ref, tmp_path):
    _ajouter(conn, ref, "Maarif", 5, 9000, 85)
    chemin = tmp_path / "poids.csv"
    chemin.write_text("arrondissement,population\nMaârif,\nAnfa,100000\n", encoding="utf-8")
    with pytest.raises(ValueError, match="incomplet"):
        analyse.ponderer_par_arrondissement(analyse.charger(conn), chemin)


def test_ponderation_calcule_avec_des_poids_complets(conn, ref, tmp_path):
    _ajouter(conn, ref, "Maarif", 5, 9000, 90, chambres=2)
    chemin = tmp_path / "poids.csv"
    chemin.write_text("arrondissement,population\nMaârif,200000\n", encoding="utf-8")
    table = analyse.ponderer_par_arrondissement(analyse.charger(conn), chemin)
    assert table.iloc[0]["loyer_m2_moyen_pondere"] == pytest.approx(100.0, abs=0.5)


def test_le_gabarit_de_poids_est_vide(conn):
    """Le fichier livre ne contient aucune population inventee."""
    poids = pd.read_csv(RACINE / "poids_arrondissements.csv")
    assert poids["population"].isna().all()
    assert len(poids) >= 15
