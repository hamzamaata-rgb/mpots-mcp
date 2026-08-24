# Opérations — collecte continue et imports

## Prérequis d'environnement

La collecte live doit tourner depuis un environnement avec accès Internet
complet (machine locale, VPS ~5 €/mois, ou GitHub Actions self-hosted).
Variables :

```bash
export CASADATA_HOME=/srv/casadata/data    # défaut: ./data
export CASADATA_CONTACT="votre@email"      # identifie le bot dans le User-Agent
export CASADATA_SALT="votre-sel-secret"    # hachage des identifiants vendeurs
export CASADATA_DELAY=4.0                  # délai min entre requêtes (s)
```

## Avant le premier run live (checklist)

1. Lire les ToS et robots.txt de chaque portail (le client les vérifie à
   chaque requête, mais une lecture humaine des ToS reste nécessaire).
2. Valider les parsers sur 3 annonces : `casadata collect portal mubawab sale --limit 3`
   puis vérifier `casadata stats` et le contenu de `data/raw/`.
   Les regex de `collect/portal.py` (liens d'annonces, id) et les templates
   d'URLs de recherche sont à ajuster si la structure du site a changé.
3. Personnaliser `CASADATA_SALT` (définitif : le changer casse le suivi vendeurs).

## Cadence recommandée

| Tâche | Fréquence | Commande |
|---|---|---|
| Mubawab vente + location | quotidien | `casadata collect portal mubawab sale --mark-missing` etc. |
| Avito vente + location | quotidien | idem `avito` |
| Sarouty | hebdo | idem `sarouty` |
| Dédup + stats | quotidien après collecte | `casadata dedupe && casadata stats` |
| Référentiels Agenz/Yakeey (agrégats quartier) | mensuel | `casadata ingest-aggregates agenz fichier.csv` |
| IPAI BKAM×ANCFCC | trimestriel | `casadata ingest-aggregates ipai fichier.csv` |
| Wayback (historique) | one-shot par tranches | `casadata collect wayback mubawab --from-year 2015 --limit 500` |
| Export Parquet | hebdo | `casadata export` |

`scripts/run_daily.sh` enchaîne le tout ; cron :

```cron
15 3 * * * /srv/casadata/scripts/run_daily.sh >> /srv/casadata/logs/daily.log 2>&1
```

`--mark-missing` **uniquement** sur les runs à périmètre complet (sinon de
fausses « disparitions »). Un run interrompu reste tracé `failed`/`partial`
dans `scrape_run` ; le run suivant reprend naturellement (idempotence par
`(source, external_id)` + append-only).

## Import d'un dataset historique

1. Déposer `fichier.csv` dans `data/incoming/`.
2. Créer `fichier.csv.manifest.json` (provenance OBLIGATOIRE) :

```json
{
  "source_code": "university_2019_2021",
  "original_url": "https://…",
  "license": "CC-BY-4.0",
  "period_start": "2019-01-01",
  "period_end": "2021-12-31",
  "confidence": 0.8,
  "transaction_type": "sale",
  "observed_at_column": "date",
  "columns": {"price": "prix", "surface_m2": "surface", "raw_location": "quartier"}
}
```

3. `casadata ingest-dataset data/incoming/fichier.csv`

Datasets à récupérer en priorité (voir STRATEGY §1.3–1.4) :
- dataset universitaire Casablanca 2019–2021 (Avito+Mubawab+Sarouty, 18 k+) —
  protocole de chasse en STRATEGY §1.4 ;
- GitHub `iliasoudghiri/Casablanca-House-Prices` (~3 k, GPS, ~2020) —
  preset `seed_github_chp` ;
- Kaggle `yassinesadiki/housing-data-in-morocco` — preset `kaggle_ma_housing`.

## Import des séries IPAI

Télécharger les publications trimestrielles (bkam.ma → Statistiques → Prix →
Publications IPAI ; ancfcc.gov.ma), transcrire en CSV :

```csv
series_code,geo_level,geo_slug,period_start,period_end,metric,value,unit
ipai_apart_casa,city,casablanca,2025-01-01,2025-03-31,price_index,…,index_2006_100
ipai_txn_casa,city,casablanca,2025-01-01,2025-03-31,transactions_count,…,count
```

puis `casadata ingest-aggregates ipai fichier.csv`.

## Monitoring et validation continue

- `casadata stats` : volumes par source, période couverte — alerte manuelle si
  le volume d'un run chute de >30 % vs la veille (`SELECT * FROM scrape_run
  ORDER BY run_id DESC`).
- Recoupement mensuel : `casadata quartiers sale` vs référentiels
  Agenz/Yakeey ; tendance vs IPAI Casablanca.
- Audit dédup : paires `status='candidate'` dans `property_link`
  (échantillon manuel, promouvoir en `confirmed`/`rejected`).
- Sauvegardes : `data/casadata.duckdb` + `data/raw/` (le brut permet de tout
  reconstruire).
