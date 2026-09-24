"""Настоящие матрицы 2ГИС из data/transit: в репозитории они зашифрованы и расшифровываются в те же байты.

Ключ берётся из переменной окружения TRANSIT_KEY: make test берёт его из .env, CI — из секрета репозитория. Без ключа
тесты расшифровки пропускаются. Открытые <регион>.json есть только на машине, где матрицы считали и шифровали: там
расшифрованное сверяется с ними байт в байт и по отпечатку задачи дня, в CI эти проверки пропускаются.
"""

import os

import pytest

from app.api.deps import planning_context
from app.geo.transit import (
    decrypt_matrix_bytes,
    encrypted_matrix_path,
    encrypted_regions,
    load_region_matrix,
    load_transit_matrices,
    load_transit_matrix,
    transit_matrix_path,
)
from app.ingest.bundle import load_bundle
from app.planning.night import problem_fingerprint
from app.planning.session import day_problem
from app.planning.workload import DEFAULT_WORKLOAD_LEVEL, workload_weights
from app.settings import REPO_ROOT, Settings

REAL_DIR = REPO_ROOT / "data" / "transit"
REAL_KEY = (os.environ.get("TRANSIT_KEY") or "").strip()
REGIONS = sorted(path.parent.name for path in (REPO_ROOT / "data" / "bundles").glob("*/bundle.json"))
needs_key = pytest.mark.skipif(not REAL_KEY, reason="нет TRANSIT_KEY: настоящие матрицы не расшифровать")


def real_settings() -> Settings:
    return Settings.from_env(
        {"DATA_DIR": str(REPO_ROOT / "data"), "GEOCODER": "cache-only", "TRANSIT_KEY": REAL_KEY}
    )


def plaintext_or_skip(region: str) -> bytes:
    """Открытый файл региона: он есть только на машине, где матрицы считали и шифровали, — в git и в CI его нет."""
    path = transit_matrix_path(REAL_DIR, region)
    if not path.is_file():
        pytest.skip(f"нет открытого {path.name}: сравнивать не с чем")
    return path.read_bytes()


def test_every_bundle_region_has_an_encrypted_matrix():
    assert REGIONS and encrypted_regions(REAL_DIR) == REGIONS


@needs_key
@pytest.mark.parametrize("region", REGIONS)
def test_the_committed_matrix_decrypts_with_the_real_key(region):
    matrix = load_region_matrix(REAL_DIR, region, REAL_KEY)
    assert matrix is not None and matrix.region == region
    assert matrix.points and len(matrix.minutes) == len(matrix.points)


@needs_key
@pytest.mark.parametrize("region", REGIONS)
def test_the_decrypted_matrix_is_byte_identical_to_the_plaintext(region):
    data = plaintext_or_skip(region)
    token = encrypted_matrix_path(REAL_DIR, region).read_bytes()
    assert decrypt_matrix_bytes(token, REAL_KEY) == data


@needs_key
@pytest.mark.parametrize("region", REGIONS)
def test_the_problem_fingerprint_is_the_same_with_decrypted_matrices(region):
    """Отпечаток задачи дня на расшифрованных матрицах тот же, что на открытых: ночные планы остаются в силе.

    Открытые матрицы читаются так, как их читал сервис до шифрования: все <регион>.json каталога по именам файлов.
    OSRM не нужен: матрицы 2ГИС входят в отпечаток и на расстояниях по прямой.
    """
    plaintext_or_skip(region)
    plain = [load_transit_matrix(path) for path in sorted(REAL_DIR.glob("*.json"))]
    decrypted = load_transit_matrices(REAL_DIR, REAL_KEY)
    assert decrypted == plain

    settings = real_settings()
    bundle = load_bundle(settings.bundles_dir / region / "bundle.json")
    weights = workload_weights(DEFAULT_WORKLOAD_LEVEL)

    def fingerprint(transit) -> str:
        ctx = planning_context(settings, None, None, transit=transit)
        problem = day_problem(bundle.requests, bundle.engineers, ctx, DEFAULT_WORKLOAD_LEVEL, True)
        return problem_fingerprint(problem, weights)

    assert fingerprint(decrypted) == fingerprint(plain)
    # Матрицы в отпечаток действительно входят: без них он другой.
    assert fingerprint(decrypted) != fingerprint(())
