#!/usr/bin/env bash
# ops/import-upstream.sh — 导入上游固定快照到 upstream/ 目录
#
# 用法:
#   ./ops/import-upstream.sh <交接包ZIP路径>
#
# 说明:
#   从交接包中提取两个固定 ZIP，校验 SHA256，解压到 upstream/。
#   如果 upstream/ 已存在内容，会先备份为 upstream.backup.<timestamp>/。

set -euo pipefail

PKG_ZIP="${1:-}"
if [[ -z "$PKG_ZIP" ]]; then
    echo "Usage: $0 <交接包ZIP路径>"
    echo "Example: $0 ../Nocturne_XinChao连续性迁移_KimiWork交接包_20260803-1121-CST.zip"
    exit 1
fi

if [[ ! -f "$PKG_ZIP" ]]; then
    echo "Error: 交接包不存在: $PKG_ZIP"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ─── 校验交接包 SHA256 ────────────────────────────────────────────────────────
echo "[*] 校验交接包 SHA256..."
EXPECTED_PKG_SHA256="1f3aa21a7e64bd048e8e23207c3d2296f4b740aa8ce2b3cd1707ce99649dfb2f"
if command -v sha256sum &>/dev/null; then
    ACTUAL_PKG_SHA256=$(sha256sum "$PKG_ZIP" | awk '{print $1}')
else
    ACTUAL_PKG_SHA256=$(shasum -a 256 "$PKG_ZIP" | awk '{print $1}')
fi

if [[ "$ACTUAL_PKG_SHA256" != "$EXPECTED_PKG_SHA256" ]]; then
    echo "Error: 交接包 SHA256 不匹配！"
    echo "  Expected: $EXPECTED_PKG_SHA256"
    echo "  Actual:   $ACTUAL_PKG_SHA256"
    exit 1
fi
echo "  ✓ 交接包 SHA256 校验通过"

# ─── 提取子 ZIP ───────────────────────────────────────────────────────────────
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo "[*] 解压交接包..."
unzip -q "$PKG_ZIP" -d "$TMPDIR"

PKG_DIR=$(find "$TMPDIR" -maxdepth 1 -type d | tail -n 1)
SNAPSHOT_DIR="$PKG_DIR/02_上游固定快照"

echo "[*] 校验上游 ZIP SHA256..."
NOCTURNE_ZIP="$SNAPSHOT_DIR/Nocturne-Memory-Core_8fecd3bbce9025bf05e2c6ef2311dfe4341ef38b.zip"
XINCHAO_ZIP="$SNAPSHOT_DIR/xinchao-dynamic-mind_9c36803629a98b95a4ec73c58809809800e10e6b.zip"

EXPECTED_NOCTURNE_SHA256="59ee2d8911e75f74f16686e8e8aef6d85ba20fc3954ca6e4086f848f124a47ce"
EXPECTED_XINCHAO_SHA256="278d613a8ad00aa63d7b397fcb917d530427dd9b27065cb64b9ec5acd9f95044"

for zip_file expected in \
    "$NOCTURNE_ZIP" "$EXPECTED_NOCTURNE_SHA256" \
    "$XINCHAO_ZIP" "$EXPECTED_XINCHAO_SHA256"; do
    if command -v sha256sum &>/dev/null; then
        actual=$(sha256sum "$zip_file" | awk '{print $1}')
    else
        actual=$(shasum -a 256 "$zip_file" | awk '{print $1}')
    fi
    if [[ "$actual" != "$expected" ]]; then
        echo "Error: ZIP SHA256 不匹配: $zip_file"
        exit 1
    fi
    echo "  ✓ $(basename "$zip_file") SHA256 校验通过"
done

# ─── 备份并解压 ───────────────────────────────────────────────────────────────
echo "[*] 备份现有 upstream/ 目录..."
if [[ -d upstream/nocturne-memory-core/server.py || -d upstream/xinchao-dynamic-mind/src ]]; then
    BACKUP_DIR="upstream.backup.$(date +%Y%m%d-%H%M%S)"
    cp -r upstream "$BACKUP_DIR"
    echo "  ✓ 已备份到 $BACKUP_DIR/"
fi

echo "[*] 解压上游快照..."
mkdir -p upstream/nocturne-memory-core upstream/xinchao-dynamic-mind

# 先清空（保留 LICENSE/NOTICE）
find upstream/nocturne-memory-core -mindepth 1 -not -name 'LICENSE' -not -name 'NOTICE' -delete 2>/dev/null || true
find upstream/xinchao-dynamic-mind -mindepth 1 -not -name 'LICENSE' -delete 2>/dev/null || true

unzip -q -o "$NOCTURNE_ZIP" -d upstream/nocturne-memory-core/
unzip -q -o "$XINCHAO_ZIP" -d upstream/xinchao-dynamic-mind/

echo "[*] 完成！"
echo ""
echo "upstream/ 目录内容:"
find upstream -maxdepth 2 -type f | sort | head -20
echo "  ... ($(find upstream -type f | wc -l) 个文件)"
