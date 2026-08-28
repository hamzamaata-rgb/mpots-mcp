"""Collecte courante (volet B) : une passe par jour sur les resultats de location.

    python collect.py --probe            # calibration : 1 page, rien en base
    python collect.py --pages 5          # collecte reelle, 5 pages maximum
    python collect.py --html-file p.html # rejoue un HTML sauvegarde (aucun reseau)

Regles de conduite envers le site, non negociables et implementees ici :
  - une seule requete a la fois, jamais de parallelisme ;
  - 3 a 5 secondes d'attente entre deux requetes (tirage aleatoire) ;
  - user-agent honnete et identifiable ;
  - robots.txt lu et respecte avant la premiere requete ;
  - un 403 ou un 429 arrete la collecte immediatement, sans nouvelle tentative :
    pas de rotation d'IP, pas de contournement.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import time
import urllib.robotparser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

import avito
from db import DB_PATH, charger_referentiel, connect, init_db, sync_quartiers
from normalize import Referentiel, log_unmatched, normalize_listing

RACINE = Path(__file__).resolve().parent
SAMPLES = RACINE / "samples"
UNMATCHED_CSV = RACINE / "unmatched_quartiers.csv"

DELAI_MIN, DELAI_MAX = 3.0, 5.0
JOURS_DEJA_VUE = 2          # profondeur : on s'arrete sur des annonces vues il y a > 2 j
JOURS_DISPARUE = 7          # une annonce active non revue depuis 7 jours passe a 'disparue'
PAGES_MAX_DEFAUT = 5
CONSECUTIVES_POUR_ARRET = 15   # annonces anciennes d'affilee avant d'arreter la pagination

UA = os.environ.get(
    "CASA_RENTALS_UA",
    "casa-rentals-research/0.1 (etude statistique du marche locatif de Casablanca; "
    "+https://github.com/hamzamaata-rgb/mpots-mcp)",
)


class CollecteArretee(RuntimeError):
    """Arret volontaire : refus du site (403/429) ou robots.txt defavorable."""


# --------------------------------------------------------------------------------------
# Reseau
# --------------------------------------------------------------------------------------

class Client:
    """Client HTTP sequentiel a debit limite. Une instance = une session de collecte."""

    def __init__(self, ua: str = UA, delai: tuple[float, float] = (DELAI_MIN, DELAI_MAX),
                 transport: httpx.BaseTransport | None = None):
        self.ua = ua
        self.delai = delai
        self.derniere_requete: float | None = None
        self.requetes = 0
        self._client = httpx.Client(
            headers={"User-Agent": ua, "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8"},
            timeout=30.0,
            follow_redirects=True,
            transport=transport,
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *args) -> None:
        self._client.close()

    def _attendre(self) -> None:
        if self.derniere_requete is not None:
            reste = random.uniform(*self.delai) - (time.monotonic() - self.derniere_requete)
            if reste > 0:
                time.sleep(reste)
        self.derniere_requete = time.monotonic()

    def robots_autorise(self, url: str) -> bool:
        """Lit et met en cache le robots.txt du domaine.

        Suit la RFC 9309 sur les cas d'echec, parce que la difference compte :
          - 200            : on applique le fichier ;
          - 4xx (404 …)    : pas de robots.txt, tout est autorise ;
          - 5xx            : serveur en difficulte, on s'interdit de crawler ;
          - erreur reseau  : on ne sait pas, donc on ne crawle pas a l'aveugle.

        Les deux derniers cas levent `CollecteArretee` plutot que de laisser passer :
        ne pas avoir pu lire les regles n'est pas une permission.
        """
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            self._attendre()
            try:
                reponse = self._client.get(f"{base}/robots.txt")
                self.requetes += 1
            except httpx.HTTPError as exc:
                raise CollecteArretee(
                    f"robots.txt de {base} injoignable ({type(exc).__name__}) : "
                    "impossible de verifier les regles, arret."
                ) from exc
            if reponse.status_code >= 500:
                raise CollecteArretee(
                    f"robots.txt de {base} renvoie {reponse.status_code} : "
                    "serveur en difficulte, on ne crawle pas."
                )
            rp.parse(reponse.text.splitlines() if reponse.status_code == 200 else [])
            self._robots[base] = rp
        return rp.can_fetch(self.ua, url)

    def get(self, url: str) -> str:
        if not self.robots_autorise(url):
            raise CollecteArretee(f"robots.txt interdit l'acces a {url}")
        self._attendre()
        reponse = self._client.get(url)
        self.requetes += 1
        if reponse.status_code in (403, 429):
            raise CollecteArretee(
                f"HTTP {reponse.status_code} sur {url} : le site refuse la collecte, arret."
            )
        reponse.raise_for_status()
        return reponse.text


# --------------------------------------------------------------------------------------
# Ecriture en base
# --------------------------------------------------------------------------------------

def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enregistrer_annonce(
    conn: sqlite3.Connection,
    ligne: dict,
    date_vue: str,
    position: int | None = None,
) -> str:
    """Insere ou met a jour une annonce, plus son snapshot du jour.

    Retourne 'neuve' ou 'revue'. Idempotent : deux passes le meme jour ne creent
    ni doublon de listing (UNIQUE sur url) ni doublon de snapshot (UNIQUE sur
    listing_id + date_vue).
    """
    colonnes = (
        "source", "source_id", "url", "content_hash", "titre", "description",
        "quartier_raw", "quartier_norm", "quartier_method", "surface_m2", "surface_source",
        "nb_pieces", "nb_chambres", "etage", "meuble", "ascenseur", "parking", "is_pro",
        "loyer_mad", "charges_incluses", "date_publication", "exclusion", "qualite",
    )
    valeurs = {c: ligne.get(c) for c in colonnes}

    existante = conn.execute(
        "SELECT id, first_seen FROM listings WHERE url = ?", (ligne["url"],)
    ).fetchone()

    if existante is None:
        champs = ", ".join([*colonnes, "first_seen", "last_seen", "created_at", "statut"])
        marques = ", ".join(["?"] * (len(colonnes) + 4))
        curseur = conn.execute(
            f"INSERT INTO listings ({champs}) VALUES ({marques})",
            (*valeurs.values(), date_vue, date_vue, _maintenant(), "active"),
        )
        listing_id, etat = curseur.lastrowid, "neuve"
    else:
        listing_id, etat = existante["id"], "revue"
        # On rafraichit les champs normalises (une annonce peut etre editee) mais
        # jamais first_seen : la duree de mise en ligne en depend.
        assignations = ", ".join(f"{c} = ?" for c in colonnes)
        conn.execute(
            f"UPDATE listings SET {assignations}, last_seen = ?, statut = 'active' WHERE id = ?",
            (*valeurs.values(), date_vue, listing_id),
        )

    conn.execute(
        "INSERT OR IGNORE INTO snapshots (listing_id, date_vue, loyer_mad, position) "
        "VALUES (?, ?, ?, ?)",
        (listing_id, date_vue, ligne.get("loyer_mad"), position),
    )
    return etat


def marquer_disparues(conn: sqlite3.Connection, aujourdhui: date | None = None,
                      jours: int = JOURS_DISPARUE) -> int:
    """Passe a 'disparue' toute annonce active non revue depuis `jours` jours."""
    limite = ((aujourdhui or date.today()) - timedelta(days=jours)).isoformat()
    curseur = conn.execute(
        "UPDATE listings SET statut = 'disparue' WHERE statut = 'active' AND last_seen < ?",
        (limite,),
    )
    return curseur.rowcount


def journaliser_run(conn: sqlite3.Connection, debut: str, source: str, pages: int,
                    vues: int, neuves: int, erreurs: int, note: str) -> int:
    curseur = conn.execute(
        "INSERT INTO runs (started_at, source, pages_vues, annonces_vues, "
        "annonces_neuves, erreurs, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (debut, source, pages, vues, neuves, erreurs, note),
    )
    conn.commit()
    return curseur.lastrowid


def _vue_recemment(conn: sqlite3.Connection, url: str, aujourdhui: date,
                   jours: int = JOURS_DEJA_VUE) -> bool:
    """Vrai si l'annonce est deja connue et a ete vue il y a plus de `jours` jours.

    C'est le signal de profondeur : on a rattrape le stock deja collecte.
    """
    ligne = conn.execute("SELECT first_seen FROM listings WHERE url = ?", (url,)).fetchone()
    if ligne is None:
        return False
    return ligne["first_seen"][:10] < (aujourdhui - timedelta(days=jours)).isoformat()


# --------------------------------------------------------------------------------------
# Boucle de collecte
# --------------------------------------------------------------------------------------

def traiter_page(conn: sqlite3.Connection, html: str, ref: Referentiel, source: str,
                 date_vue: str, aujourdhui: date, position_depart: int = 0,
                 dry_run: bool = False) -> dict:
    """Parse une page, normalise et enregistre. Retourne le bilan de la page."""
    annonces, strategie = avito.parse_page_resultats(html, aujourdhui)
    bilan = {"annonces": len(annonces), "neuves": 0, "revues": 0, "anciennes": 0,
             "erreurs": 0, "strategie": strategie, "lignes": []}

    for i, brute in enumerate(annonces):
        try:
            brute["source"] = source
            ligne = normalize_listing(brute, ref)
            if ligne["quartier_norm"] is None and brute.get("quartier_raw"):
                log_unmatched(str(brute["quartier_raw"]), UNMATCHED_CSV)

            if _vue_recemment(conn, ligne["url"], aujourdhui):
                bilan["anciennes"] += 1

            if not dry_run:
                etat = enregistrer_annonce(conn, ligne, date_vue, position_depart + i)
                bilan["neuves" if etat == "neuve" else "revues"] += 1
            bilan["lignes"].append(ligne)
        except Exception as exc:  # noqa: BLE001 - une annonce cassee ne doit pas tuer le run
            bilan["erreurs"] += 1
            print(f"    ! annonce ignoree ({type(exc).__name__}: {exc})", file=sys.stderr)
    return bilan


def collecter(conn: sqlite3.Connection, client: Client, ref: Referentiel, pages: int,
              gabarit: str, source: str = "avito", dry_run: bool = False) -> dict:
    """Parcourt les pages de resultats jusqu'a la profondeur utile ou `pages`."""
    aujourdhui = date.today()
    date_vue = aujourdhui.isoformat()
    debut = _maintenant()
    total = {"pages": 0, "vues": 0, "neuves": 0, "erreurs": 0}
    consecutives_anciennes = 0
    note = "collecte normale"

    try:
        for numero in range(1, pages + 1):
            url = avito.url_recherche(numero, gabarit)
            print(f"  page {numero} : {url}")
            html = client.get(url)
            bilan = traiter_page(conn, html, ref, source, date_vue, aujourdhui,
                                 position_depart=total["vues"], dry_run=dry_run)

            if bilan["annonces"] == 0:
                note = f"aucune annonce extraite page {numero} : parser a recalibrer"
                print(f"    {note}", file=sys.stderr)
                break

            total["pages"] += 1
            total["vues"] += bilan["annonces"]
            total["neuves"] += bilan["neuves"]
            total["erreurs"] += bilan["erreurs"]
            if not dry_run:
                conn.commit()
            print(f"    {bilan['annonces']} annonces ({bilan['strategie']}), "
                  f"{bilan['neuves']} neuves, {bilan['anciennes']} deja vues")

            consecutives_anciennes = (
                consecutives_anciennes + bilan["anciennes"] if bilan["anciennes"] else 0
            )
            if consecutives_anciennes >= CONSECUTIVES_POUR_ARRET:
                note = f"profondeur atteinte page {numero} (stock deja collecte)"
                break
    except CollecteArretee as exc:
        note = str(exc)
        total["erreurs"] += 1
        print(f"  ARRET : {note}", file=sys.stderr)
    except httpx.HTTPError as exc:
        note = f"erreur reseau : {type(exc).__name__}"
        total["erreurs"] += 1
        print(f"  ARRET : {note}", file=sys.stderr)

    if not dry_run:
        disparues = marquer_disparues(conn, aujourdhui)
        total["disparues"] = disparues
        journaliser_run(conn, debut, source, total["pages"], total["vues"],
                        total["neuves"], total["erreurs"], note)
    total["note"] = note
    return total


