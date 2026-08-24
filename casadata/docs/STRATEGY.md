# Casablanca Real Estate Data Strategy

*Version 1.0 — 24 août 2026*

Objectif : construire progressivement la meilleure base de données du marché immobilier
résidentiel casablancais — annonces, loyers, historique, géographie, liquidité,
rendements et transactions — pensée en **observations horodatées**, pas en annonces.

---

## 1. Panorama des sources identifiées

### 1.1 Portails d'annonces (données primaires)

| Source | Type | Couverture Casablanca | Variables clés | Accessibilité technique | Rôle dans la stratégie |
|---|---|---|---|---|---|
| **Mubawab.ma** | Vertical immobilier (leader) | ~15–30 k annonces vente + location actives | prix, surface, pièces, chambres, SDB, étage, état, âge, **GPS lat/lon**, tags équipements, quartier, agence | Pages HTML structurées + balisage riche ; sitemaps ; des scrapers open source et Apify prouvent la faisabilité | **Source primaire n°1** (structure + GPS) |
| **Avito.ma** | Généraliste (volume max au Maroc) | Volume le plus élevé, forte part de particuliers | prix, surface, pièces, quartier (texte), photos, date de publication, vendeur | Contenu rendu via état JSON embarqué dans les pages ; protection anti-bot modérée | **Source primaire n°2** (volume + particuliers → signal « décote » plus pur) |
| **Sarouty.ma** (Property Finder Maroc) | Vertical immobilier | Quelques milliers d'annonces, plutôt agences/moyen-haut de gamme | prix, surface, chambres, SDB, quartier, agence, référence | Pages structurées (plateforme Property Finder, balisage JSON-LD habituel) | Source secondaire (recoupement agences) |
| **Yakeey.com** | iBuyer/courtier + **référentiel de prix par quartier** | Annonces vérifiées + pages « carte des prix » par quartier | prix/m² médian par quartier « basé sur transactions réelles » | Pages publiques par quartier | **Série agrégée quartier** (calibration listing→transaction) |
| **Agenz.ma** | Big data immobilier + **référentiel prix** | Pages prix/m² par quartier et micro-quartier (ex. El Maarif), revendique transactions + cadastre | prix/m² moyen par quartier, tendances | Pages publiques par quartier | **Série agrégée quartier** (2e triangulation) |
| **MarocAnnonces, Menzili, Charika…** | Généralistes secondaires | Marginal vs Avito/Mubawab | — | — | Non prioritaire (coût > gain, doublons) |

### 1.2 Sources institutionnelles (transactions réelles et calibration)

