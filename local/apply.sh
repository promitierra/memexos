#!/bin/sh
# Restore the personal memexos configuration into the stack's volume.
#
# The file names environment variables (api_key_env), never their values, so it
# is safe to keep on disk, outside git.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
STACK="${MEMEX_STACK:-$HOME/docker/memexos}"

test -f "$STACK/compose.yaml" || { echo "no encuentro el stack en $STACK"; exit 1; }
test -f "$HERE/config.toml" || { echo "falta $HERE/config.toml"; exit 1; }

cd "$STACK"
# install -D creates the parent directory; 0600 matches what memex writes.
docker compose run --rm -T -v "$HERE":/in:ro memex \
  sh -c 'install -D -m 600 /in/config.toml /root/.config/memexos/config.toml'

docker compose up -d --force-recreate console >/dev/null
printf 'overlay aplicado en el volumen y consola recreada\n'
