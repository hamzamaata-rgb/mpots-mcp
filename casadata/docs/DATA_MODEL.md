# Modèle de données

Schéma complet : [`src/casadata/schema.sql`](../src/casadata/schema.sql).

## Principe directeur

> « Cette donnée vient d'où, quand a-t-elle été observée, quelle est sa qualité ? »

Chaque observation porte : `run_id` (→ `scrape_run` → `source`), `observed_at`,
`raw_ref` (pointeur `fichier.jsonl.gz#ligne` vers la réponse HTTP brute ou
`fichier.csv#ligne` pour un import), `confidence` (1.0 live, 0.7 wayback,
0.6–0.8 datasets selon manifest) et `quality_flags` (anomalies **flaggées,
jamais supprimées**).

## Entités

```
source ──< scrape_run ──< listing_observation >── listing >── seller
                                                     │  │
                              listing_event >────────┘  └──── location (gazetteer)
                                                     │
property ──< property_link >─────────────────────────┘
market_aggregate (IPAI, référentiels quartier)      poi (OSM)
dataset_manifest (provenance des imports)
```

- **`property`** : le bien réel (créé par la déduplication).
- **`listing`** : une annonce d'une source (`UNIQUE(source_id, external_id)`),
  avec `status` actif/disparu, `first_seen_at`/`last_seen_at`, `raw_location`
  **toujours conservé** à côté de `location_id` normalisé.
- **`listing_observation`** : **append-only**. L'état observé de l'annonce à un
  instant t : prix/loyer, surface, pièces, chambres, SDB, étage, état, âge,
  meublé, `attrs` JSON (ascenseur, parking, garage, balcon, terrasse, jardin,
  piscine, clim, chauffage, cuisine équipée, sécurité, cave, chambre de
  service, vue, orientation, disponibilité, durée bail, charges…), description,
  photos (URLs), provenance. Le scénario
  `1 500 000 → 1 450 000 → 1 400 000 → disparu` produit 3+ lignes, jamais une
  mise à jour.
- **`listing_event`** : first_seen, price_change (ancien/nouveau prix),
  disappeared (≠ vendu), reappeared, attr_change.
- **`property_link`** : annonce ↔ bien avec `score` [0,1], `method` et
  `status` (`auto` ≥ 0,80 ; `candidate` 0,55–0,80 pour revue ; `confirmed`/
  `rejected` manuels).
- **`seller`** : hash salé de l'identifiant plateforme ; `agency_name`
  uniquement pour les personnes morales ; jamais de téléphone.
- **`location`** : slug stable, 4 niveaux (ville → arrondissement → quartier →
  micro-quartier) + préfecture ; lat/lon remplis depuis OSM (pas de
  coordonnées inventées).
- **`market_aggregate`** : séries externes (IPAI 2006→, prix/m² quartier
  Agenz/Yakeey, HCP, taux) — la couche de calibration listing → transaction.
  Ne jamais mélanger avec les annonces : un prix d'annonce n'est pas un prix
  de transaction.
- **`rental` vs `sale`** : même paire listing/observation, discriminée par
  `transaction_type` + `rent_period`/`charges_included` — ce qui permet les
  comparables locatifs par quartier/surface/chambres (`analytics.comparables`).

## Vues

- `price_history` / `rent_history` : trajectoires de prix (une ligne par
  changement).
- `latest_observation` : dernier état par annonce.
- `listing_lifecycle` : durée d'exposition (`days_on_market`), nb de baisses,
  baisse totale (absolue et %), pour les modèles de liquidité et de décote.

## Ce que le schéma permet (objectifs statistiques)

| Modèle cible | Données déjà en place |
|---|---|
| Prix = f(surface, quartier, étage, …) | listing_observation × location |
| Loyer = f(surface, quartier, …) | idem, transaction_type='rent' |
| Rendement | comparables locatifs + coût d'acquisition (`gross_yield`) |
| Liquidité | listing_lifecycle (délais, probabilité de retrait) |
| Décote | price_drop_pct + calibration IPAI/market_aggregate |
| Fair value | tout ce qui précède + dédup (biens multi-annonces) |
