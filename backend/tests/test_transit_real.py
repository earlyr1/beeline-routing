"""Настоящие матрицы 2ГИС из data/transit: в репозитории они зашифрованы и расшифровываются в те же байты.

Ключ берётся из переменной окружения TRANSIT_KEY: make test берёт его из .env, CI — из секрета репозитория. Без ключа
тесты расшифровки пропускаются. Что расшифровывается ровно в прежние открытые файлы, проверяет хэш SHA-256, закреплённый
здесь же: эта проверка идёт везде, где есть ключ, и в CI тоже. Сверка байт в байт и по отпечатку задачи дня идёт там,
где есть сами открытые файлы: на диске (машина, где матрицы считали) или в истории git (коммит PLAINTEXT_COMMIT);
в CI история неглубокая, и там эти проверки пропускаются.
"""

import hashlib
import os
import subprocess

import pytest

from app.api.deps import planning_context
from app.geo.transit import (
    MATRIX_SUFFIX,
    decrypt_matrix_bytes,
    encrypted_matrix_path,
    encrypted_regions,
    load_region_matrix,
    load_transit_matrices,
    parse_transit_matrix,
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

# Коммит, где data/transit/<регион>.json лежат в git открытыми (из них зашифрованы нынешние .enc), и SHA-256 этих
# файлов (git show 584dc5b:data/transit/<регион>.json | shasum -a 256). Расшифрованный <регион>.json.enc обязан давать
# ровно эти байты: тогда отпечатки задач те же, и ночные планы data/bundles/*/night_plan.json подходят. Пересчитали
# матрицы и зашифровали заново (make transit) — впишите хэши новых открытых файлов: shasum -a 256 data/transit/*.json.
PLAINTEXT_COMMIT = "584dc5b"
PLAINTEXT_SHA256 = {
    "east": "509ca9ddfe0de3dc82d7f366430f29945ee5935c450f78ff21ff83e58b85ba55",
    "north_west": "a6336784e88aeebbf914d5ddd2567e73d0389a5582dd615e81f4305b82a46723",
    "south_center": "5856cb8aff8e2aa6b007b436989c9c8f16177e76c353bd7f1251a2a3ee4d4d1b",
    "south_east": "27064699761ab3c5d56083863420064e9572fe1329c3f90d35cfda760d4cd8c9",
}


def real_settings() -> Settings:
    return Settings.from_env(
        {"DATA_DIR": str(REPO_ROOT / "data"), "GEOCODER": "cache-only", "TRANSIT_KEY": REAL_KEY}
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_plaintext(region: str) -> bytes | None:
    """Открытый файл региона из истории git, если там тот самый, чей хэш закреплён; иначе None.

    Нет git (тесты в контейнере), неглубокий клон (CI) или хэши уже от пересчитанных матриц — None.
    """
    try:
        blob = subprocess.run(
            ["git", "show", f"{PLAINTEXT_COMMIT}:data/transit/{region}{MATRIX_SUFFIX}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return blob if sha256(blob) == PLAINTEXT_SHA256.get(region) else None


def plaintext_or_skip(region: str) -> bytes:
    """Открытый файл региона: с диска (машина, где матрицы считали и шифровали) или из истории git, иначе skip."""
    path = transit_matrix_path(REAL_DIR, region)
    if path.is_file():
        return path.read_bytes()
    blob = git_plaintext(region)
    if blob is None:
        pytest.skip(f"нет открытого {path.name} ни на диске, ни в истории git: сравнивать не с чем")
    return blob


def test_every_bundle_region_has_an_encrypted_matrix():
    assert REGIONS and encrypted_regions(REAL_DIR) == REGIONS
    assert sorted(PLAINTEXT_SHA256) == REGIONS


@needs_key
@pytest.mark.parametrize("region", REGIONS)
def test_the_committed_matrix_decrypts_with_the_real_key(region):
    matrix = load_region_matrix(REAL_DIR, region, REAL_KEY)
    assert matrix is not None and matrix.region == region
    assert matrix.points and len(matrix.minutes) == len(matrix.points)


@needs_key
@pytest.mark.parametrize("region", REGIONS)
def test_the_decrypted_matrix_has_the_pinned_plaintext_hash(region):
    # Идёт и в CI, где открытых файлов нет: хэш закреплён выше.
    token = encrypted_matrix_path(REAL_DIR, region).read_bytes()
    assert sha256(decrypt_matrix_bytes(token, REAL_KEY)) == PLAINTEXT_SHA256[region], (
        f"{region}{MATRIX_SUFFIX}.enc расшифровывается не в ту матрицу, чей хэш закреплён. Если матрицы пересчитаны "
        "нарочно, обновите PLAINTEXT_SHA256"
    )


@needs_key
@pytest.mark.parametrize("region", REGIONS)
def test_the_decrypted_matrix_is_byte_identical_to_the_plaintext(region):
    data = plaintext_or_skip(region)
    token = encrypted_matrix_path(REAL_DIR, region).read_bytes()
    assert decrypt_matrix_bytes(token, REAL_KEY) == data, (
        f"открытый {region}{MATRIX_SUFFIX} не совпадает с зашифрованным: пересчитан и не зашифрован? "
        "make transit-encrypt"
    )


@needs_key
@pytest.mark.parametrize("region", REGIONS)
def test_the_problem_fingerprint_is_the_same_with_decrypted_matrices(region):
    """Отпечаток задачи дня на расшифрованных матрицах тот же, что на открытых: ночные планы остаются в силе.

    Открытые матрицы разбираются так, как их читал сервис до шифрования: все <регион>.json по именам файлов.
    OSRM не нужен: матрицы 2ГИС входят в отпечаток и на расстояниях по прямой.
    """
    names = sorted(f"{name}{MATRIX_SUFFIX}" for name in REGIONS)
    plain = [
        parse_transit_matrix(plaintext_or_skip(name.removesuffix(MATRIX_SUFFIX)).decode()) for name in names
    ]
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
