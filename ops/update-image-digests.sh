#!/usr/bin/env bash
# ops/update-image-digests.sh — 更新 Dockerfile 和 THIRD_PARTY_MANIFEST 中的镜像 digest
# 用法: ./ops/update-image-digests.sh
#
# 自动执行 docker pull + inspect，将 digest 写入所有 Dockerfile 和 manifest。
# 需要本地 Docker 环境。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# 需要更新的镜像列表
IMAGES=(
    "python:3.12-slim"
    "node:20-alpine"
)

MANIFEST="THIRD_PARTY_MANIFEST.json"
UPDATED=0

for IMAGE in "${IMAGES[@]}"; do
    echo "[*] Processing $IMAGE..."
    if ! docker pull "$IMAGE" &>/dev/null; then
        echo "  WARNING: failed to pull $IMAGE, skipping"
        continue
    fi

    DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE" | cut -d'@' -f2)
    if [[ -z "$DIGEST" ]]; then
        echo "  WARNING: could not extract digest for $IMAGE"
        continue
    fi

    # 更新所有 Dockerfile 中的引用
    while IFS= read -r -d '' dockerfile; do
        if grep -q "FROM $IMAGE@sha256:" "$dockerfile"; then
            sed -i "s|FROM $IMAGE@sha256:[a-f0-9]*|FROM $IMAGE@$DIGEST|" "$dockerfile"
            echo "  ✓ updated $dockerfile"
            UPDATED=1
        fi
    done < <(find . -name Dockerfile -not -path './.git/*' -print0)

    # 更新 THIRD_PARTY_MANIFEST.json
    if [[ -f "$MANIFEST" ]]; then
        KEY="${IMAGE/:/\:}"
        if command -v jq &>/dev/null; then
            tmp=$(mktemp)
            jq --arg key "$IMAGE" --arg digest "$DIGEST" \
                '.dependency_locking.image_digests[$key] = $digest' "$MANIFEST" > "$tmp"
            mv "$tmp" "$MANIFEST"
            echo "  ✓ updated $MANIFEST"
            UPDATED=1
        fi
    fi
done

if [[ "$UPDATED" -eq 1 ]]; then
    echo "[*] Done. Please review changes and commit."
else
    echo "[*] No updates made."
fi
