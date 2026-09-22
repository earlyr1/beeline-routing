#!/usr/bin/env bash
# Открывает день региона на живом сервисе и печатает план: бригады, километры, заявки без исполнителя
# и откуда взят утренний план. Нужен поднятый сервис (make up). Регион первым аргументом, по умолчанию east.
set -euo pipefail

API=${API:-http://localhost:8000/api}
REGION=${1:-east}
STATE=$(mktemp)
trap 'rm -f "$STATE"' EXIT

id=$(curl -sf -X POST "$API/scenarios/$REGION" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dataset_id"])')
status=processing
for _ in $(seq 1 60); do
  status=$(curl -sf "$API/datasets/$id" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  [ "$status" = "processing" ] || break
  sleep 2
done
if [ "$status" != "ready" ]; then
  echo "  день не собрался: статус $status"
  exit 1
fi

curl -sf -X POST "$API/datasets/$id/plan" -H 'Content-Type: application/json' -d '{}' -o "$STATE"
python3 - "$STATE" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
plan = state["plan"]
used = [route for route in plan["routes"] if route["visits"]]
night = state.get("precomputed")
source = f"ночной поиск {night['search_minutes']:.0f} мин" if night else "поиск при загрузке"
km = plan["metrics"]["total_km"]
print(f"  {len(used)} бригад, {km} км, без исполнителя {len(plan['unassigned'])}, утренний план: {source}")
PY
