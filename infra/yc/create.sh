#!/usr/bin/env bash
# Поднимает в Yandex Cloud всё для сервера жюри: реестр образов, сервисные аккаунты для CI и для машины,
# федерацию GitHub OIDC, статический IP, группу безопасности и саму машину с cloud-init.
# Запуск: infra/yc/create.sh [--delete-old]
#   --delete-old  заодно удалить старую машину transit-2gis вместе с её дисками.
#
# Повторный запуск ничего не пересоздаёт: что уже есть, берётся как есть. Поэтому правка cloud-init.yaml или
# размеров машины на существующую машину не действует — размер меняет resize.sh.
# В конце печатает идентификаторы и пишет их в infra/yc/.state.env, ключи хоста — в infra/yc/known_hosts.
set -euo pipefail

# shellcheck source=infra/yc/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

OLD_VM_NAME=transit-2gis
SSH_PUBLIC_KEY=${SSH_PUBLIC_KEY:-$HOME/.ssh/beeline_routing_deploy.pub}
SSH_PRIVATE_KEY=${SSH_PUBLIC_KEY%.pub}

DELETE_OLD=0
for arg in "$@"; do
  case $arg in
    --delete-old) DELETE_OLD=1 ;;
    -h | --help)
      sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Неизвестный аргумент: $arg" >&2
      exit 2
      ;;
  esac
done

check_yc
need ssh
if [[ ! -f $SSH_PUBLIC_KEY ]]; then
  echo "Нет ключа $SSH_PUBLIC_KEY. Создать: ssh-keygen -t ed25519 -N '' -C deploy@beeline-routing -f ${SSH_PRIVATE_KEY}" >&2
  exit 1
fi
PUBLIC_KEY=$(<"$SSH_PUBLIC_KEY")
if [[ $PUBLIC_KEY != ssh-* || $PUBLIC_KEY == *$'\n'* ]]; then
  echo "$SSH_PUBLIC_KEY не похож на публичный ключ OpenSSH в одну строку" >&2
  exit 1
fi

# Зарезервированный IP старой машины переживает её удаление и без неё стоит дороже. Сам не удаляется:
# вдруг он ещё нужен. Подсказка, как удалить, повторяется в конце.
OLD_IP_NOTE=""
if [[ $DELETE_OLD == 1 ]]; then
  step "Старая машина $OLD_VM_NAME"
  old_ip=$(yc_ compute instance get --name "$OLD_VM_NAME" --format json 2>/dev/null |
    jq -r '[.network_interfaces[]?.primary_v4_address.one_to_one_nat.address // empty][0] // empty' || true)
  delete_vm_with_disks "$OLD_VM_NAME"
  if [[ -n $old_ip ]]; then
    old_address_id=$(yc_ vpc address list --format json |
      jq -r --arg ip "$old_ip" \
        '[.[]? | select(.reserved and (.used | not) and .external_ipv4_address.address == $ip)][0].id // empty')
    if [[ -n $old_address_id ]]; then
      OLD_IP_NOTE="Статический IP $old_ip старой машины остался ($old_address_id), без машины он стоит 0,6039 ₽/ч. Не нужен — удалить: yc vpc address delete --id $old_address_id --folder-id $FOLDER_ID"
      echo "$OLD_IP_NOTE"
    fi
  fi
fi

step "Реестр $REGISTRY_NAME"
REGISTRY_ID=$(id_of container registry get --name "$REGISTRY_NAME")
if [[ -z $REGISTRY_ID ]]; then
  REGISTRY_ID=$(yc_ container registry create --name "$REGISTRY_NAME" --format json | jq -r .id)
  echo "Создан"
fi

# Сервисный аккаунт по имени; создаёт, если его нет. Печатает id.
# Зовётся внутри $(...), а там set -e не действует: без явных проверок сбой create дал бы пустой id с кодом 0.
service_account() {
  local name=$1 description=$2 id
  id=$(id_of iam service-account get --name "$name")
  if [[ -z $id ]]; then
    id=$(yc_ iam service-account create --name "$name" --description "$description" --format json | jq -r .id) || return 1
  fi
  [[ -n $id && $id != null ]] || return 1
  echo "$id"
}

