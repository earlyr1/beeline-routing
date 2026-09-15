"""Проверка помощника на живом backend: загрузка бандла, план, сообщение, предложения, применение.

Внутри контейнера:  docker compose exec backend python scripts/smoke_proposals.py "Арташкин заболел после обеда"
Флаг --approve применяет все предложения. У backend должны быть заданы LLM_BASE_URL и LLM_MODEL.
Только стандартная библиотека Python.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from smoke_api import call, upload


def main(argv: list[str]) -> int:
    approve = "--approve" in argv
    args = [arg for arg in argv[1:] if arg != "--approve"]
    text = args[0] if args else "Первая бригада заболела после обеда"
    base = args[1] if len(args) > 1 else "http://127.0.0.1:8001/api"
    path = Path(args[2] if len(args) > 2 else "/app/data/bundles/east/bundle.json")

    if not call("GET", f"{base}/config")["llm_enabled"]:
        raise SystemExit("LLM не настроен: задайте LLM_BASE_URL и LLM_MODEL и перезапустите backend.")
    dataset_id = upload(base, path)
    while (status := call("GET", f"{base}/datasets/{dataset_id}"))["status"] == "processing":
        time.sleep(0.5)
    if status["status"] != "ready":
        raise SystemExit(f"Предподсчёт не удался: {status['error']}")
    call("POST", f"{base}/datasets/{dataset_id}/plan")

    started = time.monotonic()
    body = json.dumps({"text": text}).encode()
    reply = call("POST", f"{base}/datasets/{dataset_id}/chat", body, {"Content-Type": "application/json"})
    print(f"ответ помощника за {time.monotonic() - started:.1f} с")
    if reply["clarification"]:
        print("уточнение:", reply["clarification"])
    for proposal in reply["proposals"]:
        event = json.dumps(proposal["event"], ensure_ascii=False)
        error = f" | ошибка: {proposal['error']}" if proposal["error"] else ""
        print(f"{proposal['id']} [{proposal['status']}] {event} | {proposal['rationale']}{error}")

    if approve and any(proposal["status"] == "pending" for proposal in reply["proposals"]):
        result = call("POST", f"{base}/datasets/{dataset_id}/proposals/approve-all")
        for proposal in result["proposals"]:
            print(
                f"{proposal['id']} -> {proposal['status']}"
                + (f": {proposal['error']}" if proposal["error"] else "")
            )
        metrics = result["state"]["plan"]["metrics"]
        print(
            f"версия плана {result['state']['version']}, инженеров {metrics['engineers_used']}, "
            f"км {metrics['total_km']}, не назначено {metrics['unassigned']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
