#!/usr/bin/env bash
# Stock 一键部署（Linux / macOS）
# 自动检测环境 → 创建虚拟环境并安装依赖 → 重建数据湖 → 启动服务。
#
# 用法：
#   ./setup.sh              # 部署并启动
#   ./setup.sh --skip-data  # 跳过数据湖重建（透传给 bootstrap.py）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 找一个可用的 Python 来跑 bootstrap（bootstrap 内部会再精确校验版本）
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        PY="$(command -v "$c")"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "[✗] 未找到 Python。请先安装 Python 3.11+：https://www.python.org/downloads/"
    exit 1
fi

echo "===== 开始一键部署 ====="
"$PY" "$ROOT/scripts/bootstrap.py" "$@"

echo ""
echo "===== 启动服务 ====="
"$ROOT/start.sh" start

echo ""
echo "部署完成。浏览器访问：http://127.0.0.1:8082"
echo "常用命令："
echo "  ./start.sh status   # 查看状态"
echo "  ./start.sh stop     # 停止服务"
echo "  ./start.sh restart  # 重启服务"