| Source | Contenu | Historique | Accès |
|---|---|---|---|
| **Bank Al-Maghrib × ANCFCC — IPAI** (Indice des Prix des Actifs Immobiliers) | Indice trimestriel des prix **par ville (dont Casablanca)** et par type d'actif (appartement, maison, villa, terrain, commercial), méthode *repeat sales* sur les **transactions enregistrées à la conservation foncière** ; + **nombre de transactions** par trimestre | **2006 → aujourd'hui** (base 100 en 2006) | PDF + tableaux trimestriels gratuits sur bkam.ma et ancfcc.gov.ma |
| **Bank Al-Maghrib — statistiques** | Taux débiteurs immobiliers, encours crédit habitat (coût d'acquisition → rendement net) | 2006 → | bkam.ma, gratuit |
| **HCP** | RGPH 2024 (population, ménages, logement **par arrondissement**), IPC composante logement, enquêtes | recensements 2004/2014/2024 | hcp.ma, gratuit |
| **ANCFCC** | Rapports d'activité (volumes de mutations) | annuel | ancfcc.gov.ma |

**Point clé** : les transactions individuelles de l'ANCFCC ne sont **pas publiques**.
La meilleure approximation licite est : IPAI (tendance transactionnelle) + référentiels
Agenz/Yakeey (niveaux par quartier annoncés comme issus de transactions) + nos annonces
(niveaux fins). Ne jamais présenter un prix d'annonce comme un prix de transaction —
le schéma sépare structurellement les deux.

### 1.3 Datasets existants (historique déjà constitué)

| Dataset | Contenu | Période | Taille | Licence/qualité | Verdict |
|---|---|---|---|---|---|
| **Kaggle — `yassinesadiki/housing-data-in-morocco`** | Prix logements Maroc (scraping portail) | ~2024–2025 | à évaluer au téléchargement | licence à vérifier sur la page | Ingestion via adaptateur `kaggle_ma_housing` |
| **GitHub — `iliasoudghiri/Casablanca-House-Prices`** | ~3 000 annonces Mubawab Casablanca avec **lat/lon**, surface, pièces, étage, état, âge, prix | ~2020 | 3 k obs. | pas de licence data explicite → usage recherche interne, provenance tracée | **Seed historique 2020** (adaptateur fourni) |
| **GitHub — `zmesror/avito-trackr`** | Scraper Avito.ma (Scrapy→MySQL) | code, pas de data | — | MIT (code) | Référence technique parsing Avito |
| **GitHub — `Loubaris/Data-Immo`** | Scraper multi-sites (avito, mubawab, charika) → Excel | code | — | référence technique | Référence technique |
| **Dataset universitaire 2019–2021 (Avito+Mubawab+Sarouty, >18 000 obs.)** | Appartements à vendre à Casablanca | 2019–2021 | 18 k+ | **non localisé depuis cet environnement** (Kaggle/HF/ResearchGate inaccessibles au réseau du conteneur) | **Protocole de chasse §1.4** + adaptateur générique prêt (`university_2019_2021`) |
| Hugging Face | Rien de spécifique Maroc immobilier trouvé (le dataset `UBC-NLP/Casablanca` est un corpus **vocal**, faux ami) | — | — | — | — |

### 1.4 Protocole de récupération du dataset universitaire 2019–2021

À exécuter depuis un poste avec accès Internet complet (le réseau de ce conteneur
bloque Kaggle/HF/ResearchGate/Scholar) :

1. **Kaggle** : rechercher `casablanca apartments`, `morocco real estate`, `avito maroc`,
   `appartement casablanca` (les 3 sources combinées Avito+Mubawab+Sarouty sont une
   signature forte ; filtrer par date de création 2020–2022).
2. **Google Scholar / ResearchGate** : requêtes
   `"Casablanca" apartments price prediction "web scraping" 2019 2021`,
   `prédiction prix appartements Casablanca apprentissage automatique`,
   et versions arabes (`توقع أسعار الشقق الدار البيضاء`). Examiner les sections
   *Data availability* ; contacter les auteurs (les datasets d'articles marocains sont
   fréquemment partagés sur demande ou en annexe Mendeley Data).
3. **Mendeley Data / Zenodo / Data in Brief** : `Casablanca apartments`, `Moroccan real estate`.
4. **Google dorks** : `filetype:csv casablanca appartement prix`, `site:github.com casablanca avito mubawab sarouty csv`.
5. Une fois le fichier obtenu : le déposer dans `data/incoming/`, créer un manifest
   (voir `docs/OPERATIONS.md`) et lancer `casadata ingest-dataset` — l'adaptateur
   générique CSV mappe les colonnes et trace la provenance.

### 1.5 Archives web (profondeur historique 2012 → 2026)

La **Wayback Machine** (web.archive.org) archive Mubawab, Avito, Sarouty et les pages
« prix au m² » depuis ~2012. L'API CDX permet de lister les snapshots par motif d'URL
(`mubawab.ma/fr/a/*` = pages annonces, pages listing par quartier, pages référentiel prix).
C'est la **seule voie réaliste vers un historique 2015–2021 au niveau annonce** en
dehors des datasets déjà constitués. Rendement estimé : dizaines de milliers de pages
annonces + séries prix/m² par quartier reconstituées. Un harvester CDX poli est fourni
(`casadata collect wayback`). Limite : échantillonnage biaisé vers les pages populaires ;
à traiter comme source `confidence=medium` et le flagger comme telle.

### 1.6 Données géographiques

- **OpenStreetMap** : quartiers/suburbs de Casablanca, lignes de tram T1–T4 (+ busway),
  écoles, universités, centres commerciaux, hôpitaux, plages, axes ; extraction via
  Overpass API ou extrait Geofabrik Maroc. Licence ODbL — compatible usage interne
  avec attribution.
- **HCP RGPH 2024** : population/ménages par arrondissement → densité, socio-démo.
- **Nomenclature maison** : gazetteer Casablanca (préfecture → arrondissement →
  quartier → micro-quartier, avec alias FR/AR/translittérations) fourni dans
  `src/casadata/geo/gazetteer.json` — c'est le référentiel pivot de toute la géographie.

### 1.7 Sources commerciales (fallback)

- **Apify** (acteurs Avito/Mubawab existants) : ~5–30 $/1000 résultats. Utile en
  dépannage ponctuel, non nécessaire si les collecteurs tournent.
- **CEIC / Statista** : agrégats payants redondants avec l'IPAI gratuit → écartés.
- **Agenz API** : pas d'API publique documentée ; un partenariat data est une option
  long terme (ils revendiquent transactions + cadastre).

---

## 2. Historique disponible — synthèse

| Voie | Période | Granularité | Effort |
|---|---|---|---|
| IPAI BKAM×ANCFCC | **2006 → 2026** | trimestre × ville × type d'actif + volumes | faible (20 ans de PDF/XLS) |
| Dataset universitaire (si retrouvé) | 2019–2021 | annonce | faible-moyen |
| GitHub seed (Casablanca-House-Prices) | ~2020 | annonce (3 k, GPS) | faible |
| Wayback Machine | ~2012 → 2026 | annonce (échantillon) + agrégats quartier | moyen-élevé |
| Kaggle housing Morocco | ~2024–2025 | annonce | faible |
| **Collecte propre** | **2026 → ∞** | annonce × jour, exhaustif | continu |

Conclusion : la profondeur 2015→2026 se construit en **couches** — agrégats
institutionnels (excellents, 20 ans), couches annonces partielles (2019–2021, 2020,
2024–2025), archives web opportunistes, puis à partir d'aujourd'hui un flux propre,
exhaustif et quotidien. Chaque couche est tracée avec sa source et sa confiance.

## 3. Taille potentielle

- Stock actif estimé Casablanca (tous portails, avant dédup) : ~40–70 k annonces vente,
  ~15–30 k location.
- Après déduplication inter/intra-portails : **~30–50 k biens uniques actifs**, flux de
  nouveaux biens ~5–10 k/mois.
- Collecte quotidienne → **10–25 M d'observations/an** (une annonce active = 1 obs/jour).
  En cadence hebdomadaire : 1,5–3,5 M/an.
- Horizon 12 mois : **>100 k annonces uniques, >500 k observations** atteints même en
  hebdomadaire ; en quotidien, plusieurs millions. L'objectif §15 du cahier des charges
  est réaliste **sans forcer le volume**.

## 4. Accessibilité, contraintes et conformité

1. **Contrainte découverte pendant cette session** : l'environnement d'exécution
   (conteneur Claude Code) bloque l'egress vers les portails marocains, Kaggle, HF et
   archive.org (seuls GitHub et les registres de paquets passent). **La collecte doit
   tourner chez vous** : machine locale, VPS (~5 €/mois), ou GitHub Actions planifié.
   Tout le code est conçu pour cela (aucune dépendance à cet environnement).
2. **robots.txt** : vérification **à l'exécution** intégrée au client HTTP
   (`casadata.collect.robots`) — chaque URL est testée contre le robots.txt du domaine
   avant requête ; refus = skip loggé. Les robots.txt n'étant pas consultables depuis ce
   conteneur, aucune hypothèse n'est codée en dur.
3. **Politesse** : 1 requête / 3–5 s par domaine (configurable), User-Agent identifiable,
   backoff exponentiel sur 429/5xx, fenêtres horaires creuses, cache d'étag.
4. **Pas de contournement** : ni CAPTCHA, ni login, ni paywall, ni obfuscation d'IP.
   Si un portail durcit l'accès → on bascule sur les autres sources (multi-sources by design).
5. **Données personnelles (loi 09-08 / CNDP)** : on ne stocke **pas** les téléphones ni
   noms de particuliers. Les vendeurs sont identifiés par un hash salé de l'identifiant
   plateforme + type (particulier/agence) + nom **uniquement si agence** (personne morale).
   Les photos ne sont pas téléchargées par défaut (URLs conservées) — activable si
   l'usage est validé.
6. **Copyright** : textes bruts conservés en archive privée de travail (raw store),
   jamais republiés ; les analyses diffusées sont des agrégats/dérivés.
7. **Coûts** : 0 € en logiciel ; VPS optionnel ~5 €/mois ; Apify optionnel en secours.

## 5. Stratégie de combinaison retenue

```
                    ┌──────────────── COUCHE CALIBRATION ───────────────┐
  IPAI 2006-2026 (transactions) ── Agenz/Yakeey (prix/m² quartier) ── HCP
                    └──────────────┬────────────────────────────────────┘
                                   │ ratios listing→transaction, tendances longues
                                   ▼
   Mubawab (structure+GPS) ─┐
   Avito (volume)           ├─► RAW STORE ─► NORMALISATION ─► DÉDUP ─► BASE OBSERVATIONS
   Sarouty (agences)        │   (JSONL.gz)    (gazetteer géo)  (score)   (DuckDB/Parquet)
   Wayback (2012-2026)      │
   Datasets seed (2019-25) ─┘
```

- **Primaire quotidien** : Mubawab + Avito (vente ET location, Casablanca entière).
- **Hebdomadaire** : Sarouty ; référentiels Agenz + Yakeey (agrégats quartier) ; 
- **Trimestriel** : IPAI, taux BKAM.
- **One-shot** : datasets seed, harvest Wayback, OSM/HCP.

## 6. Architecture technique retenue

| Choix | Décision | Justification |
|---|---|---|
| Langage | **Python 3.10+** | écosystème scraping/data, vos exemples, maintenance |
| Base | **DuckDB** (fichier unique) + **Parquet** exports | zéro admin, analytique columnar rapide jusqu'à des centaines de millions de lignes ; migration Postgres triviale si multi-écrivains un jour |
| Données brutes | **JSONL gzip append-only** par `scrape_run` (`data/raw/`) | reproductibilité totale : on peut re-parser sans re-scraper |
| HTTP | `httpx` + limiteur + robots checker maison | léger, pas de framework lourd au départ |
| Parsing | selectolax/BeautifulSoup au moment du branchement live | les collecteurs livrés ici séparent fetch/parse pour être testables hors-ligne |
| Orchestration | `casadata` CLI + cron/systemd timer (script fourni) | simple au début, scalable (chaque run est idempotent et repris) |
| Monitoring | table `scrape_run` + `casadata stats` + flags qualité | suffisant phase 1 |
| Dédup | blocking + scoring pondéré, table `property_link` avec confiance | jamais de fusion destructive |

## 7. Schéma de données (résumé — détail dans `docs/DATA_MODEL.md`)

Entités : `source`, `scrape_run`, `location` (gazetteer 4 niveaux + lat/lon),
`seller` (hashé, type), `property` (bien réel, créé par la dédup), `property_link`
(annonce↔bien, score, méthode), `listing` (annonce, transaction_type=sale|rent),
`listing_observation` (**append-only** : prix/loyer/attributs à chaque passage),
`listing_event` (first_seen, price_change, disappeared, reappeared),
`market_aggregate` (séries IPAI, Agenz, Yakeey, HCP), `poi` (géo OSM),
`dataset_manifest` (provenance des imports). Vues dérivées : `price_history`,
`rent_history`, `listing_lifecycle` (durée d'exposition, nb baisses, % baisse totale,
délai 1ère baisse), `latest_listings`.

Toute ligne porte `source_id`, `scrape_run_id`, `observed_at`, `raw_ref` (pointeur vers
le JSONL brut), `confidence` et `quality_flags` — la question « d'où vient cette donnée,
quand, avec quelle qualité ? » a une réponse par construction.

## 8. Plan de collecte

- **Phase 0 (fait ici)** : schéma, gazetteer, ingestion, dédup, cycle d'observation,
  adaptateurs seed, CLI, tests.
- **Phase 1 (chez vous, semaine 1)** : vérifier robots.txt/ToS réels des portails,
  brancher les parsers live Mubawab puis Avito (vente+location Casablanca), première
  collecte complète, `casadata stats`.
- **Phase 2 (semaines 2–4)** : cron quotidien, ingestion IPAI 2006–2026 + référentiels
  quartiers, seed datasets (GitHub, Kaggle, dataset universitaire si retrouvé), OSM POI.
- **Phase 3 (mois 2–3)** : harvest Wayback, Sarouty, tuning dédup sur données réelles,
  premiers rapports quartier (prix/m², délais, baisses).
- **Phase 4 (mois 3+)** : modèles hédoniques simples (prix, loyer), comparables
  locatifs → rendement, score d'opportunité (le module `analytics` pose déjà les requêtes).

## 9. Plan de validation

1. Tests unitaires (schéma, géo-normalisation, dédup, cycle d'observation) — livrés.
2. Contrôles à chaque run : taux de parse, % localisations normalisées, % prix
   aberrants (flag, jamais suppression : bornes 1 500–120 000 MAD/m² vente,
   20–500 MAD/m²/mois location), volume vs run précédent (alerte si -30 %).
3. Recoupement mensuel : médianes prix/m² par quartier vs référentiels Agenz/Yakeey ;
   tendance trimestrielle vs IPAI Casablanca.
4. Audit dédup : échantillon manuel de paires à score 0,5–0,8.

## 10. Volume final atteignable (estimation honnête)

| Horizon | Biens uniques | Observations | Historique couvert |
|---|---|---|---|
| 3 mois | 40–60 k | 1–4 M | 2019→2026 (couches seed) |
| 12 mois | 100–150 k | 8–25 M | 2015→2027 (avec Wayback) |
| 3 ans | 250–400 k | 50–120 M | 2006→2029 (agrégats) + 2019→2029 (annonces) |

DuckDB/Parquet tient ces volumes sur une machine unique sans difficulté.

---

*Rapport produit après exploration web (WebSearch) le 24/08/2026. Les robots.txt et ToS
des portails doivent être relus depuis l'environnement d'exécution final avant la
première collecte live — le client HTTP le fait automatiquement, mais une lecture
humaine des ToS reste recommandée.*
