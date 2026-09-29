"""Бандлы дополнительных дней организаторов: data/bundles/<день>/bundle.json для кнопок на экране загрузки.

Запуск из каталога backend:
  .venv/bin/python -m scripts.extra_days

Дни описаны в config/synth_config.yaml (extra_days). Заявки собираются из выгрузки тем же путём, что и при загрузке
CSV в сервисе: повторы номеров отбрасываются, окна проверяются, адреса берутся из data/geocode_cache.json. Внешний
геокодер не вызывается: адреса этих дней уже в кэше, а без кэша день не собирается, чтобы бандл не зависел от ответа
Nominatim. Бригады и офис — из бандла региона дня, распределения диспетчеров в выгрузке нет. Ночной план дня
считает scripts.night_plan --region <день>.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.api.ingest_service import drop_repeated_ids
from app.domain.models import Bundle
from app.ingest.beeline_csv import parse_beeline_csv
from app.ingest.bundle import load_bundle, save_bundle
from app.ingest.geocode import JsonGeocodeCache, geocode_address
from app.ingest.window_check import check_windows
from app.synth.config import SynthConfig
from app.synth.requests import build_requests

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
BUNDLES_DIR = REPO_ROOT / "data" / "bundles"


def main() -> int:
    cfg = SynthConfig.load(BACKEND_DIR / "config" / "synth_config.yaml")
    cache = JsonGeocodeCache(REPO_ROOT / "data" / "geocode_cache.json")
    failed = False
    for key, day in cfg.extra_days.items():
        base = load_bundle(BUNDLES_DIR / day.region / "bundle.json")
        raw = check_windows(cfg, drop_repeated_ids(parse_beeline_csv((REPO_ROOT / day.source).read_bytes())))
        requests = build_requests(
            cfg, raw, lambda address, district: geocode_address(address, district, None, cache)
        )
        missing = [request.id for request in requests if request.lat is None or request.lon is None]
        if missing:
            print(
                f"{key}: нет координат у {len(missing)} заявок ({', '.join(missing[:5])}): адресов нет в кэше"
            )
            failed = True
            continue
        bundle = Bundle(
            region=key,
            office=base.office.model_copy(update={"region": key, "title": day.title}),
            requests=requests,
            engineers=base.engineers,
        )
        path = BUNDLES_DIR / key / "bundle.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        save_bundle(bundle, path)
        print(
            f"{key}: {len(requests)} заявок, {len(bundle.engineers)} инженеров → {path.relative_to(REPO_ROOT)}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
