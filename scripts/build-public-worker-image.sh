#!/usr/bin/env bash
# Build, push and pin the trusted public render/export worker image.
#
# The container trusted-worker backend (PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=
# container) only launches a digest-pinned image (name@sha256:<digest>), and
# svg generation's render binding needs the image and font-inventory digests
# (PUBLIC_RENDER_WORKER_IMAGE_DIGEST / PUBLIC_RENDER_WORKER_FONT_INVENTORY_DIGEST).
# A registry digest exists only after a push, so this script:
#
#   1. builds the Dockerfile's `public-worker-runtime` target,
#   2. pushes it to REGISTRY and reads back the repository digest,
#   3. fingerprints the fonts inside that exact image (network none), and
#   4. writes the three values as compose variables to OUT (default
#      .env.public-worker), for `docker compose --env-file`.
#
# Usage: REGISTRY=registry.example.com/presenton scripts/build-public-worker-image.sh
#        REGISTRY=localhost:5005 scripts/build-public-worker-image.sh   # local registry
set -euo pipefail

: "${REGISTRY:?set REGISTRY to the registry/namespace to push to, e.g. localhost:5005}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="${IMAGE_NAME:-presenton-public-worker}"
REVISION="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
TREE="$(git -C "$REPO_ROOT" rev-parse 'HEAD^{tree}' 2>/dev/null || echo unknown)"
TAG="${REGISTRY%/}/${NAME}:${REVISION:0:12}"
OUT="${OUT:-$REPO_ROOT/.env.public-worker}"

docker build --target public-worker-runtime \
  --build-arg "SOURCE_REVISION=$REVISION" --build-arg "SOURCE_TREE_DIGEST=$TREE" \
  -t "$TAG" "$REPO_ROOT"
docker push "$TAG" >/dev/null

PINNED="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$TAG" \
  | grep -F "${REGISTRY%/}/${NAME}@sha256:" | head -n1)"
if [[ ! "$PINNED" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "could not read a repository digest for $TAG after the push" >&2
  exit 1
fi
IMAGE_DIGEST="sha256:${PINNED##*@sha256:}"

# The same function the render binding uses, run inside the pinned image as
# its non-root worker user with no network.
FONT_DIGEST="$(docker run --rm --network none --entrypoint /opt/venv/bin/python "$PINNED" -c \
  'from services.public_render_runtime_identity import _local_font_inventory_digest; print(_local_font_inventory_digest({}))' \
  | tail -n1)"
if [[ ! "$FONT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "font inventory digest is invalid: $FONT_DIGEST" >&2
  exit 1
fi

cat > "$OUT" <<EOF
PRESENTON_PUBLIC_WORKER_IMAGE=$PINNED
PRESENTON_PUBLIC_WORKER_IMAGE_DIGEST=$IMAGE_DIGEST
PRESENTON_PUBLIC_WORKER_FONT_INVENTORY_DIGEST=$FONT_DIGEST
EOF
echo "pinned $PINNED"
echo "wrote $OUT"
