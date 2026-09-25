"""scripts/osrm_prepare.sh с заглушками curl, osmium и osrm-*: без сети и без настоящего OSRM."""

import hashlib
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "osrm_prepare.sh"

WRITE_OUTPUT = """#!/bin/sh
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  shift
done
head -c 2048 /dev/zero > "$out"
"""
STUBS = {
    "curl": WRITE_OUTPUT,
    "osmium": WRITE_OUTPUT,
    "osrm-extract": '#!/bin/sh\nfor last; do :; done\nhead -c 4096 /dev/zero > "${last%.osm.pbf}.osrm.ebg"\n',
    "osrm-partition": '#!/bin/sh\ntouch "$1.partition"\n',
    "osrm-customize": '#!/bin/sh\ntouch "$1.cells"\n',
}


# curl, который не находит датированную выгрузку: любой URL с «260922» отвечает ошибкой, как 404 у curl -f.
MISSING_DATED = '#!/bin/sh\ncase "$*" in *260922*) exit 22;; esac\n' + WRITE_OUTPUT.split("\n", 1)[1]
needs_bash = pytest.mark.skipif(
    not SCRIPT.exists() or shutil.which("bash") is None, reason="нужны bash и scripts/"
)


def _run(
    tmp_path: Path, stubs: dict[str, str], **env_extra: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    data = tmp_path / "data"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DATA_DIR": str(data),
        "OSRM_BBOX": "36.6,54.6,38.9,56.3",
        **env_extra,
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60), data


@needs_bash
def test_prepare_removes_pbf_files_after_graph_is_built(tmp_path):
    result, data = _run(tmp_path, STUBS, GRAPH_URL="", PBF_URL="http://pbf.invalid/region.osm.pbf")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (data / "central-fed-district.osm.pbf").exists()
    assert not (data / "moscow.osm.pbf").exists()
    assert (data / "moscow.osrm.ebg").exists() and (data / "moscow.osrm.cells").exists()
    assert (data / ".prepared-36.6_54.6_38.9_56.3").exists()
    assert "Граф занимает на диске" in result.stdout


@needs_bash
def test_prepare_downloads_pinned_extract_and_falls_back_to_latest(tmp_path):
    """По умолчанию качается выгрузка 22.09.2026, на которой посчитаны ночные планы; если её нет — свежая."""
    result, data = _run(tmp_path, {**STUBS, "curl": MISSING_DATED}, GRAPH_URL="")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "central-fed-district-260922.osm.pbf" in result.stdout
    assert (
        "беру свежую https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf"
        in result.stdout
    )
    assert (data / "moscow.osrm.cells").exists()


def _graph_archive(tmp_path: Path) -> tuple[Path, str]:
    """Архив «графа стенда» из двух файлов moscow.osrm.*, как его собирает infra/yc/publish_graph.sh."""
    files = tmp_path / "graph"
    files.mkdir()
    for name in ("moscow.osrm.cells", "moscow.osrm.mldgr"):
        (files / name).write_bytes(b"graph " + name.encode())
    archive = tmp_path / "graph.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(files.iterdir()):
            tar.add(path, arcname=path.name)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def _copy_curl(archive: Path) -> str:
    """curl, который вместо скачивания кладёт в -o готовый архив графа, а PBF — нулями."""
    return (
        '#!/bin/sh\nout=""; url=""\n'
        'while [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then out="$2"; shift; fi; url="$1"; shift; done\n'
        f'case "$url" in *tar.gz) cp "{archive}" "$out";; *) head -c 2048 /dev/zero > "$out";; esac\n'
    )


@needs_bash
def test_prepare_downloads_stand_graph_when_checksum_matches(tmp_path):
    """Готовый граф стенда распаковывается как есть: свой граф не собирается, osrm-* не вызываются."""
    archive, digest = _graph_archive(tmp_path)
    stubs = {**STUBS, "curl": _copy_curl(archive), "osrm-extract": "#!/bin/sh\nexit 1\n"}

    result, data = _run(tmp_path, stubs, GRAPH_URL="http://graph.invalid/moscow.tar.gz", GRAPH_SHA256=digest)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (data / "moscow.osrm.cells").read_bytes() == b"graph moscow.osrm.cells"
    assert (data / ".prepared-36.6_54.6_38.9_56.3").exists()
    assert not (data / "graph.tar.gz").exists()
    assert "граф стенда" in result.stdout


@needs_bash
def test_prepare_builds_own_graph_when_checksum_differs(tmp_path):
    """Архив с чужой контрольной суммой не распаковывается: граф собирается из выгрузки OSM."""
    archive, _ = _graph_archive(tmp_path)

    result, data = _run(
        tmp_path, {**STUBS, "curl": _copy_curl(archive)}, GRAPH_URL="http://graph.invalid/moscow.tar.gz"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "не совпала контрольная сумма" in result.stdout
    assert (data / "moscow.osrm.ebg").exists()
    assert (data / "moscow.osrm.cells").read_bytes() == b""
