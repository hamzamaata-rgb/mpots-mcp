-- casadata — schéma DuckDB
-- Principe : append-only pour les observations ; toute ligne est traçable
-- (source, run, horodatage, pointeur brut, confiance, flags qualité).

CREATE SEQUENCE IF NOT EXISTS seq_source START 1;
CREATE SEQUENCE IF NOT EXISTS seq_run START 1;
CREATE SEQUENCE IF NOT EXISTS seq_location START 1;
CREATE SEQUENCE IF NOT EXISTS seq_seller START 1;
CREATE SEQUENCE IF NOT EXISTS seq_listing START 1;
CREATE SEQUENCE IF NOT EXISTS seq_observation START 1;
CREATE SEQUENCE IF NOT EXISTS seq_event START 1;
CREATE SEQUENCE IF NOT EXISTS seq_property START 1;
CREATE SEQUENCE IF NOT EXISTS seq_aggregate START 1;
CREATE SEQUENCE IF NOT EXISTS seq_poi START 1;
CREATE SEQUENCE IF NOT EXISTS seq_manifest START 1;

-- ---------------------------------------------------------------- SOURCE
CREATE TABLE IF NOT EXISTS source (
    source_id   BIGINT PRIMARY KEY DEFAULT nextval('seq_source'),
    code        VARCHAR NOT NULL UNIQUE,          -- 'mubawab', 'avito', 'ipai', ...
    name        VARCHAR NOT NULL,
    kind        VARCHAR NOT NULL CHECK (kind IN ('portal','institutional','dataset','archive','aggregate','geo')),
    base_url    VARCHAR,
    notes       VARCHAR,
    created_at  TIMESTAMP NOT NULL DEFAULT current_timestamp
);

-- ------------------------------------------------------------- SCRAPE_RUN
CREATE TABLE IF NOT EXISTS scrape_run (
    run_id          BIGINT PRIMARY KEY DEFAULT nextval('seq_run'),
    source_id       BIGINT NOT NULL REFERENCES source(source_id),
    scope           VARCHAR,                      -- 'casablanca/sale', 'casablanca/rent', 'full', ...
    method          VARCHAR NOT NULL,             -- 'http', 'wayback', 'dataset_import', 'manual'
    started_at      TIMESTAMP NOT NULL DEFAULT current_timestamp,
    finished_at     TIMESTAMP,
    status          VARCHAR NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','success','partial','failed')),
    pages_fetched   BIGINT DEFAULT 0,
    records_parsed  BIGINT DEFAULT 0,
    records_failed  BIGINT DEFAULT 0,
    raw_path        VARCHAR,                      -- data/raw/<source>/<run_id>.jsonl.gz
    notes           VARCHAR
);

-- --------------------------------------------------------------- LOCATION
-- Gazetteer normalisé : Casablanca -> préfecture -> arrondissement ->
-- quartier -> micro-quartier. `slug` est la clé stable.
CREATE TABLE IF NOT EXISTS location (
    location_id     BIGINT PRIMARY KEY DEFAULT nextval('seq_location'),
    slug            VARCHAR NOT NULL UNIQUE,
    city            VARCHAR NOT NULL DEFAULT 'casablanca',
    prefecture      VARCHAR,
    arrondissement  VARCHAR,
    quartier        VARCHAR,
    micro_quartier  VARCHAR,
    level           VARCHAR NOT NULL CHECK (level IN ('city','prefecture','arrondissement','quartier','micro_quartier')),
    lat             DOUBLE,
    lon             DOUBLE,
    geo_source      VARCHAR                       -- 'gazetteer', 'osm', ...
);

-- ----------------------------------------------------------------- SELLER
-- Données personnelles minimisées : identifiant plateforme hashé/salé,
-- nom conservé uniquement pour les personnes morales (agences/promoteurs).
CREATE TABLE IF NOT EXISTS seller (
    seller_id      BIGINT PRIMARY KEY DEFAULT nextval('seq_seller'),
    source_id      BIGINT NOT NULL REFERENCES source(source_id),
    external_hash  VARCHAR NOT NULL,              -- sha256(salt + platform seller id)
    seller_type    VARCHAR NOT NULL DEFAULT 'unknown'
                   CHECK (seller_type IN ('particulier','agence','promoteur','unknown')),
    agency_name    VARCHAR,                       -- personnes morales uniquement
    first_seen_at  TIMESTAMP NOT NULL DEFAULT current_timestamp,
    last_seen_at   TIMESTAMP NOT NULL DEFAULT current_timestamp,
    UNIQUE (source_id, external_hash)
);

-- --------------------------------------------------------------- PROPERTY
-- Le bien réel. Créé/alimenté par la déduplication, jamais fusion destructive.
CREATE TABLE IF NOT EXISTS property (
    property_id     BIGINT PRIMARY KEY DEFAULT nextval('seq_property'),
    property_type   VARCHAR,                      -- 'apartment','house','villa','studio','riad','land','commercial','other'
    location_id     BIGINT REFERENCES location(location_id),
    surface_m2      DOUBLE,
    rooms           INTEGER,
    bedrooms        INTEGER,
    floor           INTEGER,
    lat             DOUBLE,
    lon             DOUBLE,
    created_at      TIMESTAMP NOT NULL DEFAULT current_timestamp,
    notes           VARCHAR
);