# Роль на реестр для сервисного аккаунта, если её ещё нет.
registry_role() {
  local role=$1 sa_id=$2
  if yc_ container registry list-access-bindings --id "$REGISTRY_ID" --format json |
    jq -e --arg role "$role" --arg sa "$sa_id" 'any(.[]?; .role_id == $role and .subject.id == $sa)' >/dev/null; then
    echo "Роль $role у $sa_id уже есть"
    return 0
  fi
  yc_ container registry add-access-binding --id "$REGISTRY_ID" --role "$role" --service-account-id "$sa_id" >/dev/null
  echo "Выдана роль $role для $sa_id"
}

step "Сервисные аккаунты"
CI_SA_ID=$(service_account "$CI_SA_NAME" "GitHub Actions: пушит образы в реестр $REGISTRY_NAME") || {
  echo "Не удалось найти или создать сервисный аккаунт $CI_SA_NAME" >&2
  exit 1
}
VM_SA_ID=$(service_account "$VM_SA_NAME" "Машина жюри: скачивает образы из реестра $REGISTRY_NAME") || {
  echo "Не удалось найти или создать сервисный аккаунт $VM_SA_NAME" >&2
  exit 1
}
echo "$CI_SA_NAME: $CI_SA_ID"
echo "$VM_SA_NAME: $VM_SA_ID"
registry_role container-registry.images.pusher "$CI_SA_ID"
registry_role container-registry.images.puller "$VM_SA_ID"

step "Федерация GitHub OIDC $FEDERATION_NAME"
FEDERATION_ID=$(id_of iam workload-identity oidc federation get --name "$FEDERATION_NAME")
if [[ -z $FEDERATION_ID ]]; then
  FEDERATION_ID=$(yc_ iam workload-identity oidc federation create --name "$FEDERATION_NAME" \
    --description "GitHub Actions репозитория $GITHUB_REPO" \
    --issuer "$OIDC_ISSUER" --audiences "$OIDC_AUDIENCE" --jwks-url "$OIDC_JWKS_URL" \
    --format json | jq -r .id)
  echo "Создана"
fi
CREDENTIAL_ID=$(yc_ iam workload-identity federated-credential list --service-account-id "$CI_SA_ID" --format json |
  jq -r --arg fed "$FEDERATION_ID" --arg sub "$OIDC_SUBJECT" \
    '[.[]? | select(.federation_id == $fed and .external_subject_id == $sub)][0].id // empty')
if [[ -z $CREDENTIAL_ID ]]; then
  CREDENTIAL_ID=$(yc_ iam workload-identity federated-credential create --service-account-id "$CI_SA_ID" \
    --federation-id "$FEDERATION_ID" --external-subject-id "$OIDC_SUBJECT" --format json | jq -r .id)
  echo "Создан федеративный доступ для $OIDC_SUBJECT"
fi

step "Статический IP $ADDRESS_NAME"
ADDRESS_ID=$(id_of vpc address get --name "$ADDRESS_NAME")
if [[ -z $ADDRESS_ID ]]; then
  ADDRESS_ID=$(yc_ vpc address create --name "$ADDRESS_NAME" --external-ipv4 "zone=$ZONE" --format json | jq -r .id)
  echo "Создан"
fi
VM_IP=$(yc_ vpc address get --id "$ADDRESS_ID" --format json | jq -r .external_ipv4_address.address)
echo "$VM_IP"

# Сеть $NETWORK_NAME и подсеть $SUBNET_NAME бывают только от этого скрипта, поэтому teardown.sh удаляет их по
# имени. Файл состояния тут ни при чём: он пишется в конце и после сбоя на середине его нет.
step "Сеть и подсеть в $ZONE"
NETWORK_ID=$(id_of vpc network get --name "$DEFAULT_NETWORK_NAME")
if [[ -z $NETWORK_ID ]]; then
  NETWORK_ID=$(id_of vpc network get --name "$NETWORK_NAME")
fi
if [[ -z $NETWORK_ID ]]; then
  NETWORK_ID=$(yc_ vpc network create --name "$NETWORK_NAME" --format json | jq -r .id)
  echo "Создана сеть $NETWORK_NAME"
