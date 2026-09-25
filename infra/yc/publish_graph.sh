#!/usr/bin/env bash
# Выкладывает готовый граф OSRM стенда жюри в публичный бакет Object Storage, чтобы scripts/osrm_prepare.sh на
# любом клоне скачивал ровно его, а не собирал свой. Граф, собранный заново даже из той же выгрузки OSM, расходится
# со стендом на метры в отдельных парах точек, отпечаток задачи получается другим, и ночные планы
# (data/bundles/<регион>/night_plan.json) не подхватываются.
#
# Запуск: infra/yc/publish_graph.sh [архив.tar.gz]
# Без аргумента архив собирается из локального тома OSRM (beeline-routing_osrm-data): в нём должен лежать граф
# стенда — тот, на котором посчитаны ночные планы (проверка: пометка «Утренний план: ночной поиск» на локальном
# запуске). В конце печатает адрес и sha256 для GRAPH_URL и GRAPH_SHA256 в scripts/osrm_prepare.sh.
# Бакет удаляет teardown.sh. Хранение ~0.25 ГБ стоит копейки в месяц.
set -euo pipefail

# shellcheck source=infra/yc/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

GRAPH_VOLUME=${GRAPH_VOLUME:-beeline-routing_osrm-data}
OBJECT=${GRAPH_OBJECT:-moscow-osrm-260922.tar.gz}

check_yc
need shasum

archive=${1:-}
if [[ -z $archive ]]; then
  need docker
  archive=$(mktemp -d)/$OBJECT
  step "Собираю архив графа из тома $GRAPH_VOLUME"
  docker run --rm -v "$GRAPH_VOLUME:/data:ro" -v "$(dirname "$archive"):/out" alpine \
    sh -c "cd /data && tar -cf - moscow.osrm.* | gzip -6 > /out/$OBJECT"
fi

if [[ -z $(id_of storage bucket get --name "$GRAPH_BUCKET") ]]; then
  step "Создаю публичный на чтение бакет $GRAPH_BUCKET"
  # Имя бакета глобальное на весь Object Storage: если занято, задайте своё через GRAPH_BUCKET.
  yc_ storage bucket create --name "$GRAPH_BUCKET" --default-storage-class standard \
    --max-size 1073741824 --public-read >/dev/null
fi

step "Загружаю $(basename "$archive") ($(du -h "$archive" | cut -f 1))"
"$YC" storage s3 cp "$archive" "s3://$GRAPH_BUCKET/$OBJECT"

step "Готово"
echo "GRAPH_URL=$GRAPH_BASE_URL/$GRAPH_BUCKET/$OBJECT"
echo "GRAPH_SHA256=$(shasum -a 256 "$archive" | cut -d ' ' -f 1)"