# --------------------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------------------

def probe(client: Client) -> int:
    """Calibration : telecharge une page, sauvegarde le HTML, compare les strategies.

    A passer avant la premiere vraie collecte : c'est la seule facon de verifier que
    l'URL de recherche et les strategies de parsing correspondent au site d'aujourd'hui.
    """
    SAMPLES.mkdir(exist_ok=True)
    for gabarit in avito.RECHERCHE_CANDIDATES:
        url = avito.url_recherche(1, gabarit)
        print(f"\n> {url}")
        try:
            html = client.get(url)
        except (CollecteArretee, httpx.HTTPError) as exc:
            print(f"  echec : {exc}")
            continue

        chemin = SAMPLES / f"resultats_{date.today().isoformat()}.html"
        chemin.write_text(html, encoding="utf-8")
        rapport = avito.diagnostic(html)
        print(f"  HTML sauvegarde ({len(html)} octets) -> {chemin}")
        print(f"  annonces trouvees par strategie : {rapport}")
        if any(isinstance(v, int) and v > 0 for v in rapport.values()):
            print(f"\n  GABARIT RETENU : {gabarit}")
            print("  Figer cette valeur en tete de avito.RECHERCHE_CANDIDATES.")
            return 0
        print("  aucune annonce extraite : selecteurs a recalibrer sur ce HTML.")
    print("\nAucun gabarit exploitable.", file=sys.stderr)
    return 1


