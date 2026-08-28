# Loyers residentiels Casablanca — base et normalisation

Base de donnees d'annonces de location residentielle a Casablanca, a visee statistique
(loyer median au m2 par quartier et typologie, dispersion, evolution, tension locative).
L'objectif est un echantillon propre et documente, pas un crawl exhaustif.

Etat : **etape 1 terminee** (schema, referentiel quartiers, normalisation testee).
Le parser Avito (etape 2) n'est pas encore ecrit — il attend la validation du referentiel.

## Installation

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python db.py --init     # cree data/casa_rentals.db + charge le referentiel
./venv/bin/python -m pytest tests/ -q
```

## Fichiers

| Fichier | Role |
|---|---|
| `schema.sql` | Schema SQLite (listings, snapshots, quartiers, runs) |
| `db.py` | Creation de la base, synchronisation du referentiel, stats |
| `quartiers_seed.csv` | **Referentiel des quartiers — source de verite, a editer a la main** |
| `normalize.py` | Extraction et normalisation : surface, loyer, typologie, quartier, PII, score qualite |
| `tests/test_normalize.py` | 79 tests unitaires |
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
