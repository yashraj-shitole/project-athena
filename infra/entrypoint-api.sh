#!/bin/sh
# Entrypoint for the API container. Runs as root to fix ownership on
# the (potentially root-owned) api_storage volume, then drops to the
# unprivileged `athena` user to start uvicorn.
set -eu

STORAGE_DIR="${ATHENA_STORAGE_DIR:-/app/storage}"

# chown only the top of the storage tree. The volume itself is mounted
# at $STORAGE_DIR; everything beneath it was created by this container
# (or was empty) so we own the rest. -R is safe and idempotent.
if [ -d "$STORAGE_DIR" ]; then
    chown -R athena:athena "$STORAGE_DIR" 2>/dev/null || true
else
    mkdir -p "$STORAGE_DIR"
    chown -R athena:athena "$STORAGE_DIR"
fi

# Run the CMD as athena. exec so the shell is replaced.
exec gosu athena "$@"