def rejouer_fichier(conn: sqlite3.Connection, ref: Referentiel, chemin: Path,
                    dry_run: bool) -> dict:
    """Rejoue un HTML deja telecharge : permet de calibrer et de tester sans reseau."""
    html = Path(chemin).read_text(encoding="utf-8")
    aujourdhui = date.today()
    debut = _maintenant()
    bilan = traiter_page(conn, html, ref, "avito", aujourdhui.isoformat(), aujourdhui,
                         dry_run=dry_run)
    if not dry_run:
        conn.commit()
        # Un rejeu alimente la base : il doit apparaitre dans le journal au meme titre
        # qu'une collecte, sans quoi l'origine des lignes devient intracable.
        journaliser_run(conn, debut, "avito", 1, bilan["annonces"], bilan["neuves"],
                        bilan["erreurs"], f"rejeu du fichier {Path(chemin).name}")
    print(f"strategie : {bilan['strategie']} | annonces : {bilan['annonces']} | "
          f"neuves : {bilan['neuves']} | erreurs : {bilan['erreurs']}")
    return bilan


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pages", type=int, default=PAGES_MAX_DEFAUT)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--probe", action="store_true", help="calibration, n'ecrit rien en base")
    p.add_argument("--html-file", help="rejoue un HTML local, sans reseau")
    p.add_argument("--dry-run", action="store_true", help="parse sans ecrire en base")
    p.add_argument("--gabarit", default=avito.RECHERCHE_CANDIDATES[0])
    p.add_argument("--delai-min", type=float, default=DELAI_MIN)
    p.add_argument("--delai-max", type=float, default=DELAI_MAX)
    args = p.parse_args()

    if args.delai_min < 3.0:
        p.error("le delai minimal entre requetes est de 3 secondes")

    if args.probe:
        with Client(delai=(args.delai_min, args.delai_max)) as client:
            return probe(client)

    conn = init_db(args.db)
    sync_quartiers(conn)
    ref = charger_referentiel(conn)

    if args.html_file:
        rejouer_fichier(conn, ref, Path(args.html_file), args.dry_run)
        return 0

    print(f"Collecte Avito - {date.today().isoformat()} - {args.pages} pages max")
    with Client(delai=(args.delai_min, args.delai_max)) as client:
        total = collecter(conn, client, ref, args.pages, args.gabarit, dry_run=args.dry_run)

    print(f"\n{total['pages']} pages, {total['vues']} annonces vues, "
          f"{total['neuves']} neuves, {total['erreurs']} erreurs")
    if "disparues" in total:
        print(f"{total['disparues']} annonces passees a 'disparue'")
    print(f"note : {total['note']}")
    return 1 if total["erreurs"] and total["pages"] == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
