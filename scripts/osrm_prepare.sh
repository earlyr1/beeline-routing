#!/usr/bin/env bash
# Готовит граф OSRM (профиль car, алгоритм MLD) для Москвы и области.
# Запуск: docker compose --profile prepare run --rm osrm-prepare
set -euo pipefail

DATA_DIR="${DATA_DIR:-/data}"
# Сначала скачивается готовый граф стенда жюри (infra/yc/publish_graph.sh): на нём посчитаны ночные планы
# (data/bundles/<регион>/night_plan.json), и только с ним их отпечаток задачи совпадает. Граф, собранный заново
# даже из той же выгрузки OSM, расходится со стендом на метры в отдельных парах точек, и ночные планы не
# подхватываются. GRAPH_URL= (пусто) — не скачивать, а собрать свой. Готовый граф — только для bbox по умолчанию.
GRAPH_URL="${GRAPH_URL-https://storage.yandexcloud.net/beeline-routing-osrm/moscow-osrm-260922.tar.gz}"
GRAPH_SHA256="${GRAPH_SHA256-761bef957209006aad14543c5b0d314e46dfc269a0af109203e5ac3499e53053}"
DEFAULT_BBOX=36.6,54.6,38.9,56.3
# Если готовый граф недоступен, свой собирается из той же выгрузки, что и граф стенда: 23.09.2026 он собран из
# выгрузки Geofabrik от 22.09. Geofabrik хранит датированные файлы; если этот уберут, скачивается свежая выгрузка.
PBF_URL="${PBF_URL:-https://download.geofabrik.de/russia/central-fed-district-260922.osm.pbf}"
PBF_FALLBACK_URL="${PBF_FALLBACK_URL:-https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf}"
# lon_min,lat_min,lon_max,lat_max: Москва, Новая Москва, Зеленоград, Домодедово, Кашира, Ступино
OSRM_BBOX="${OSRM_BBOX:-$DEFAULT_BBOX}"

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
disk_usage() { du -ch "$GRAPH".* 2>/dev/null | tail -n 1 | cut -f 1; }
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d ' ' -f 1
}

if [[ -n "$GRAPH_URL" && "$OSRM_BBOX" == "$DEFAULT_BBOX" ]]; then
  archive="$DATA_DIR/graph.tar.gz"
  step "Скачиваю готовый граф стенда $GRAPH_URL"
  if curl -fL --retry 5 --retry-delay 5 -o "$archive" "$GRAPH_URL" && [[ "$(sha256 "$archive")" == "$GRAPH_SHA256" ]]; then
    tar -xzf "$archive" -C "$DATA_DIR"
    rm -f "$archive"
    touch "$DONE"
    step "Готово: $GRAPH — граф стенда, ночные планы подхватятся"
    step "Граф занимает на диске: $(disk_usage)"
    exit 0
  fi
  rm -f "$archive"
  step "Готовый граф не скачался или не совпала контрольная сумма: собираю свой, ночные планы на нём могут не подхватиться"
fi

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
step "Граф занимает на диске: $(disk_usage)"
