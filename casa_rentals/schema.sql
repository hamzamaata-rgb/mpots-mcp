-- Schema base de donnees "location residentielle Casablanca".
-- Toutes les dates sont stockees en ISO 8601 (UTC pour les timestamps, YYYY-MM-DD pour les dates).
-- AUCUNE donnee personnelle n'est stockee : ni telephone, ni nom, ni email, ni URL de profil vendeur.
-- Seul `is_pro` (agence vs particulier) est conserve.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Une ligne par annonce unique jamais vue.
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,          -- 'avito' | 'avito_wayback' | 'mubawab'
    source_id       TEXT,                   -- identifiant natif si disponible
    url             TEXT UNIQUE NOT NULL,
    content_hash    TEXT NOT NULL,          -- sha256 du titre + description normalises (et expurges)

    -- Caracteristiques du bien (brutes puis normalisees)
    titre           TEXT,
    description     TEXT,                   -- expurgee de toute PII avant stockage
    quartier_raw    TEXT,
    quartier_norm   TEXT,                   -- FK logique vers quartiers.nom
    surface_m2      REAL,
    nb_pieces       INTEGER,
    nb_chambres     INTEGER,
    etage           INTEGER,
    meuble          INTEGER,                -- 0/1/NULL
    ascenseur       INTEGER,                -- 0/1/NULL
    parking         INTEGER,                -- 0/1/NULL
    is_pro          INTEGER,                -- 0/1/NULL

    -- Prix
    loyer_mad       REAL,                   -- loyer mensuel affiche
    charges_incluses INTEGER,               -- 0/1/NULL

    first_seen      TEXT NOT NULL,          -- ISO date
    last_seen       TEXT NOT NULL,
    date_publication TEXT,                  -- si le site l'expose
    statut          TEXT DEFAULT 'active',  -- 'active' | 'disparue'
    qualite         INTEGER,                -- score 0-3, cf. normalize.score_qualite
    created_at      TEXT NOT NULL,

    -- Ajouts par rapport au schema initial (cf. README, section "ecarts au schema") :
    duplicate_of    INTEGER REFERENCES listings(id),  -- annonce republiee -> pointe vers l'originale
    quartier_method TEXT,                   -- 'exact' | 'alias' | 'fuzzy' | NULL : tracabilite du score qualite
    surface_source  TEXT,                   -- 'structure' | 'texte' | NULL : idem
    exclusion       TEXT                    -- motif d'exclusion analytique ('vente', 'courte_duree', ...)
);

-- Une ligne par observation quotidienne : capture les changements de prix.
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listings(id),
    date_vue    TEXT NOT NULL,
    loyer_mad   REAL,
    position    INTEGER,                    -- rang dans la page de resultats
    UNIQUE(listing_id, date_vue)
);

-- Referentiel quartiers, revu a la main.
CREATE TABLE IF NOT EXISTS quartiers (
    nom            TEXT PRIMARY KEY,
    aliases        TEXT,                    -- variantes separees par |
    arrondissement TEXT,
    segment        TEXT,                    -- 'haut' | 'intermediaire' | 'populaire'
    perimetre      TEXT DEFAULT 'casablanca'-- 'casablanca' (commune) | 'peripherie' (hors commune)
);

-- Journal de collecte, pour documenter les biais apres coup.
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,
    source        TEXT,
    pages_vues    INTEGER,
    annonces_vues INTEGER,
    annonces_neuves INTEGER,
    erreurs       INTEGER,
    note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_quartier  ON listings(quartier_norm);
CREATE INDEX IF NOT EXISTS idx_listings_hash      ON listings(content_hash);
CREATE INDEX IF NOT EXISTS idx_listings_statut    ON listings(statut, last_seen);
CREATE INDEX IF NOT EXISTS idx_listings_dup       ON listings(duplicate_of);
CREATE INDEX IF NOT EXISTS idx_snapshots_listing  ON snapshots(listing_id);
