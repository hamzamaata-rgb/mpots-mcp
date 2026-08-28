# Loyers residentiels Casablanca — base et normalisation

Base de donnees d'annonces de location residentielle a Casablanca, a visee statistique
(loyer median au m2 par quartier et typologie, dispersion, evolution, tension locative).
L'objectif est un echantillon propre et documente, pas un crawl exhaustif.

Etat : **chaine complete ecrite et testee (147 tests), mais aucune donnee collectee.**
L'environnement d'execution distant bloque tout egress HTTPS hors registres de paquets
(`avito.ma`, `mubawab.ma` et `web.archive.org` renvoient 403 au CONNECT du proxy), donc
la premiere collecte reste a lancer depuis une machine au reseau ouvert.

## Installation

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python db.py --init     # cree data/casa_rentals.db + charge le referentiel
./venv/bin/python -m pytest tests/ -q
```

## Lancer la collecte

```bash
./lancer.sh                                 # amorce complete : venv, deps, base, calibration
./lancer.sh collecte                        # puis la collecte reelle

# ou etape par etape :
./venv/bin/python collect.py --probe        # 1. calibration - OBLIGATOIRE la premiere fois
./venv/bin/python collect.py --pages 5      # 2. premiere collecte reelle
./venv/bin/python dedup.py                  # 3. rattachement des republications
./venv/bin/python analyse.py                # 4. couverture et cellules sous le seuil
```

`--probe` telecharge une page, l'ecrit dans `samples/` et affiche combien d'annonces
chaque strategie de parsing en tire. Tant qu'il ne renvoie pas un gabarit exploitable,
`--pages` ne collectera rien : les URLs de recherche et les selecteurs DOM ont ete ecrits
sans acces au site reel et doivent etre confrontes a une page d'aujourd'hui. Un HTML
sauvegarde se rejoue hors ligne avec `collect.py --html-file samples/xxx.html`.

Le collecteur s'arrete de lui-meme sur un 403 ou un 429, sur un `robots.txt` defavorable,
et quand aucune annonce n'est extraite d'une page (parser a recalibrer) — dans tous les cas
avec une ligne dans `runs` expliquant pourquoi.

## Fichiers

| Fichier | Role |
|---|---|
| `schema.sql` | Schema SQLite (listings, snapshots, quartiers, runs) |
| `db.py` | Creation de la base, synchronisation du referentiel, stats |
| `quartiers_seed.csv` | **Referentiel des quartiers — source de verite, a editer a la main** |
| `normalize.py` | Extraction et normalisation : surface, loyer, typologie, quartier, PII, score qualite |
| `avito.py` | Parser : payload Next.js, JSON-LD, repli DOM. Pur, sans reseau |
| `collect.py` | Collecte courante : debit limite, robots.txt, idempotence, journal de run |
| `dedup.py` | Rattachement des republications via `duplicate_of` |
| `lancer.sh` | Amorce complete de la collecte en une commande |
| `analyse.py` | Couverture, loyers au m2 par cellule, durees de mise en ligne |
| `analyse.ipynb` | Notebook d'analyse en coupe (habillage de `analyse.py`) |
| `poids_arrondissements.csv` | **Gabarit vide** : populations a renseigner pour l'annexe ponderee |
| `METHODO.md` | Note methodologique : sources, exclusions, biais, indice hedonique |
| `tests/` | 147 tests (79 normalisation + 41 collecte + 24 analyse) |
| `data/casa_rentals.db` | Base locale (non versionnee) |

Le referentiel s'edite dans le CSV, jamais directement en base : `python db.py --sync-quartiers`
reapplique le fichier. La synchronisation n'efface aucun quartier deja utilise par des lignes
de `listings`, elle signale seulement les orphelins.

## Donnees personnelles

Le schema ne comporte aucune colonne de contact. `normalize.scrub_pii` remplace telephones
marocains (`06…`, `+212 6…`, separateurs varies), emails et URLs par `[tel]`, `[email]`, `[url]`
dans le titre et la description **avant** stockage, et le `content_hash` est calcule sur le
texte expurge — deux republications identiques au numero pres ont donc le meme hash.
`normalize_listing` ignore toute cle de contact presente en entree. Seul `is_pro` est conserve.

## Ecarts au schema initial

Quatre colonnes ajoutees a `listings`, toutes justifiees par une regle de l'enonce :

- `duplicate_of` — le dedoublonnage marque au lieu de supprimer (regle demandee).
- `quartier_method` (`exact` / `alias` / `fuzzy`) et `surface_source` (`structure` / `texte`) —
  le score `qualite` depend de ces deux informations ; les stocker rend le score
  recalculable et auditable apres coup, sans re-parser.
- `exclusion` — motif d'exclusion analytique (`vente`, `courte_duree`) : on garde la ligne
  pour compter les rejets dans METHODO.md plutot que de la perdre silencieusement.

Une colonne ajoutee a `quartiers` : `perimetre` (`casablanca` / `peripherie`), cf. ci-dessous.

## Regles de normalisation

- **Surface** : champ structure prioritaire, sinon extraction texte. Unites sures
  (`m2`, `m²`, `metres carres`, `متر مربع`) d'abord ; `metres` seul n'est accepte que hors
  tournure de distance (« a 200 metres de la plage » n'est pas une surface). Bornes [15, 500],
  premiere valeur plausible retenue (« 90 m2 avec terrasse de 25 m2 » → 90).
- **Loyer** : separateurs de milliers espace/point/apostrophe. Rejets tracks par motif —
  `sous_plancher` (< 1 000), `vente` (> 100 000 ou vocabulaire de vente),
  `courte_duree` (prix a la nuit / a la semaine), `illisible`.
- **Typologie** : `studio` → 1 piece, `S+2` → 3 pieces / 2 chambres (convention marocaine,
  salon compte), `F3`/`T3` → 3 pieces. A defaut, pieces = chambres + 1 (deduction signalee).
  Cellules : `studio_T2` / `T3` / `T4+`.
- **Quartier** : trois passes — exact sur un alias, alias present comme mot entier
  (le plus long gagne), puis fuzzy rapidfuzz avec seuil 88 **et** marge de 4 points sur le
  meilleur autre quartier. La marge evite les confusions Aïn Diab / Aïn Chock / Aïn Sebaâ :
  en cas d'ambiguite on ne tranche pas, la ligne part dans `unmatched_quartiers.csv`.
  Les alias de moins de 5 caracteres (`CIL`, `Polo`) sont exclus du fuzzy.
- **Qualite** : 3 = surface structuree + quartier exact + loyer ; 2 = quartier fuzzy ;
  1 = surface extraite du texte ; 0 = champ manquant. Les degradations se cumulent par le
  minimum. Les plafonds sont les constantes `CAP_QUARTIER_FUZZY` et `CAP_SURFACE_TEXTE`.

## Points en attente d'arbitrage

1. **Segment des quartiers** : classement en trois tiers alors que le marche en montre
   plutot quatre (cf. message de synthese).
2. **Perimetre** : Bouskoura, Dar Bouazza, Errahma, Nouaceur, Mohammedia, Tit Mellil sont
   hors commune de Casablanca mais massivement annonces comme « Casablanca ».
   Marques `perimetre = 'peripherie'`, a exclure par defaut des medians « Casablanca ».
3. **Plafond `CAP_SURFACE_TEXTE = 1`** : applique la regle a la lettre, mais si Avito
   n'expose pas de surface structuree, toutes les lignes tombent a 1 et le filtre
   `qualite >= 2` vide l'echantillon. A retrancher sur donnees reelles a l'etape 2.