fi
SUBNET_ID=$(yc_ vpc network list-subnets --id "$NETWORK_ID" --format json |
  jq -r --arg zone "$ZONE" '[.[]? | select(.zone_id == $zone)][0].id // empty')
if [[ -z $SUBNET_ID ]]; then
  SUBNET_ID=$(yc_ vpc subnet create --name "$SUBNET_NAME" --network-id "$NETWORK_ID" --zone "$ZONE" \
    --range "$SUBNET_CIDR" --format json | jq -r .id)
  echo "Создана подсеть $SUBNET_NAME $SUBNET_CIDR"
fi
echo "сеть $NETWORK_ID, подсеть $SUBNET_ID"

step "Группа безопасности $SG_NAME: снаружи только 22, 80 и 443"
SG_ID=$(id_of vpc security-group get --name "$SG_NAME")
if [[ -z $SG_ID ]]; then
  SG_ID=$(yc_ vpc security-group create --name "$SG_NAME" --network-id "$NETWORK_ID" \
    --description "Сервер жюри: SSH для деплоя, HTTP для ACME и редиректа, HTTPS" \
    --rule "direction=ingress,port=22,protocol=tcp,v4-cidrs=[0.0.0.0/0],description=ssh" \
    --rule "direction=ingress,port=80,protocol=tcp,v4-cidrs=[0.0.0.0/0],description=http" \
    --rule "direction=ingress,port=443,protocol=tcp,v4-cidrs=[0.0.0.0/0],description=https" \
    --rule "direction=egress,protocol=any,from-port=0,to-port=65535,v4-cidrs=[0.0.0.0/0],description=all-egress" \
    --format json | jq -r .id)
  echo "Создана"
fi

step "Машина $VM_NAME"
JUST_CREATED=0
INSTANCE_ID=$(id_of compute instance get --name "$VM_NAME")
if [[ -z $INSTANCE_ID ]]; then
  user_data=$(mktemp)
  trap 'rm -f "$user_data"' EXIT
  # Подстановка без ${var//…}: bash 3.2 из macOS и bash 5 по-разному обходятся с кавычками в замене.
  template=$(<"$YC_DIR/cloud-init.yaml")
  printf '%s%s%s\n' "${template%%__DEPLOY_SSH_PUBLIC_KEY__*}" "$PUBLIC_KEY" "${template#*__DEPLOY_SSH_PUBLIC_KEY__}" >"$user_data"
  # Ice Lake, 2 ядра по 50%, 4 ГБ, 30 ГБ network-hdd. Метаданные в стиле GCE нужны для IAM-токена машины:
  # им она логинится в реестр перед каждым pull. Метаданные в стиле AWS не нужны никому и выключены явно:
  # IMDSv1 отвечает без особого заголовка, обычная цель SSRF. Контейнерам дорогу к метаданным закрывает
  # cloud-init.yaml (правило в цепочке DOCKER-USER).
  INSTANCE_ID=$(yc_ compute instance create --name "$VM_NAME" --hostname "$VM_NAME" --zone "$ZONE" \
    --platform standard-v3 --cores 2 --core-fraction 50 --memory 4 \
    --create-boot-disk "image-family=ubuntu-2404-lts,image-folder-id=standard-images,size=30,type=network-hdd,auto-delete=true" \
    --network-interface "subnet-id=$SUBNET_ID,nat-ip-version=ipv4,nat-address=$VM_IP,security-group-ids=[$SG_ID]" \
    --service-account-id "$VM_SA_ID" \
    --metadata-options "gce-http-endpoint=enabled,gce-http-token=enabled,aws-v1-http-endpoint=disabled,aws-v1-http-token=disabled,aws-v2-http-endpoint=disabled,aws-v2-http-token=disabled" \
    --metadata-from-file "user-data=$user_data" \
    --format json | jq -r .id)
  JUST_CREATED=1
  echo "Создана"
fi

