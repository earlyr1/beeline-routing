#!/usr/bin/env bash
# Готовит граф OSRM (профиль car, алгоритм MLD) для Москвы и области.
# Запуск: docker compose --profile prepare run --rm osrm-prepare
set -euo pipefail

DATA_DIR="${DATA_DIR:-/data}"
PBF_URL="${PBF_URL:-https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf}"
# lon_min,lat_min,lon_max,lat_max: Москва, Новая Москва, Зеленоград, Домодедово, Кашира, Ступино
OSRM_BBOX="${OSRM_BBOX:-36.6,54.6,38.9,56.3}"

SOURCE="$DATA_DIR/central-fed-district-latest.osm.pbf"
CLIPPED="$DATA_DIR/moscow.osm.pbf"
GRAPH="$DATA_DIR/moscow.osrm"
DONE="$DATA_DIR/.prepared-${OSRM_BBOX//,/_}"

if [[ -f "$DONE" ]]; then
  echo "Граф уже собран для bbox $OSRM_BBOX, пропускаю"
  exit 0
fi

mkdir -p "$DATA_DIR"
started=$(date +%s)
step() { echo "[$(( $(date +%s) - started ))s] $*"; }

if [[ ! -f "$SOURCE" ]]; then
  step "1/5 Скачиваю $PBF_URL"
  curl -fL --retry 5 --retry-delay 5 -C - -o "$SOURCE.part" "$PBF_URL"
  mv "$SOURCE.part" "$SOURCE"
fi

step "2/5 Вырезаю bbox $OSRM_BBOX"
osmium extract --bbox "$OSRM_BBOX" --strategy complete_ways --overwrite -o "$CLIPPED" "$SOURCE"

step "3/5 osrm-extract"
osrm-extract -p /opt/car.lua "$CLIPPED"

step "4/5 osrm-partition"
osrm-partition "$GRAPH"

step "5/5 osrm-customize"
osrm-customize "$GRAPH"

touch "$DONE"
step "Готово: $GRAPH"
