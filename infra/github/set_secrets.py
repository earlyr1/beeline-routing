#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pynacl>=1.5", "bcrypt>=4.0"]
# ///
"""Секреты и переменные GitHub Actions для выката на сервер жюри — одной командой.

Запуск из корня репозитория: uv run infra/github/set_secrets.py
Пароль жюри спрашивается без эха (или BASIC_AUTH_PASSWORD в окружении, или одной строкой через --password-stdin);
в аргументах командной строки его нет нигде. Значения не печатаются никогда — только имена.

Откуда что берётся:
  .env в корне         — YANDEX_MAPS_API_KEY, YANDEX_GEOCODER_API_KEY, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL;
  infra/yc/.state.env  — YC_SA_ID, YC_REGISTRY_ID, VM_HOST (пишет infra/yc/create.sh), SITE_HOST (<IP>.nip.io);
  infra/yc/known_hosts — VM_KNOWN_HOSTS (тоже create.sh, ключи хоста из вывода последовательного порта);
  ~/.ssh/beeline_routing_deploy — VM_SSH_KEY, закрытый ключ пользователя deploy;
  генерируется        — POSTGRES_PASSWORD (только если в GitHub его ещё нет: база на машине уже создана
                         со старым паролем, новый её сломал бы), BASIC_AUTH_HASH (bcrypt от пароля жюри).
Сам пароль жюри тоже уходит в секрет BASIC_AUTH_PASSWORD: им деплой проверяет сервис через Caddy.
Переменная TLS_MODE меняется только с --tls-mode; если её в GitHub ещё нет, ставится acme.

Токен GitHub (нужны права Secrets и Variables на запись для репозитория) ищется по порядку:
$GITHUB_TOKEN, ~/.config/github-mcp/token, файл GITHUB_MCP_TOKEN в корне репозитория.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

import bcrypt
from nacl import encoding, public

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = "earlyr1/beeline-routing"
APP_KEYS = ("YANDEX_MAPS_API_KEY", "YANDEX_GEOCODER_API_KEY", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
# Каждый запрос с неверным паролем Caddy проверяет bcrypt заново (кэш помнит только уже виденные пары), и при
# цене 12 это четверть-полсекунды ядра машины: перебор паролей съел бы процессор. Цена 10 вчетверо дешевле, а хэш
# лежит только в секретах GitHub и в .env машины с правами 600 — стойкость к офлайн-перебору тут вторична.
BCRYPT_COST = 10
# GITHUB_API_URL — как в самих Actions; другой адрес нужен только для проверки скрипта на заглушке API.
API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE по строкам: комментарии, export и кавычки вокруг значения — как в .env для compose."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:  # комментарий после значения без кавычек, как понимает его compose
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def read_token() -> tuple[str, str]:
    """Токен GitHub и откуда он взят (печатается только источник). Следующий источник читается, только если
    в предыдущем токена нет. $GITHUB_TOKEN первым: так его можно подставить на один запуск, не трогая файлы,
    например если у токена из файла нет прав на секреты."""
    sources = (
        ("$GITHUB_TOKEN", lambda: os.environ.get("GITHUB_TOKEN", "")),
        (
            "~/.config/github-mcp/token",
            lambda: read_if_exists(Path.home() / ".config" / "github-mcp" / "token"),
        ),
        ("GITHUB_MCP_TOKEN", lambda: read_if_exists(REPO_ROOT / "GITHUB_MCP_TOKEN")),
    )
    for source, read in sources:
        token = token_from_text(read())
        if token:
            return token, source
    sys.exit("Нет токена GitHub: ни $GITHUB_TOKEN, ни ~/.config/github-mcp/token, ни файла GITHUB_MCP_TOKEN")


def read_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def token_from_text(text: str) -> str:
    """Файл с токеном бывает голым токеном или строкой вида NAME=token: берётся первое непустое значение."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            line = line.split("=", 1)[1].strip().strip("'\"")
        if line:
            return line
    return ""


