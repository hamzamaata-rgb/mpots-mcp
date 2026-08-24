#!/usr/bin/env bash
# Collecte quotidienne casadata — à lancer via cron depuis un environnement
# avec accès Internet complet. Chaque étape est indépendante: un échec
# n'empêche pas les suivantes (les runs échoués restent tracés en base).
set -u
cd "$(dirname "$0")/.."

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }

for source in mubawab avito; do
  for tx in sale rent; do
    log "collecte $source $tx"
    casadata collect portal "$source" "$tx" --mark-missing || log "ECHEC $source $tx"
  done
done

# hebdomadaire: sarouty le lundi
if [ "$(date -u +%u)" = "1" ]; then
  for tx in sale rent; do
    casadata collect portal sarouty "$tx" --mark-missing || log "ECHEC sarouty $tx"
  done
fi

log "déduplication"
casadata dedupe || log "ECHEC dedupe"
casadata stats
log "export parquet hebdo"
if [ "$(date -u +%u)" = "7" ]; then casadata export; fi
log "terminé"
