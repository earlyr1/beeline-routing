#!/usr/bin/env bash
# Меняет гарантированную долю ядер и память машины жюри: остановка, правка, запуск.
# Запуск: infra/yc/resize.sh <core_fraction> <memory_gb>
#   infra/yc/resize.sh 100 4   на день защиты: ядра без ограничений
#   infra/yc/resize.sh 50 8    на сборку графа OSRM (workflow osrm-graph)
#   infra/yc/resize.sh 50 4    обычный режим
# Ice Lake (standard-v3) допускает долю 20, 50 или 100. Машина простаивает минуту-две; статический IP, диск
# и тома Docker сохраняются, контейнеры поднимаются сами (restart: unless-stopped).
set -euo pipefail

# shellcheck source=infra/yc/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [[ $# -ne 2 ]]; then
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
fi
CORE_FRACTION=$1
MEMORY_GB=$2
case $CORE_FRACTION in
  20 | 50 | 100) ;;
  *)
    echo "Доля ядер для standard-v3: 20, 50 или 100, а не $CORE_FRACTION" >&2
    exit 2
    ;;
esac
if [[ ! $MEMORY_GB =~ ^[1-9][0-9]*$ ]]; then
  echo "Память — целое число гигабайт, а не $MEMORY_GB" >&2
  exit 2
fi

check_yc

step "Останавливаю $VM_NAME"
yc_ compute instance stop --name "$VM_NAME" >/dev/null
step "Ставлю долю ядер $CORE_FRACTION% и $MEMORY_GB ГБ"
# Если правка не прошла (например, память вне пределов для этой доли), машина всё равно запускается обратно.
if ! yc_ compute instance update --name "$VM_NAME" --core-fraction "$CORE_FRACTION" --memory "$MEMORY_GB" >/dev/null; then
  echo "Правка не прошла, запускаю машину в прежнем размере" >&2
  yc_ compute instance start --name "$VM_NAME" >/dev/null
  exit 1
fi
step "Запускаю"
yc_ compute instance start --name "$VM_NAME" >/dev/null
yc_ compute instance get --name "$VM_NAME" --format json |
  jq -r '"Сейчас: \(.resources.cores) ядра по \(.resources.core_fraction)%, \((.resources.memory | tonumber) / 1073741824) ГБ, \(.status)"'
echo "Контейнеры поднимутся сами за минуту-две после загрузки."