class GitHub:
    def __init__(self, repo: str, token: str, source: str) -> None:
        self.repo = repo
        self.token = token
        self.source = source
        self._box: public.SealedBox | None = None
        self._key_id = ""

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{API}/repos/{self.repo}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            return error.code, payload

    def check(self, status: int, payload: dict, what: str) -> None:
        if status >= 300:
            # В ответе GitHub только его сообщение об ошибке: значений секретов там нет.
            message = f"GitHub ответил {status} на «{what}»: {payload.get('message', '')}"
            if status in (401, 403, 404):
                message += (
                    f"\nТокен взят из {self.source}. Нужен доступ к {self.repo} с правами Secrets и Variables"
                    " на чтение и запись (fine-grained) или repo (classic); другой токен на один запуск —"
                    " через $GITHUB_TOKEN. 404 — ещё и если репозитория нет."
                )
            sys.exit(message)

    def secret_names(self) -> set[str]:
        names: set[str] = set()
        page = 1
        while True:
            status, payload = self.request("GET", f"/actions/secrets?per_page=100&page={page}")
            self.check(status, payload, "список секретов")
            batch = [item["name"] for item in payload.get("secrets", [])]
            names.update(batch)
            if len(batch) < 100:
                return names
            page += 1

    def put_secret(self, name: str, value: str) -> None:
        # Секрет шифруется открытым ключом репозитория (sealed box libsodium): расшифровать его может только GitHub.
        if self._box is None:
            status, key = self.request("GET", "/actions/secrets/public-key")
            self.check(status, key, "публичный ключ репозитория")
            self._box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
            self._key_id = key["key_id"]
        encrypted = base64.b64encode(self._box.encrypt(value.encode())).decode()
        status, payload = self.request(
            "PUT", f"/actions/secrets/{name}", {"encrypted_value": encrypted, "key_id": self._key_id}
        )
        self.check(status, payload, f"секрет {name}")

    def has_variable(self, name: str) -> bool:
        status, payload = self.request("GET", f"/actions/variables/{name}")
        if status == 404:
            return False
        self.check(status, payload, f"переменная {name}")
        return True

    def put_variable(self, name: str, value: str) -> None:
        status, payload = self.request("POST", "/actions/variables", {"name": name, "value": value})
        if status == 409:  # уже есть — обновить
            status, payload = self.request(
                "PATCH", f"/actions/variables/{name}", {"name": name, "value": value}
            )
        self.check(status, payload, f"переменная {name}")


def read_password(from_stdin: bool) -> str | None:
    """Пароль жюри: из окружения, одной строкой из stdin или с клавиатуры без эха. None — не менять."""
    if os.environ.get("BASIC_AUTH_PASSWORD"):
        return os.environ["BASIC_AUTH_PASSWORD"]
    if from_stdin:
        line = sys.stdin.readline().rstrip("\r\n")
        return line or None
    if not sys.stdin.isatty():
        return None
    first = getpass.getpass("Пароль жюри (Enter — оставить прежний): ")
    if not first:
        return None
    if getpass.getpass("Ещё раз: ") != first:
        sys.exit("Пароли не совпали")
    return first