-- ---------------------------------------------------------------- LISTING
CREATE TABLE IF NOT EXISTS listing (
    listing_id       BIGINT PRIMARY KEY DEFAULT nextval('seq_listing'),
    source_id        BIGINT NOT NULL REFERENCES source(source_id),
    external_id      VARCHAR NOT NULL,            -- id côté portail (ou hash d'URL)
    url              VARCHAR,
    transaction_type VARCHAR NOT NULL CHECK (transaction_type IN ('sale','rent')),
    property_type    VARCHAR,
    title            VARCHAR,
    seller_id        BIGINT REFERENCES seller(seller_id),
    location_id      BIGINT REFERENCES location(location_id),
    raw_location     VARCHAR,                     -- texte localisation d'origine, toujours conservé
    lat              DOUBLE,
    lon              DOUBLE,
    published_at     TIMESTAMP,                   -- date de publication annoncée par le portail
    first_seen_at    TIMESTAMP NOT NULL,
    last_seen_at     TIMESTAMP NOT NULL,
    status           VARCHAR NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active','disappeared')),
    disappeared_at   TIMESTAMP,
    geo_confidence   DOUBLE,                      -- confiance normalisation géo [0,1]
    UNIQUE (source_id, external_id)
);

-- ----------------------------------------------------- LISTING_OBSERVATION
-- APPEND-ONLY. Une ligne = l'état observé d'une annonce à un instant t.
-- On n'écrase JAMAIS un prix : chaque passage ajoute une observation.
CREATE TABLE IF NOT EXISTS listing_observation (
    observation_id   BIGINT PRIMARY KEY DEFAULT nextval('seq_observation'),
    listing_id       BIGINT NOT NULL REFERENCES listing(listing_id),
    run_id           BIGINT NOT NULL REFERENCES scrape_run(run_id),
    observed_at      TIMESTAMP NOT NULL,
    price            DOUBLE,                      -- MAD ; vente: prix, location: loyer
    rent_period      VARCHAR CHECK (rent_period IN ('month','day','year') OR rent_period IS NULL),
    charges_included BOOLEAN,
    surface_m2       DOUBLE,
    rooms            INTEGER,
    bedrooms         INTEGER,
    bathrooms        INTEGER,
    floor            INTEGER,
    floors_total     INTEGER,
    condition        VARCHAR,                     -- 'new','good','to_renovate', ...
    age_years        VARCHAR,                     -- tranche d'âge portail ('1-5', '10-20', 'new'...)
    furnished        BOOLEAN,
    attrs            JSON,                        -- elevator, parking, garage, balcony, terrace, garden, pool, ac, heating, equipped_kitchen, security, cellar, service_room, view, orientation, availability, duration...
    description      VARCHAR,
    description_hash VARCHAR,
    photos_count     INTEGER,
    photo_urls       JSON,                        -- URLs uniquement, pas de téléchargement par défaut
    raw_ref          VARCHAR,                     -- '<raw_path>#<line_no>' vers le JSON brut
    confidence       DOUBLE DEFAULT 1.0,          -- confiance source (wayback: 0.7, dataset: selon manifest)
    quality_flags    JSON                         -- ['price_outlier','surface_missing',...] — on flagge, on ne supprime pas
);
CREATE INDEX IF NOT EXISTS idx_obs_listing ON listing_observation(listing_id, observed_at);

-- ------------------------------------------------------------ LISTING_EVENT
CREATE TABLE IF NOT EXISTS listing_event (
    event_id    BIGINT PRIMARY KEY DEFAULT nextval('seq_event'),
    listing_id  BIGINT NOT NULL REFERENCES listing(listing_id),
    event_type  VARCHAR NOT NULL CHECK (event_type IN ('first_seen','price_change','disappeared','reappeared','attr_change')),
    event_at    TIMESTAMP NOT NULL,
    old_price   DOUBLE,
    new_price   DOUBLE,
    details     JSON
);
CREATE INDEX IF NOT EXISTS idx_event_listing ON listing_event(listing_id, event_at);

-- ------------------------------------------------------------ PROPERTY_LINK
-- Rapprochement annonce <-> bien réel, avec score et méthode. Plusieurs
-- annonces (multi-portails, re-publications) peuvent pointer le même bien.
CREATE TABLE IF NOT EXISTS property_link (
    property_id BIGINT NOT NULL REFERENCES property(property_id),
    listing_id  BIGINT NOT NULL REFERENCES listing(listing_id),
    score       DOUBLE NOT NULL,                  -- [0,1]
    method      VARCHAR NOT NULL,                 -- 'exact','blocking_v1','manual'
    status      VARCHAR NOT NULL DEFAULT 'auto'
                CHECK (status IN ('auto','candidate','confirmed','rejected')),
    created_at  TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (property_id, listing_id)
);

-- --------------------------------------------------------- MARKET_AGGREGATE
-- Séries agrégées externes : IPAI (BKAM/ANCFCC), référentiels Agenz/Yakeey,
-- HCP, taux BKAM. Sert de calibration listing -> transaction.
CREATE TABLE IF NOT EXISTS market_aggregate (
    aggregate_id BIGINT PRIMARY KEY DEFAULT nextval('seq_aggregate'),
    source_id    BIGINT NOT NULL REFERENCES source(source_id),
    series_code  VARCHAR NOT NULL,                -- 'ipai_apartment_casablanca', 'agenz_ppm2', ...
    geo_level    VARCHAR NOT NULL,                -- 'city','arrondissement','quartier'
    geo_slug     VARCHAR,                         -- lien vers location.slug si applicable
    period_start DATE NOT NULL,
    period_end   DATE NOT NULL,
    metric       VARCHAR NOT NULL,                -- 'price_index','price_per_m2','transactions_count','rent_per_m2','rate'
    value        DOUBLE NOT NULL,
    unit         VARCHAR,                         -- 'index_2006_100','MAD/m2','count','pct'
    collected_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    raw_ref      VARCHAR,
    UNIQUE (source_id, series_code, geo_level, geo_slug, period_start, period_end, metric)
);

-- --------------------------------------------------------------------- POI
CREATE TABLE IF NOT EXISTS poi (
    poi_id    BIGINT PRIMARY KEY DEFAULT nextval('seq_poi'),
    category  VARCHAR NOT NULL,                   -- 'tram_stop','school','university','mall','hospital','beach','cbd', ...
    name      VARCHAR,
    lat       DOUBLE NOT NULL,
    lon       DOUBLE NOT NULL,
    geo_source VARCHAR NOT NULL DEFAULT 'osm',
    tags      JSON
);

-- --------------------------------------------------------- DATASET_MANIFEST
CREATE TABLE IF NOT EXISTS dataset_manifest (
    manifest_id  BIGINT PRIMARY KEY DEFAULT nextval('seq_manifest'),
    source_id    BIGINT NOT NULL REFERENCES source(source_id),
    file_name    VARCHAR NOT NULL,
    sha256       VARCHAR,
    original_url VARCHAR,
    license      VARCHAR,
    period_start DATE,
    period_end   DATE,
    row_count    BIGINT,
    confidence   DOUBLE DEFAULT 0.8,
    ingested_at  TIMESTAMP,
    notes        VARCHAR
);

-- ------------------------------------------------------------------- VUES
-- Historique des prix : uniquement les observations où le prix change.
CREATE OR REPLACE VIEW price_history AS
SELECT o.listing_id, l.transaction_type, o.observed_at, o.price, o.surface_m2,
       CASE WHEN o.surface_m2 > 0 THEN o.price / o.surface_m2 END AS price_per_m2,
       o.confidence
FROM listing_observation o
JOIN listing l USING (listing_id)
QUALIFY price IS DISTINCT FROM lag(price) OVER (PARTITION BY o.listing_id ORDER BY o.observed_at)
        OR lag(price) OVER (PARTITION BY o.listing_id ORDER BY o.observed_at) IS NULL;

CREATE OR REPLACE VIEW rent_history AS
SELECT * FROM price_history WHERE transaction_type = 'rent';

-- Dernière observation par annonce.
CREATE OR REPLACE VIEW latest_observation AS
SELECT o.*
FROM listing_observation o
QUALIFY row_number() OVER (PARTITION BY listing_id ORDER BY observed_at DESC, observation_id DESC) = 1;

-- Cycle de vie : exposition, baisses, délais. NB: disparu != vendu.
CREATE OR REPLACE VIEW listing_lifecycle AS
WITH per AS (
    SELECT o.listing_id,
           min(o.observed_at)                            AS first_obs,
           max(o.observed_at)                            AS last_obs,
           count(*)                                      AS n_obs,
           arg_min(o.price, o.observed_at)               AS first_price,
           arg_max(o.price, o.observed_at)               AS last_price,
           count(DISTINCT o.price) - 1                   AS n_price_changes
    FROM listing_observation o
    WHERE o.price IS NOT NULL
    GROUP BY o.listing_id
)
SELECT l.listing_id, l.source_id, l.transaction_type, l.property_type, l.location_id,
       l.status, l.published_at, per.first_obs, per.last_obs, per.n_obs,
       per.first_price, per.last_price, per.n_price_changes,
       (per.last_price - per.first_price)                              AS price_drop_abs,
       CASE WHEN per.first_price > 0
            THEN (per.last_price - per.first_price) / per.first_price END AS price_drop_pct,
       date_diff('day', per.first_obs, coalesce(l.disappeared_at, per.last_obs)) AS days_on_market
FROM listing l
JOIN per ON per.listing_id = l.listing_id;
