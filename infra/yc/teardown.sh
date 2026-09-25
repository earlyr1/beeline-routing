#!/usr/bin/env bash
# Сносит всё, что создал create.sh: машину с дисками, статический IP, группу безопасности, сеть и подсеть (если
# их создавали мы, а не взяли готовые: они узнаются по именам routing-*), образы и сам реестр, федерацию GitHub
# и оба сервисных аккаунта, а также бакет с готовым графом OSRM из publish_graph.sh. Спрашивает подтверждение. Запуск: infra/yc/teardown.sh
# После него не остаётся ничего, за что Yandex Cloud берёт деньги, включая неактивный статический IP.
set -euo pipefail

# shellcheck source=infra/yc/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

check_yc
# Сеть и подсеть — по имени, а не по файлу состояния: если create.sh упал на середине, файла нет, а сеть уже есть.
SUBNET_ID=$(id_of vpc subnet get --name "$SUBNET_NAME")
NETWORK_ID=$(id_of vpc network get --name "$NETWORK_NAME")

cat <<EOF
Будет удалено в папке $FOLDER_ID:
  машина $VM_NAME и её диски (вместе с базой дня, графом OSRM и сертификатами)
  статический IP $ADDRESS_NAME, группа безопасности $SG_NAME
  реестр $REGISTRY_NAME со всеми образами
  федерация $FEDERATION_NAME, сервисные аккаунты $CI_SA_NAME и $VM_SA_NAME
  бакет $GRAPH_BUCKET с графом OSRM (после него osrm-prepare на чистом клоне соберёт граф сам)
EOF
[[ -n $SUBNET_ID ]] && echo "  подсеть $SUBNET_NAME"
[[ -n $NETWORK_ID ]] && echo "  сеть $NETWORK_NAME"
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
  # Не у всех ресурсов есть get --name (федерации нужен federation_id): тогда ищем по имени в списке каталога.
  if [[ -z $id ]]; then
    # shellcheck disable=SC2086
    id=$(yc_ $kind list --format json 2>/dev/null |
      jq -r --arg name "$name" '[.[]? | select(.name == $name)][0].id // empty' || true)
  fi
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
remove_named "vpc subnet" "$SUBNET_NAME"
remove_named "vpc network" "$NETWORK_NAME"

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

step "Бакет с графом OSRM"
if [[ -n $(id_of storage bucket get --name "$GRAPH_BUCKET") ]]; then
  # Непустой бакет не удаляется: сначала все объекты.
  "$YC" storage s3 rm --recursive "s3://$GRAPH_BUCKET/" >/dev/null
  echo "Удаляю бакет $GRAPH_BUCKET"
  yc_ storage bucket delete --name "$GRAPH_BUCKET" >/dev/null
else
  echo "Нет бакета $GRAPH_BUCKET, пропускаю"
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