def env_safe(name: str, value: str) -> None:
    """Деплой пишет значения в .env в одинарных кавычках: одинарную кавычку и перевод строки туда не записать."""
    if "'" in value or "\n" in value or "\r" in value:
        sys.exit(f"В значении {name} одинарная кавычка или перевод строки: в .env на машине так не записать")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--repo", default=DEFAULT_REPO, help=f"владелец/репозиторий, по умолчанию {DEFAULT_REPO}"
    )
    parser.add_argument(
        "--basic-auth-user", default=os.environ.get("BASIC_AUTH_USER", "jury"), help="логин жюри"
    )
    parser.add_argument("--password-stdin", action="store_true", help="пароль жюри одной строкой из stdin")
    # Без флага TLS_MODE не трогается: повторный запуск ради пароля или ключей не должен молча вернуть acme
    # после перехода на internal (например, когда упёрлись в лимит Let's Encrypt).
    parser.add_argument(
        "--tls-mode",
        choices=("acme", "internal"),
        default=None,
        help="переменная TLS_MODE; без флага остаётся прежней, а если её ещё нет — acme",
    )
    # Кабинет ключа Яндекс Карт не принимает голый IP, поэтому сайт открывается ещё и по имени в nip.io:
    # 81.26.188.190.nip.io указывает на тот же адрес. Пустое значение — только IP.
    parser.add_argument(
        "--site-host",
        default=None,
        help="имя сайта для ключа Яндекс Карт, по умолчанию <IP>.nip.io; пустая строка — SITE_HOST не трогать",
    )
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument("--state-file", type=Path, default=REPO_ROOT / "infra" / "yc" / ".state.env")
    parser.add_argument("--known-hosts", type=Path, default=REPO_ROOT / "infra" / "yc" / "known_hosts")
    parser.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh" / "beeline_routing_deploy")
    parser.add_argument("--dry-run", action="store_true", help="ничего не отправлять, только показать имена")
    args = parser.parse_args()

    if not args.state_file.is_file():
        sys.exit(f"Нет {args.state_file}: сначала infra/yc/create.sh")
    state = parse_env_file(args.state_file)
    for key in ("CI_SA_ID", "REGISTRY_ID", "VM_IP"):
        if not state.get(key):
            sys.exit(f"В {args.state_file} нет {key}: перезапустите infra/yc/create.sh")
    if not args.known_hosts.is_file() or not args.known_hosts.read_text(encoding="utf-8").strip():
        sys.exit(f"Нет {args.known_hosts}: create.sh снимает его с вывода последовательного порта машины")
    if not args.ssh_key.is_file():
        sys.exit(f"Нет закрытого ключа {args.ssh_key}")
    app_env = parse_env_file(args.env_file) if args.env_file.is_file() else {}

    variables = {
        "YC_SA_ID": state["CI_SA_ID"],
        "YC_REGISTRY_ID": state["REGISTRY_ID"],
        "VM_HOST": state["VM_IP"],
        "SITE_HOST": f"{state['VM_IP']}.nip.io" if args.site_host is None else args.site_host.strip(),
    }
    # Пустую переменную GitHub не принимает: пустой --site-host просто не трогает SITE_HOST.
    if not variables["SITE_HOST"]:
        del variables["SITE_HOST"]
    values: dict[str, str] = {
        "VM_SSH_KEY": args.ssh_key.read_text(encoding="utf-8"),
        "VM_KNOWN_HOSTS": args.known_hosts.read_text(encoding="utf-8").strip() + "\n",
    }
    skipped: list[str] = []
    for name in APP_KEYS:
        value = app_env.get(name, "")
        if value:
            env_safe(name, value)
            values[name] = value
        else:
            skipped.append(name)

    password = read_password(args.password_stdin)
    if password is not None:
        user = args.basic_auth_user
        if not user or ":" in user:
            sys.exit("Логин жюри не пустой и без двоеточия")
        env_safe("BASIC_AUTH_USER", user)
        values["BASIC_AUTH_USER"] = user
        values["BASIC_AUTH_PASSWORD"] = password
        values["BASIC_AUTH_HASH"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_COST)).decode()

    if args.dry_run:
        existing: set[str] = set()
        github = None
        print(f"Проба: в {args.repo} ничего не отправляется")
    else:
        token, source = read_token()
        print(f"Токен GitHub: {source}")
        github = GitHub(args.repo, token, source)
        existing = github.secret_names()

    if args.tls_mode:
        variables["TLS_MODE"] = args.tls_mode
    elif github and not github.has_variable("TLS_MODE"):
        variables["TLS_MODE"] = "acme"

    # Пароль базы только первый раз: том pg-data на машине уже инициализирован с ним.
    if "POSTGRES_PASSWORD" not in existing:
        # token_urlsafe — только буквы, цифры, - и _: пароль подставляется в DATABASE_URL без экранирования.
        values["POSTGRES_PASSWORD"] = secrets.token_urlsafe(24)
    if password is None:
        missing = [
            name
            for name in ("BASIC_AUTH_USER", "BASIC_AUTH_HASH", "BASIC_AUTH_PASSWORD")
            if name not in existing
        ]
        if missing and not args.dry_run:
            sys.exit(
                f"В GitHub ещё нет {', '.join(missing)}: нужен пароль жюри (с клавиатуры, --password-stdin или BASIC_AUTH_PASSWORD)"
            )

    for name, value in variables.items():
        if github:
            github.put_variable(name, value)
        print(f"переменная {name}")
    if "TLS_MODE" not in variables:
        print(
            "переменная TLS_MODE не менялась"
            if github
            else "переменная TLS_MODE прежняя, а если её нет — acme"
        )
    for name, value in values.items():
        if github:
            github.put_secret(name, value)
        print(f"секрет     {name}")
    if "POSTGRES_PASSWORD" not in values:
        print("секрет     POSTGRES_PASSWORD уже есть, не трогаю")
    if password is None:
        print("секреты    BASIC_AUTH_* не менялись: пароль жюри не введён")
    for name in skipped:
        print(f"пропущен   {name}: в .env пусто, секрет в GitHub (если был) не тронут")


if __name__ == "__main__":
    main()
