import hashlib
import json

from app.domain.models import Metrics
from app.geo.matrix import TrafficProfile
from app.ingest.geocode import GeoHit
from app.synth.config import SynthConfig
from app.synth.prepare import build_parser, prepare_region, self_check
from tests.test_synth import CONFIG

SYNTHETIC = (
    "Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Гигабитное подключение\r\n"
    "11;Локальная заявка;Нет линка;17.08.2026 14:00;17.08.2026 16:00;Таганский;Город Москва, ул.Первая, д. 1;Нет\r\n"
    "12;Локальная заявка;Нет линка;17.08.2026 10:00;17.08.2026 12:00;Таганский;Город Москва, ул.Вторая, д. 2;Нет\r\n"
    ";;;;;;;\r\n"
    "Адрес Офиса;г. Москва, ул Юных Ленинцев, д 83с 4;;;;;;\r\n"
)
CONTROL = (
    "Заявка;Тип заявки BK;Статус BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Бригада;Гигабитное подключение\r\n"
    "305;Локальная заявка;Выполнена;Нет линка;17.08.2026 14:00;17.08.2026 16:00;Таганский;"
    "Город Москва, ул.Первая, д. 1, кв. 5;Бригада А;Нет\r\n"
    "306;Локальная заявка;Отменена;Нет линка;17.08.2026 10:00;17.08.2026 12:00;Таганский;"
    "Город Москва, ул.Вторая, д. 2, кв. 7;Бригада Б;Нет\r\n"
)


class HashGeocoder:
    """Детерминированные точки в пределах ~3 км от центра Москвы."""

    def lookup(self, query):
        digest = hashlib.sha256(query.encode()).digest()
        return GeoHit(55.74 + digest[0] / 255 * 0.03, 37.60 + digest[1] / 255 * 0.05, "building")


def test_prepare_region_end_to_end(tmp_path):
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "t_synthetic.csv").write_bytes(SYNTHETIC.encode("cp1251"))
    (tmp_path / "data" / "raw" / "t_control.csv").write_bytes(CONTROL.encode("utf-8"))
    cfg = SynthConfig.load(CONFIG).model_copy(deep=True)
    cfg.regions = {
        "t": cfg.regions["east"].model_copy(
            update={"control": "data/raw/t_control.csv", "synthetic": "data/raw/t_synthetic.csv"}
        )
    }

    result = prepare_region(
        "t",
        cfg,
        repo_root=tmp_path,
        geocoder=HashGeocoder(),
        osrm=None,
        cache=None,
        time_limit_s=1,
        traffic=TrafficProfile({}),
    )

    assert result.fcfs.metrics.engineers_used == 2
    assert result.optimized.metrics.engineers_used == 1
    assert result.self_check_ok
    assert len(result.bundle.engineers) == 2 and len(result.bundle.requests) == 2
    assert result.bundle.control_plan.metrics.engineers_used == 2
    assert "| Оптимизированный (OR-Tools) | 1 |" in result.report
    cache = json.loads((tmp_path / "data" / "geocode_cache.json").read_text(encoding="utf-8"))
    assert "Москва, Юных Ленинцев улица, 83с4" in cache


def test_self_check_requires_strict_improvement():
    worse = Metrics(engineers_used=3, km_per_engineer={}, total_km=10, assigned=5, unassigned=0)
    better = Metrics(engineers_used=2, km_per_engineer={}, total_km=12, assigned=5, unassigned=0)
    assert self_check(worse, better)[0]
    assert not self_check(better, better)[0]
    assert not self_check(better, worse)[0]


def test_cli_time_limit_defaults_to_five_seconds():
    assert build_parser().parse_args([]).time_limit == 5
    assert build_parser().parse_args(["--time-limit", "10"]).time_limit == 10
