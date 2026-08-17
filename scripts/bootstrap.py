"""一键部署核心：自动检测运行环境并安装依赖、重建数据湖。

职责：
1. 检测操作系统、Python（>=3.11）、Node.js（>=18）；
2. 创建后端虚拟环境（backend/.venv）并安装后端依赖；
3. 安装前端依赖（frontend/node_modules）；
4. 重组 parquet 分片并重建 DuckDB 数据湖；
5. 输出下一步启动指引。

用法（通常由仓库根目录的 setup.sh / setup.ps1 调用）：
    python scripts/bootstrap.py [--skip-data] [--skip-backend] [--skip-frontend]
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"


def log(msg: str, level: str = "info") -> None:
    prefix = {"info": "  •", "ok": "[✓]", "warn": "[!]", "err": "[✗]"}.get(level, "  •")
    print(f"{prefix} {msg}", flush=True)


def section(msg: str) -> None:
    print(f"\n===== {msg} =====", flush=True)


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    print(f"    $ {' '.join(cmd)}", flush=True)
    subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env, check=True)


def py_version(python: str) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            [python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True, text=True, timeout=60,
        )
        a, b = out.stdout.strip().split(".")[:2]
        return int(a), int(b)
    except Exception:  # noqa: BLE001
        return None


def find_python() -> str | None:
    """返回满足 >=3.11 的 Python 解释器路径，优先已有 venv。"""
    candidates: list[str] = []
    venv_py = VENV / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    if venv_py.exists():
        candidates.append(str(venv_py))
    if os.name == "nt":
        candidates += ["py", "python", "python3"]
    else:
        candidates += ["python3", "python"]

    for c in candidates:
        p = shutil.which(c) if not Path(c).is_absolute() else str(Path(c))
        if not p or not Path(p).exists():
            continue
        ver = py_version(p)
        if ver and ver >= (3, 11):
            return p
    return None


def find_node() -> str | None:
    p = shutil.which("node")
    if not p:
        return None
    try:
        out = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
        major = int(out.lstrip("v").split(".")[0])
        return p if major >= 18 else None
    except Exception:  # noqa: BLE001
        return None


def venv_python() -> Path:
    return VENV / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def venv_pip() -> list[str]:
    return [str(venv_python()), "-m", "pip"]


def setup_backend(python: str) -> None:
    section("后端环境")
    if not (venv_python()).exists():
        log(f"创建虚拟环境 {VENV} ...")
        run([python, "-m", "venv", str(VENV)])
    else:
        log(f"虚拟环境已存在，跳过创建：{VENV}")

    log("安装后端依赖（pip install -e backend）...")
    env = os.environ.copy()
    if env.get("PIP_INDEX_URL"):
        log(f"使用镜像源：{env['PIP_INDEX_URL']}")
    cmd = venv_pip() + ["install", "--upgrade", "pip"]
    run(cmd, env=env)
    cmd = venv_pip() + ["install", "-e", str(BACKEND)]
    run(cmd, env=env)
    log("后端依赖安装完成", "ok")


def setup_frontend() -> None:
    section("前端环境")
    if not (FRONTEND / "package.json").exists():
        log("未找到 frontend/package.json，跳过", "warn")
        return
    log("安装前端依赖（npm install）...")
    run(["npm", "install"], cwd=FRONTEND)
    log("前端依赖安装完成", "ok")


def setup_data(python: str) -> None:
    section("数据湖")
    parquet_dir = BACKEND / "data" / "parquet"
    has_chunks = any(parquet_dir.glob("*.part*")) if parquet_dir.exists() else False
    has_parquet = (parquet_dir / "daily.parquet").exists() or (parquet_dir / "fundamentals.parquet").exists()
    db = BACKEND / "data" / "duckdb" / "market.duckdb"

    if not (has_parquet or has_chunks):
        log("未找到本地 parquet 数据，跳过重建。", "warn")
        log("如需下载数据，可运行：python scripts/download_data.py（需联网）")
        return

    if db.exists() and db.stat().st_size > 0:
        log(f"数据湖已存在，跳过重建：{db}")
        return

    log("重建 DuckDB 数据湖（scripts/rebuild_lake.py）...")
    run([python, str(ROOT / "scripts" / "rebuild_lake.py")])
    log("数据湖重建完成", "ok")


def main() -> int:
    ap = argparse.ArgumentParser(description="Stock 一键部署")
    ap.add_argument("--skip-backend", action="store_true")
    ap.add_argument("--skip-frontend", action="store_true")
    ap.add_argument("--skip-data", action="store_true")
    args = ap.parse_args()

    section("环境自检")
    log(f"操作系统：{platform.system()} {platform.release()} ({platform.machine()})")

    python = find_python()
    if not python:
        log("未找到 Python 3.11+。请先安装：https://www.python.org/downloads/", "err")
        return 1
    ver = py_version(python)
    log(f"Python：{python} ({ver[0]}.{ver[1]})", "ok")

    node = find_node()
    if not node:
        log("未找到 Node.js 18+。请先安装：https://nodejs.org/", "err")
        return 1
    out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    log(f"Node.js：{node} ({out})", "ok")

    if not args.skip_backend:
        setup_backend(python)
    else:
        log("已跳过后端依赖安装", "warn")

    if not args.skip_frontend:
        setup_frontend()
    else:
        log("已跳过前端依赖安装", "warn")

    if not args.skip_data:
        setup_data(str(venv_python()) if (venv_python()).exists() else python)
    else:
        log("已跳过数据湖重建", "warn")

    section("部署完成")
    print("下一步启动服务：")
    if os.name == "nt":
        print("    powershell -ExecutionPolicy Bypass -File .\\setup.ps1  （已自动包含启动）")
        print("    或：.\\start.ps1 start")
    else:
        print("    ./start.sh start")
    print("浏览器访问：http://127.0.0.1:8082 （首次运行会提示配置大模型 API）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
