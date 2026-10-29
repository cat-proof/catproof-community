#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
command -v docker >/dev/null 2>&1 || { echo 'Install and start Docker Desktop or Docker Engine with Compose v2.' >&2; exit 1; }
docker compose version >/dev/null
case "${1:-start}" in
  start|init)
    if [ ! -f .env ]; then
      docker run --rm --user "$(id -u):$(id -g)" --mount "type=bind,src=$(pwd),dst=/workspace" --workdir /workspace python:3.12-slim python scripts/init_env.py
    fi
    if [ "${1:-start}" = init ]; then exit 0; fi
    case "${2:-}" in
      --build) docker compose build app ;;
      '') docker compose pull app ;;
      *) echo 'Usage: ./community.sh start [--build]' >&2; exit 2 ;;
    esac
    docker compose up -d --no-build --wait
    printf '\nCatProof is running at https://localhost:8443/signup\n'
    printf 'Export the local CA with ./community.sh certificate and trust it manually before browser sign-in.\n'
    ;;
  stop) docker compose down ;;
  status) docker compose ps ;;
  logs) docker compose logs --tail=100 app web ;;
  certificate)
    docker compose exec -T web cat /data/caddy/pki/authorities/local/root.crt > catproof-local-ca.crt
    echo 'Exported catproof-local-ca.crt. Follow docs/INSTALL.md to configure browser trust.'
    ;;
  *) echo 'Usage: ./community.sh {start [--build]|init|stop|status|logs|certificate}' >&2; exit 2 ;;
esac
