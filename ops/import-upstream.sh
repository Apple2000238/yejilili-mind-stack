#!/usr/bin/env bash
# ops/import-upstream.sh — 导入上游固定快照到 upstream/ 目录
#
# 用法:
#   ./ops/import-upstream.sh <交接包ZIP路径>
#
# 说明:
#   从交接包中提取两个固定 ZIP，校验 SHA256，解压到 upstream/。
#   如果 upstream/ 已存在内容，会先备份为 upstream.backup.<timestamp>/。
#   全程使用 staging 目录，全部校验通过后才原子替换。

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
trap 'rm -rf "$TMPDIR"' EXIT

echo "[*] 解压交接包..."
unzip -q "$PKG_ZIP" -d "$TMPDIR"

PKG_DIR=$(find "$TMPDIR" -maxdepth 1 -type d | tail -n 1)
SNAPSHOT_DIR="$PKG_DIR/02_上游固定快照"

echo "[*] 校验上游 ZIP SHA256..."
NOCTURNE_ZIP="$SNAPSHOT_DIR/Nocturne-Memory-Core_8fecd3bbce9025bf05e2c6ef2311dfe4341ef38b.zip"
XINCHAO_ZIP="$SNAPSHOT_DIR/xinchao-dynamic-mind_9c36803629a98b95a4ec73c58809809800e10e6b.zip"

EXPECTED_NOCTURNE_SHA256="59ee2d8911e75f74f16686e8e8aef6d85ba20fc3954ca6e4086f848f124a47ce"
EXPECTED_XINCHAO_SHA256="278d613a8ad00aa63d7b397fcb917d530427dd9b27065cb64b9ec5acd9f95044"

# 使用数组实现双变量迭代
ZIP_FILES=("$NOCTURNE_ZIP" "$XINCHAO_ZIP")
EXPECTED_HASHES=("$EXPECTED_NOCTURNE_SHA256" "$EXPECTED_XINCHAO_SHA256")

for i in "${!ZIP_FILES[@]}"; do
    zip_file="${ZIP_FILES[$i]}"
    expected="${EXPECTED_HASHES[$i]}"

    if [[ ! -f "$zip_file" ]]; then
        echo "Error: 上游 ZIP 不存在: $zip_file"
        exit 1
    fi

    if command -v sha256sum &>/dev/null; then
        actual=$(sha256sum "$zip_file" | awk '{print $1}')
    else
        actual=$(shasum -a 256 "$zip_file" | awk '{print $1}')
    fi
    if [[ "$actual" != "$expected" ]]; then
        echo "Error: ZIP SHA256 不匹配: $zip_file"
        echo "  Expected: $expected"
        echo "  Actual:   $actual"
        exit 1
    fi
    echo "  ✓ $(basename "$zip_file") SHA256 校验通过"
done

# ─── 原子替换：staging -> upstream ────────────────────────────────────────────
# 检查 upstream/ 是否已有内容（通过判断关键路径是否存在）
UPSTREAM_HAS_CONTENT=0
if [[ -e "upstream/nocturne-memory-core/server.py" || -e "upstream/xinchao-dynamic-mind/src/config.js" ]]; then
    UPSTREAM_HAS_CONTENT=1
fi

# 在 staging 目录完成全部解包与校验
STAGING_DIR="upstream.new"
if [[ -e "$STAGING_DIR" ]]; then
    echo "Error: staging 目录 $STAGING_DIR 已存在，请手动清理"
    exit 1
fi

echo "[*] 解压上游快照到 staging..."
mkdir -p "$STAGING_DIR/nocturne-memory-core" "$STAGING_DIR/xinchao-dynamic-mind"

unzip -q -o "$NOCTURNE_ZIP" -d "$STAGING_DIR/nocturne-memory-core/"
unzip -q -o "$XINCHAO_ZIP" -d "$STAGING_DIR/xinchao-dynamic-mind/"

# 校验解压后关键文件存在
if [[ ! -f "$STAGING_DIR/nocturne-memory-core/server.py" ]]; then
    echo "Error: Nocturne 解压后缺少关键文件 server.py"
    rm -rf "$STAGING_DIR"
    exit 1
fi
if [[ ! -f "$STAGING_DIR/xinchao-dynamic-mind/src/config.js" ]]; then
    echo "Error: XinChao 解压后缺少关键文件 src/config.js"
    rm -rf "$STAGING_DIR"
    exit 1
fi

# 备份现有 upstream
if [[ "$UPSTREAM_HAS_CONTENT" -eq 1 ]]; then
    BACKUP_DIR="upstream.backup.$(date +%Y%m%d-%H%M%S)"
    mv upstream/ "$BACKUP_DIR"
    echo "  ✓ 已备份到 $BACKUP_DIR/"
fi

# 原子替换：staging -> upstream
mv "$STAGING_DIR" upstream/

echo "[*] 完成！"
echo ""
echo "upstream/ 目录内容:"
find upstream -maxdepth 2 -type f | sort | head -20
echo "  ... ($(find upstream -type f | wc -l) 个文件)"
