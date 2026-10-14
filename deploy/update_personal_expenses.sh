#!/usr/bin/env bash
# Load the newest image tar from container_files/ and recreate the personal-expenses container.
# Run on the server: bash <docker-config-dir>/personal-expenses/update_personal_expenses.sh
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TAR_DIR="$SCRIPT_DIR/container_files"
SERVICE=personal-expenses

latest_tar="$(ls -1t "$TAR_DIR"/$SERVICE-*.tar 2>/dev/null | head -n1 || true)"
if [[ -z "$latest_tar" ]]; then
  echo "No $SERVICE-*.tar files found in $TAR_DIR" >&2
  exit 1
fi

echo "[INFO] Loading $latest_tar"
load_output="$(docker load -i "$latest_tar")"
echo "$load_output"
if [[ "$load_output" =~ Loaded\ image:\ ([^[:space:]]+) ]]; then
  new_image="${BASH_REMATCH[1]}"
  docker tag "$new_image" $SERVICE:latest
else
  echo "[ERROR] Could not find the loaded image name" >&2
  exit 1
fi

old_tars="$(ls -1t "$TAR_DIR"/$SERVICE-*.tar | tail -n +2 || true)"
if [[ -n "$old_tars" ]]; then
  echo "[INFO] Removing old tars:"; echo "$old_tars"
  echo "$old_tars" | xargs rm -f
fi

(cd "$SCRIPT_DIR/.." && docker compose up -d --force-recreate $SERVICE)

echo "[INFO] Waiting for health check..."
status=missing
for _ in $(seq 1 30); do
  status="$(docker inspect -f '{{.State.Health.Status}}' $SERVICE 2>/dev/null || echo missing)"
  [[ "$status" == "healthy" ]] && break
  sleep 5
done
docker ps --filter name=$SERVICE --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
docker logs --tail 15 $SERVICE
if [[ "$status" != "healthy" ]]; then
  echo "[ERROR] $SERVICE is $status" >&2
  exit 1
fi

# Only prune once the new container is healthy, so a failed deploy can still roll back.
new_id="$(docker image inspect -f '{{.Id}}' "$new_image")"
old_ids="$(docker images --no-trunc $SERVICE --format '{{.ID}}' | sort -u | grep -vxF "$new_id" || true)"
if [[ -n "$old_ids" ]]; then
  for id in $old_ids; do
    echo "[INFO] Removing old image $(docker image inspect -f '{{join .RepoTags ", "}}' "$id") (${id:7:12})"
    docker rmi -f "$id" >/dev/null || echo "[WARN] Could not remove image $id" >&2
  done
fi
docker images $SERVICE
