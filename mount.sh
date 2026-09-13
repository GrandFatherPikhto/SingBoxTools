#!/bin/bash
# Монтирует/размонтирует sshfs-точки к серверу shawarma (10.95.2.1) для инспекции
# конфигов zapret/sing-box/bind без захода по ssh за каждой мелочью.
#
# Использование:
#   ./mount-shawarma.sh          # смонтировать всё, что ещё не смонтировано
#   ./mount-shawarma.sh umount   # размонтировать всё

set -euo pipefail

HOST="denis@10.95.2.1"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# локальная_директория:удалённый_путь
MOUNTS=(
  "$BASE_DIR/etc:/etc"
  "$BASE_DIR/sing-box:/home/denis/sing-box"
)

do_mount() {
  for entry in "${MOUNTS[@]}"; do
    local_dir="${entry%%:*}"
    remote_path="${entry#*:}"
    if mountpoint -q "$local_dir" 2>/dev/null; then
      echo "уже смонтировано: $local_dir"
      continue
    fi
    mkdir -p "$local_dir"
    echo "монтирую $HOST:$remote_path -> $local_dir"
    sshfs "$HOST:$remote_path" "$local_dir"
  done
}

do_umount() {
  for entry in "${MOUNTS[@]}"; do
    local_dir="${entry%%:*}"
    if mountpoint -q "$local_dir" 2>/dev/null; then
      echo "размонтирую $local_dir"
      fusermount -u "$local_dir"
    fi
  done
}

case "${1:-mount}" in
  umount|unmount) do_umount ;;
  mount|"") do_mount ;;
  *) echo "использование: $0 [mount|umount]" >&2; exit 1 ;;
esac
