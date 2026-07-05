#!/bin/sh
# Entrypoint for the API container. The image's USER is `athena` (uid
# non-root) for the actual process, but Docker mounts named volumes as
# root by default, so the storage tree can be root-owned on first boot.
# This entrypoint fixes ownership on the storage volume (which requires
# root), then drops back to the unprivileged `athena` user to run uvicorn.
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

# If we're already running as the athena user (e.g. the orchestrator
# invoked us with --user athena), skip the re-exec  -  gosu would refuse to
# drop to the same uid and there's nothing to drop.
CURRENT_UID="$(id -u 2>/dev/null || echo 0)"
if [ "$CURRENT_UID" = "0" ]; then
    if ! command -v gosu >/dev/null 2>&1; then
        echo "entrypoint: running as root but 'gosu' is not installed; refusing to start uvicorn as root" >&2
        exit 1
    fi
    exec gosu athena "$@"
else
    exec "$@"
fi