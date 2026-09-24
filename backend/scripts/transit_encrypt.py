"""Шифрует открытые матрицы 2ГИС data/transit/<регион>.json в <регион>.json.enc ключом TRANSIT_KEY.

Запуск из каталога backend (make transit-encrypt берёт ключ из .env сам, make transit запускает шифрование после
пересчёта):
  TRANSIT_KEY=... python -m scripts.transit_encrypt
  TRANSIT_KEY=... python -m scripts.transit_encrypt --rekey

В репозитории и в образе backend лежат только зашифрованные файлы; открытые остаются на машине, где матрицы считали
(они в .gitignore и .dockerignore). Шифруются точные байты файла, поэтому сервис после расшифровки видит ровно ту же
матрицу: отпечатки задач и ночные планы не меняются. Токен Fernet каждый раз другой (случайный вектор и время
в нём), поэтому файл, который и так расшифровывается в те же байты, не перезаписывается: иначе каждый запуск давал
бы лишнюю правку в git. Зашифрованный файл, который этим ключом не расшифровывается, скрипт не трогает: скорее всего,
ключ в .env не тот, что в секретах выката, и перешифровка сломала бы матрицы на сервере. Сменить ключ нарочно —
флаг --rekey. Ни ключ, ни содержимое матриц не печатаются. Каталог переопределяется TRANSIT_MATRIX_DIR.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from app.geo.transit import (
    MATRIX_SUFFIX,
    TRANSIT_KEY_ENV,
    TransitKeyError,
    decrypt_matrix_bytes,
    encrypt_matrix_bytes,
    encrypted_matrix_path,
    parse_transit_matrix,
)
from app.settings import Settings

NO_KEY = (
    f"Нужен ключ шифрования матриц в переменной окружения {TRANSIT_KEY_ENV} (make transit-encrypt берёт его из .env).\n"
    "Новый ключ: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' — "
    f"его же надо положить в секрет {TRANSIT_KEY_ENV} репозитория на GitHub (infra/github/set_secrets.py)."
)


def _same_bytes(sealed: Path, data: bytes, key: str) -> bool:
    """Зашифрованный файл уже хранит ровно эти байты. Не расшифровывается этим ключом — TransitKeyError."""
    return decrypt_matrix_bytes(sealed.read_bytes(), key) == data


def encrypt_region(plain: Path, key: str, rekey: bool) -> tuple[bool, str]:
    """Шифрует один открытый файл рядом с ним. Ответ: удалось ли и строка для вывода (без содержимого и ключа)."""
    region = plain.name.removesuffix(MATRIX_SUFFIX)
    sealed = encrypted_matrix_path(plain.parent, region)
    data = plain.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    if parse_transit_matrix(text) is None:
        return False, f"регион {region}: {plain.name} — не матрица, не зашифрован"
    if sealed.is_file():
        try:
            if _same_bytes(sealed, data, key):
                return True, f"регион {region}: без изменений — {sealed}"
        except TransitKeyError:
            if not rekey:
                return False, (
                    f"регион {region}: {sealed.name} не расшифровывается этим ключом — не тронут. Ключ в .env "
                    "не тот? Перешифровать этим ключом нарочно: --rekey"
                )
    # Сначала во временный файл рядом, потом переименование: оборванный запуск не оставит битый .enc.
    partial = sealed.with_name(f"{sealed.name}.tmp")
    partial.write_bytes(encrypt_matrix_bytes(data, key))
    partial.replace(sealed)
    if not _same_bytes(sealed, data, key):
        return False, f"регион {region}: {sealed.name} после записи расшифровывается не в те же байты"
    return True, f"регион {region}: зашифрован — {sealed}"


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Шифрует открытые матрицы 2ГИС data/transit/<регион>.json ключом TRANSIT_KEY"
    )
    parser.add_argument(
        "--rekey",
        action="store_true",
        help="перешифровать и те файлы, что зашифрованы другим ключом (смена ключа)",
    )
    args = parser.parse_args(argv)
    settings = Settings.from_env(os.environ if env is None else env)
    key = settings.transit_key
    if not key:
        print(NO_KEY, file=sys.stderr)
        return 2
    try:
        encrypt_matrix_bytes(b"", key)
    except TransitKeyError as error:
        print(f"{error}. {NO_KEY}", file=sys.stderr)
        return 2
    plains = sorted(settings.transit_dir.glob(f"*{MATRIX_SUFFIX}"))
    if not plains:
        print(f"В {settings.transit_dir} нет открытых матриц <регион>.json: шифровать нечего")
        return 0
    failed = 0
    for plain in plains:
        ok, line = encrypt_region(plain, key, args.rekey)
        failed += not ok
        print(line, file=sys.stdout if ok else sys.stderr)
    print(f"регионов: {len(plains)}, с ошибкой: {failed}")
    if failed < len(plains):
        print(
            "В git идут только <регион>.json.enc; чтобы сервис считал по ним, пересоберите образ: "
            "docker compose up -d --build backend"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
