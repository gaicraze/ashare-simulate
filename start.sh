#!/usr/bin/env bash
# Stock 项目启动脚本（Linux / macOS）
# 用法：
#   ./start.sh start      # 启动后端(8000) + 前端(8082)
#   ./start.sh stop       # 停止全部
#   ./start.sh status     # 查看状态
#   ./start.sh restart    # 重启全部
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.run"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
BACKEND_PORT=8000
FRONTEND_PORT=8082
BACKEND_LOG="$RUN_DIR/backend.log"
BACKEND_ERR="$RUN_DIR/backend.err"
FRONTEND_LOG="$RUN_DIR/frontend.log"
FRONTEND_ERR="$RUN_DIR/frontend.err"
BACKEND_PID="$RUN_DIR/backend.pid"
FRONTEND_PID="$RUN_DIR/frontend.pid"

mkdir -p "$RUN_DIR"

# 优先使用后端虚拟环境里的 Python
if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
    PY="$BACKEND_DIR/.venv/bin/python"
else
    PY="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"
fi

http_ok() {
    curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null | grep -q '200'
}
backend_up()   { http_ok "http://127.0.0.1:${BACKEND_PORT}/api/health"; }
frontend_up()  { http_ok "http://127.0.0.1:${FRONTEND_PORT}/"; }

start_backend() {
    if backend_up; then echo "[start] 后端已在运行 (${BACKEND_PORT})"; return 0; fi
    if [ -z "$PY" ] || [ ! -x "$PY" ]; then
        echo "[start] 错误: 找不到 Python。请先运行 ./setup.sh 完成部署。"
        return 1
    fi
    echo "[start] 启动后端 :${BACKEND_PORT} ..."
    ( cd "$BACKEND_DIR" && nohup "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" \
        >>"$BACKEND_LOG" 2>>"$BACKEND_ERR" & echo $! > "$BACKEND_PID" )
    sleep 2
    backend_up && echo "[start] 后端就绪 ✓" || echo "[start] 后端启动中/失败，见 $BACKEND_ERR"
}

start_frontend() {
    if frontend_up; then echo "[start] 前端已在运行 (${FRONTEND_PORT})"; return 0; fi
    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        echo "[start] 错误: frontend/node_modules 缺失，请先运行 ./setup.sh。"
        return 1
    fi
    echo "[start] 启动前端 :${FRONTEND_PORT} ..."
    ( cd "$FRONTEND_DIR" && nohup npm run dev \
        >>"$FRONTEND_LOG" 2>>"$FRONTEND_ERR" & echo $! > "$FRONTEND_PID" )
    sleep 3
    frontend_up && echo "[start] 前端就绪 ✓" || echo "[start] 前端启动中/失败，见 $FRONTEND_ERR"
}

stop_all() {
    for f in "$FRONTEND_PID" "$BACKEND_PID"; do
        if [ -f "$f" ]; then
            pid=$(cat "$f" 2>/dev/null || true)
            [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null && echo "[stop] 已停止 PID $pid" || true
            rm -f "$f"
        fi
    done
    echo "[stop] 完成（如有残留进程，请手动 kill）"
}

status() {
    backend_up  && echo "后端  :${BACKEND_PORT}  运行中 ✓" || echo "后端  :${BACKEND_PORT}  未运行"
    frontend_up && echo "前端  :${FRONTEND_PORT}  运行中 ✓" || echo "前端  :${FRONTEND_PORT}  未运行"
}

case "${1:-start}" in
    start)   start_backend; start_frontend ;;
    stop)    stop_all ;;
    restart) stop_all; sleep 1; start_backend; start_frontend ;;
    status)  status ;;
    *) echo "用法: $0 {start|stop|status|restart}"; exit 1 ;;
esac
