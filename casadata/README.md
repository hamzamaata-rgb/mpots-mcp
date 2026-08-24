# casadata — Plateforme de données immobilières Casablanca

Base de données du marché immobilier résidentiel casablancais pensée en
**observations horodatées multi-sources** : chaque passage de collecte ajoute
des observations (append-only), les prix ne sont jamais écrasés, chaque donnée
est traçable (source → run → brut → confiance → flags qualité).

**Commencez par lire [`docs/STRATEGY.md`](docs/STRATEGY.md)** — le rapport
« Casablanca Real Estate Data Strategy » (sources, historique, volumes,
contraintes, plan). Puis [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) et
[`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Installation

```bash
cd casadata
pip install -e ".[dev]"
pytest                      # 30 tests
casadata init               # crée data/casadata.duckdb + gazetteer (63 localisations)
```

## Ce qui est inclus

| Module | Rôle |
|---|---|
| `schema.sql` | Schéma DuckDB complet : source, scrape_run, location, seller, property, property_link, listing, listing_observation (append-only), listing_event, market_aggregate, poi, dataset_manifest + vues price_history / rent_history / listing_lifecycle |
| `geo/` | Gazetteer Casablanca (préfectures → arrondissements → quartiers → micro-quartiers, alias FR/AR) + normalisation `raw_location → slug + confiance` |
| `ingest/` | Cycle d'observation (first_seen, price_change, disappeared, reappeared) + ingestion de datasets historiques CSV avec manifest de provenance obligatoire |
| `collect/` | Client HTTP poli (robots.txt vérifié à l'exécution, rate-limit, backoff, archive brute JSONL.gz), collecteurs Mubawab/Avito/Sarouty, harvester Wayback Machine (historique 2012→), import de séries institutionnelles (IPAI BKAM×ANCFCC, référentiels quartiers) |
| `dedup/` | Rapprochement annonces ↔ biens : blocking + score multi-signaux, liens `auto` / `candidate`, jamais de fusion destructive |
| `quality/` | Flags d'anomalies (jamais de suppression) |
| `analytics/` | Stats par quartier, cycle de vie (durée d'exposition, baisses), comparables locatifs → loyer estimé → rendement brut, export Parquet |

## Démarrage rapide

```bash
# 1. importer un dataset historique (manifest de provenance obligatoire)
casadata ingest-dataset data/incoming/seed.csv

# 2. collecte live (à lancer depuis un environnement avec accès Internet complet)
casadata collect portal mubawab sale --limit 5      # test de validation des parsers
casadata collect portal mubawab sale --mark-missing # run complet
casadata collect portal avito rent --mark-missing

# 3. historique via archives web
casadata collect wayback mubawab --from-year 2015 --limit 500

# 4. séries institutionnelles (CSV transcrit des publications IPAI)
casadata ingest-aggregates ipai data/incoming/ipai_casablanca.csv

# 5. analyse
casadata dedupe
casadata stats
casadata quartiers sale
casadata estimate-rent maarif 95 2 --price 1350000
casadata export     # Parquet pour notebooks
```

## Important

- **Environnement d'exécution** : ce dépôt a été développé dans un conteneur
  sans accès réseau aux portails marocains. Les collecteurs sont conformes et
  testés hors-ligne ; **valider les sélecteurs sur pages réelles au premier run
  live** (`--limit 3`) et relire les ToS/robots.txt des portails.
- **Conformité** : robots.txt vérifié à chaque requête, 1 req/4 s minimum,
  User-Agent identifiable (renseigner `CASADATA_CONTACT`), pas de contournement
  de CAPTCHA/auth, téléphones jamais stockés, identifiants vendeurs hashés
  (`CASADATA_SALT` à personnaliser), noms conservés uniquement pour les agences.
- **Annonce disparue ≠ vendu** — la sémantique est `disappeared`, rien d'autre.
- **Collecte continue** : `scripts/run_daily.sh` + cron (voir OPERATIONS.md).
