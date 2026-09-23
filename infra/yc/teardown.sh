#!/usr/bin/env bash
# Сносит всё, что создал create.sh: машину с дисками, статический IP, группу безопасности, сеть и подсеть (если
# их создавали мы, а не взяли готовую сеть default), образы и сам реестр, федерацию GitHub и оба сервисных
# аккаунта. Спрашивает подтверждение. Запуск: infra/yc/teardown.sh
# После него не остаётся ничего, за что Yandex Cloud берёт деньги, включая неактивный статический IP.
set -euo pipefail

# shellcheck source=infra/yc/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

check_yc
CREATED_NETWORK=0
CREATED_SUBNET=0
load_state

cat <<EOF
Будет удалено в папке $FOLDER_ID:
  машина $VM_NAME и её диски (вместе с базой дня, графом OSRM и сертификатами)
  статический IP $ADDRESS_NAME, группа безопасности $SG_NAME
  реестр $REGISTRY_NAME со всеми образами
  федерация $FEDERATION_NAME, сервисные аккаунты $CI_SA_NAME и $VM_SA_NAME
EOF
[[ $CREATED_SUBNET == 1 ]] && echo "  подсеть $SUBNET_NAME"
[[ $CREATED_NETWORK == 1 ]] && echo "  сеть $NETWORK_NAME"
read -r -p 'Напишите «удалить», чтобы продолжить: ' answer
if [[ $answer != удалить ]]; then
  echo "Ничего не тронуто"
  exit 1
fi

# Удалить ресурс по имени, если он есть: remove_named "vpc address" routing-ip
remove_named() {
  local kind=$1 name=$2 id
  # shellcheck disable=SC2086  # kind — несколько слов команды yc
  id=$(id_of $kind get --name "$name")
  if [[ -z $id ]]; then
    echo "Нет $kind $name, пропускаю"
    return 0
  fi
  echo "Удаляю $kind $name ($id)"
  # shellcheck disable=SC2086
  yc_ $kind delete --id "$id" >/dev/null
}

step "Машина"
delete_vm_with_disks "$VM_NAME"

step "Сеть"
remove_named "vpc address" "$ADDRESS_NAME"
remove_named "vpc security-group" "$SG_NAME"
if [[ $CREATED_SUBNET == 1 ]]; then
  remove_named "vpc subnet" "$SUBNET_NAME"
fi
if [[ $CREATED_NETWORK == 1 ]]; then
  remove_named "vpc network" "$NETWORK_NAME"
fi

step "Реестр"
REGISTRY_ID=$(id_of container registry get --name "$REGISTRY_NAME")
if [[ -n $REGISTRY_ID ]]; then
  # Непустой реестр не удаляется: сначала все образы всех репозиториев. Список идёт страницами — по кругу, пока
  # не опустеет.
  for _ in $(seq 1 20); do
    images=$(yc_ container image list --registry-id "$REGISTRY_ID" --format json | jq -r '.[]?.id')
    [[ -z $images ]] && break
    echo "Удаляю образов: $(wc -l <<<"$images" | tr -d ' ')"
    # shellcheck disable=SC2086  # список id через пробел — отдельные аргументы
    yc_ container image delete $images >/dev/null
  done
  echo "Удаляю реестр $REGISTRY_NAME ($REGISTRY_ID)"
  yc_ container registry delete --id "$REGISTRY_ID" >/dev/null
else
  echo "Нет реестра $REGISTRY_NAME, пропускаю"
fi

step "Доступ GitHub и сервисные аккаунты"
CI_SA_ID=$(id_of iam service-account get --name "$CI_SA_NAME")
if [[ -n $CI_SA_ID ]]; then
  for credential in $(yc_ iam workload-identity federated-credential list --service-account-id "$CI_SA_ID" --format json | jq -r '.[]?.id'); do
    echo "Удаляю федеративный доступ $credential"
    yc_ iam workload-identity federated-credential delete --id "$credential" >/dev/null
  done
fi
remove_named "iam workload-identity oidc federation" "$FEDERATION_NAME"
remove_named "iam service-account" "$CI_SA_NAME"
remove_named "iam service-account" "$VM_SA_NAME"

rm -f "$STATE_FILE" "$KNOWN_HOSTS_FILE"
step "Готово: в папке $FOLDER_ID не осталось ничего из create.sh"
echo "Секреты и переменные в GitHub остались: они ничего не стоят, но VM_HOST и ключи уже никуда не ведут."
