#!/bin/bash
set -euo pipefail

# ─── Config ───────────────────────────────────────────────────────────────────
IMAGE_NAME="quote-management"
CONTAINER_NAME="quote-management"
HOST_PORT="${PORT:-5001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${DATA_DIR:-$SCRIPT_DIR/data}"

# ─── Helpers ──────────────────────────────────────────────────────────────────
info()  { echo "[INFO]  $*"; }
ok()    { echo "[OK]    $*"; }
die()   { echo "[ERROR] $*" >&2; exit 1; }

command -v podman &>/dev/null || die "podman not found in PATH"

# ─── Persistent data directory ────────────────────────────────────────────────
info "Using data directory: $DATA_DIR"
mkdir -p "$DATA_DIR/uploads"

# Touch the DB file so the volume mount creates a file, not a directory
touch "$DATA_DIR/quotes.db"

# ─── Build image ──────────────────────────────────────────────────────────────
info "Building image '$IMAGE_NAME'..."
podman build -t "$IMAGE_NAME" "$SCRIPT_DIR"
ok "Image built."

# ─── Stop & remove existing container ─────────────────────────────────────────
if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
    info "Stopping existing container '$CONTAINER_NAME'..."
    podman rm -f "$CONTAINER_NAME"
    ok "Removed."
fi

# ─── Initialize DB if empty ───────────────────────────────────────────────────
if [ ! -s "$DATA_DIR/quotes.db" ]; then
    info "Initializing database..."
    podman run --rm \
        -v "$DATA_DIR/quotes.db:/app/quotes.db:Z" \
        "$IMAGE_NAME" \
        python -c "from app import init_db; init_db()"
    ok "Database initialized."
fi

# ─── Run container ────────────────────────────────────────────────────────────
info "Starting container '$CONTAINER_NAME' on port $HOST_PORT..."
podman run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p "${HOST_PORT}:5001" \
    -v "$DATA_DIR/quotes.db:/app/quotes.db:Z" \
    -v "$DATA_DIR/uploads:/app/uploads:Z" \
    "$IMAGE_NAME"

ok "Container started."
echo ""
echo "  App:  http://localhost:${HOST_PORT}"
echo "  Logs: podman logs -f $CONTAINER_NAME"
echo "  Stop: podman rm -f $CONTAINER_NAME"
echo ""
