# Общее для create.sh, resize.sh и teardown.sh: имена ресурсов, вызов yc в нужной папке, файл состояния.
# Подключается через source, сам не запускается. yc должен быть уже авторизован: `yc init`.
# shellcheck shell=bash
# Имена ниже читают подключающие скрипты, сам lib.sh их не использует.
# shellcheck disable=SC2034

YC=${YC:-yc}
FOLDER_ID=${FOLDER_ID:-b1g640095ie8rkbcvrde}
ZONE=${ZONE:-ru-central1-d}

REGISTRY_NAME=routing
CI_SA_NAME=routing-ci
VM_SA_NAME=routing-vm
FEDERATION_NAME=routing-github
ADDRESS_NAME=routing-ip
SG_NAME=routing-sg
VM_NAME=routing
# Своя сеть создаётся, только если в папке нет сети default: облачных сетей по умолчанию разрешено всего две.
# Сеть и подсеть с этими именами бывают только от create.sh, teardown.sh удаляет их по имени.
DEFAULT_NETWORK_NAME=default
NETWORK_NAME=routing-net
SUBNET_NAME=routing-$ZONE
SUBNET_CIDR=${SUBNET_CIDR:-10.140.0.0/24}

# GitHub Actions получает IAM-токен сервисного аккаунта routing-ci без ключей: по OIDC-токену прогона.
GITHUB_REPO=earlyr1/beeline-routing
OIDC_ISSUER=https://token.actions.githubusercontent.com
OIDC_AUDIENCE=https://github.com/earlyr1
OIDC_JWKS_URL=https://token.actions.githubusercontent.com/.well-known/jwks
# Токен с таким sub выдаётся только прогонам из ветки main, и только без environment: в job — иначе sub другой.
OIDC_SUBJECT="repo:$GITHUB_REPO:ref:refs/heads/main"

YC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STATE_FILE="$YC_DIR/.state.env"
KNOWN_HOSTS_FILE="$YC_DIR/known_hosts"

# yc всегда в нашей папке. Глобальный флаг ставится в конец, после флагов команды.
yc_() {
  "$YC" "$@" --folder-id "$FOLDER_ID"
}

# id ресурса или пусто, если его нет: `id_of container registry get --name routing`.
# Ошибку доступа не отличить от «нет такого» — тогда упадёт следующий за ней create, и её будет видно.
id_of() {
  yc_ "$@" --format json 2>/dev/null | jq -r '.id // empty' || true
}

step() {
  printf '\n==> %s\n' "$*"
}

need() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || {
      echo "Нужен $tool" >&2
      exit 1
    }
  done
}

# Проверка, что yc авторизован и видит папку: до первой правки, а не на середине.
check_yc() {
  need "$YC" jq
  if ! yc_ resource-manager folder get --id "$FOLDER_ID" --format json >/dev/null; then
    echo "yc не видит папку $FOLDER_ID. Сначала: yc init" >&2
    exit 1
  fi
}

# Удалить машину и все её диски: загрузочный с auto-delete уходит вместе с ней, остальные — следом.
delete_vm_with_disks() {
  local name=$1 json vm_id disks disk
  json=$(yc_ compute instance get --name "$name" --format json 2>/dev/null) || {
    echo "Машины $name нет, пропускаю"
    return 0
  }
  vm_id=$(jq -r .id <<<"$json")
  disks=$(jq -r '[.boot_disk.disk_id, (.secondary_disks // [])[].disk_id] | map(select(. != null)) | .[]' <<<"$json")
  echo "Удаляю машину $name ($vm_id)"
  yc_ compute instance delete --id "$vm_id" >/dev/null
  for disk in $disks; do
    if yc_ compute disk get --id "$disk" >/dev/null 2>&1; then
      echo "Удаляю диск $disk"
      yc_ compute disk delete --id "$disk" >/dev/null
    fi
  done
}
