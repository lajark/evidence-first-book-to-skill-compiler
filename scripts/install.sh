#!/usr/bin/env bash
# Book2Skill 一键安装脚本（国内镜像加速）
#
# 用法:
#   bash scripts/install.sh              # 安装全部依赖（含可选分组）
#   bash scripts/install.sh --core       # 仅安装核心运行时依赖
#   bash scripts/install.sh --no-dev     # 安装运行时 + 可选，不含 dev 工具
#
# 默认使用清华镜像源加速。如需使用官方 PyPI，设环境变量 USE_OFFICIAL=1。

set -uo pipefail

# --- 镜像选择 ---
if [[ "${USE_OFFICIAL:-0}" == "1" ]]; then
    INDEX_URL=""
    PIP_FLAGS=()
else
    INDEX_URL="-i https://pypi.tuna.tsinghua.edu.cn/simple"
    PIP_FLAGS=("--trusted-host" "pypi.tuna.tsinghua.edu.cn")
    echo "→ 使用清华镜像源（设 USE_OFFICIAL=1 切换官方源）"
fi

# --- 安装分组选择 ---
EXTRAS="pdf,epub,docx,html,ocr,dev"
if [[ "${1:-}" == "--core" ]]; then
    EXTRAS=""
    echo "→ 仅安装核心运行时依赖"
elif [[ "${1:-}" == "--no-dev" ]]; then
    EXTRAS="pdf,epub,docx,html,ocr"
    echo "→ 安装运行时 + 可选依赖（不含 dev 工具）"
fi

# --- 虚拟环境 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ ! -d ".venv" ]]; then
    echo "→ 创建虚拟环境 .venv"
    python -m venv .venv
fi

PIP=".venv/Scripts/pip"

# --- 升级 pip（非致命，失败继续）---
echo "→ 升级 pip"
$PIP install --upgrade pip $INDEX_URL "${PIP_FLAGS[@]}" 2>/dev/null || true

# --- 安装依赖 ---
if [[ -n "$EXTRAS" ]]; then
    echo "→ 安装 book2skill[$EXTRAS]"
    $PIP install -e ".[$EXTRAS]" $INDEX_URL "${PIP_FLAGS[@]}"
else
    echo "→ 安装 book2skill（核心）"
    $PIP install -e . $INDEX_URL "${PIP_FLAGS[@]}"
fi

echo ""
echo "✓ 安装完成。验证："
echo "  .venv/Scripts/book2skill version"
echo "  .venv/Scripts/pytest"
