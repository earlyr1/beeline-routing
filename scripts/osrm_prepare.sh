#!/usr/bin/env bash
# Готовит граф OSRM (профиль car, алгоритм MLD) для Москвы и области.
# Запуск: docker compose --profile prepare run --rm osrm-prepare
set -euo pipefail

DATA_DIR="${DATA_DIR:-/data}"
# Выгрузка закреплена датой: из неё 23.09.2026 собран граф стенда жюри, на нём посчитаны ночные планы
# (data/bundles/<регион>/night_plan.json). Свежая выгрузка дала бы другие минуты и километры, и ночные планы
# не подхватились бы. Даже та же выгрузка, собранная заново, расходится со стендом на метры в отдельных парах,
# но это меньше, чем разница, которая накапливается в OSM за недели. Geofabrik хранит датированные файлы;
# если этот когда-нибудь уберут, скачивается свежая выгрузка.
PBF_URL="${PBF_URL:-https://download.geofabrik.de/russia/central-fed-district-260922.osm.pbf}"
PBF_FALLBACK_URL="${PBF_FALLBACK_URL:-https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf}"
# lon_min,lat_min,lon_max,lat_max: Москва, Новая Москва, Зеленоград, Домодедово, Кашира, Ступино
OSRM_BBOX="${OSRM_BBOX:-36.6,54.6,38.9,56.3}"

SOURCE="$DATA_DIR/central-fed-district.osm.pbf"
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
  if ! curl -fL --retry 5 --retry-delay 5 -C - -o "$SOURCE.part" "$PBF_URL"; then
    step "Выгрузки $PBF_URL нет, беру свежую $PBF_FALLBACK_URL: ночные планы на таком графе могут не подхватиться"
    rm -f "$SOURCE.part"
    curl -fL --retry 5 --retry-delay 5 -o "$SOURCE.part" "$PBF_FALLBACK_URL"
  fi
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

# osrm-routed читает только moscow.osrm.*, исходные PBF (около 1 ГБ) больше не нужны.
# Для другого bbox источник скачается заново.
rm -f "$CLIPPED" "$SOURCE"
touch "$DONE"
step "Готово: $GRAPH"
step "Граф занимает на диске: $(du -ch "$GRAPH".* 2>/dev/null | tail -n 1 | cut -f 1)"