cat >"$STATE_FILE" <<EOF
# Пишет infra/yc/create.sh. Не секреты, но у каждого свои, поэтому файл в .gitignore.
FOLDER_ID=$FOLDER_ID
ZONE=$ZONE
REGISTRY_ID=$REGISTRY_ID
REGISTRY=cr.yandex/$REGISTRY_ID
CI_SA_ID=$CI_SA_ID
VM_SA_ID=$VM_SA_ID
FEDERATION_ID=$FEDERATION_ID
CREDENTIAL_ID=$CREDENTIAL_ID
ADDRESS_ID=$ADDRESS_ID
VM_IP=$VM_IP
NETWORK_ID=$NETWORK_ID
SUBNET_ID=$SUBNET_ID
SG_ID=$SG_ID
INSTANCE_ID=$INSTANCE_ID
EOF

# Ключи хоста берутся из вывода последовательного порта: их печатает cloud-init в самом конце первой загрузки,
# уже после установки Docker. Так known_hosts получается не с первого подключения вслепую, а из облака.
# Печатает он их только в первую загрузку, после перезагрузки (resize.sh) их там нет. Поэтому ждём их, только
# если машина создана сейчас или ключей этого IP ещё нет в known_hosts.
if [[ $JUST_CREATED == 1 ]] || ! grep -q "^$VM_IP " "$KNOWN_HOSTS_FILE" 2>/dev/null; then
  step "Ключи хоста (cloud-init печатает их в конце первой загрузки, обычно 3–6 минут)"
  host_keys=""
  for _ in $(seq 1 60); do
    host_keys=$(yc_ compute instance get-serial-port-output --id "$INSTANCE_ID" 2>/dev/null |
      tr -d '\r' |
      sed -n '/-----BEGIN SSH HOST KEY KEYS-----/,/-----END SSH HOST KEY KEYS-----/p' |
      grep -oE '(ssh-ed25519|ecdsa-sha2-nistp[0-9]+|ssh-rsa) AAAA[A-Za-z0-9+/=]+' || true)
    [[ -n $host_keys ]] && break
    sleep 15
  done
  if [[ -n $host_keys ]]; then
    while read -r key_type key; do
      echo "$VM_IP $key_type $key"
    done <<<"$host_keys" >"$KNOWN_HOSTS_FILE"
    echo "Записаны в $KNOWN_HOSTS_FILE"
  else
    echo "Ключи хоста за 15 минут не появились. Посмотреть вывод: yc compute instance get-serial-port-output --id $INSTANCE_ID" >&2
    echo "и записать строки из блока SSH HOST KEY KEYS в $KNOWN_HOSTS_FILE как «$VM_IP <тип> <ключ>»." >&2
  fi
fi

# Сначала сам SSH, потом Docker: по сообщению сразу видно, что из двух не так.
vm_ssh() {
  ssh -i "$SSH_PRIVATE_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS_FILE" -o ConnectTimeout=15 "deploy@$VM_IP" "$@"
}
if grep -q "^$VM_IP " "$KNOWN_HOSTS_FILE" 2>/dev/null; then
  step "Машина по SSH"
  if ! vm_ssh true; then
    echo "SSH пока не отвечает. Проверить позже: ssh -i $SSH_PRIVATE_KEY -o UserKnownHostsFile=$KNOWN_HOSTS_FILE deploy@$VM_IP" >&2
  # docker info идёт в сам демон от имени deploy: заодно видно, что deploy в группе docker.
  elif vm_ssh 'cloud-init status --wait >/dev/null; docker info >/dev/null && docker compose version'; then
    echo "SSH по ключу deploy работает, Docker на месте"
  else
    echo "SSH работает, а Docker нет. Лог первой загрузки на машине: sudo cat /var/log/cloud-init-output.log" >&2
    echo "Поставить заново: sudo /usr/local/sbin/install-docker.sh, потом перезайти по SSH." >&2
  fi
fi

step "Готово"
cat <<EOF
Реестр:          $REGISTRY_ID (cr.yandex/$REGISTRY_ID)
CI:              $CI_SA_NAME $CI_SA_ID
Машина читает:   $VM_SA_NAME $VM_SA_ID
Федерация:       $FEDERATION_ID, sub $OIDC_SUBJECT
IP:              $VM_IP
Машина:          $INSTANCE_ID
Состояние:       $STATE_FILE

Дальше: uv run infra/github/set_secrets.py — секреты и переменные GitHub.
EOF
if [[ -n $OLD_IP_NOTE ]]; then
  echo
  echo "$OLD_IP_NOTE"
fi
