# 环境搭建与运行

## 环境要求

| 依赖 | 版本 |
|---|---|
| Python | ≥ 3.11（建议 3.12） |
| Node.js | ≥ 18（建议 20+） |
| 磁盘空间 | ≥ 1.5 GB |

## 一键部署（推荐）

```bash
# Linux / macOS
./setup.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

脚本会自动：

1. 检测操作系统与 Python / Node 版本；
2. 创建后端虚拟环境 `backend/.venv` 并 `pip install -e backend`；
3. `npm install` 安装前端依赖；
4. 重组 `*.parquet.part*` 数据分片 → 重建 DuckDB 数据湖（`scripts/rebuild_lake.py`）；
5. 启动后端（:8000）与前端（:8082）。

浏览器访问 **http://127.0.0.1:8082**。

## 手动部署

```bash
# 1. 后端依赖
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e backend        # Windows: backend\.venv\Scripts\pip install -e backend

# 2. 前端依赖
cd frontend && npm install && cd ..

# 3. 重建数据湖
backend/.venv/bin/python scripts/rebuild_lake.py

# 4. 启动
./start.sh start    # Linux/macOS
.\start.ps1 start   # Windows
```

## 启停

```bash
./start.sh start     # 启动后端(8000) + 前端(8082)
./start.sh status    # 查看状态
./start.sh stop      # 停止
./start.sh restart   # 重启
```

Windows 对应 `.\start.ps1 start|status|stop|restart`。

## 配置大模型 API

- 首次打开看板会弹出引导弹窗，选择预设并填写 API Key 即可（保存在本地 `backend/data/llm_config.json`）。
- 或复制 `backend/.env.example` 为 `backend/.env`，填写 `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY` 等环境变量。
- 支持任意 OpenAI 兼容服务，详见 `docs/architecture.md`。

## 数据下载与更新（可选）

```bash
backend/.venv/bin/python scripts/download_data.py     # 重新下载原始 parquet 数据
backend/.venv/bin/python scripts/update_daily.py      # 增量更新当日行情
backend/.venv/bin/python scripts/rebuild_lake.py      # 重建 DuckDB 数据湖
backend/.venv/bin/python scripts/split_data.py        # 维护者：更新数据后重新分片（供 git 提交）
```

## 环境变量

完整列表见 `backend/.env.example`。常用：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATA_SOURCE` | `tencent` | 在线数据源：`tencent`（腾讯/新浪直连）或 `akshare` |
| `AKSHARE_ENABLED` | `0` | 是否启用 akshare |
| `PROXY` / `DATA_PROXY` | 空 | 可选 HTTP 代理（留空 = 直连） |
| `DATA_AUTO_UPDATE` | `1` | 是否每交易日收盘后自动增量更新 |
| `DATA_UPDATE_AFTER` | `08:00` | 允许自动更新的最早 UTC 时刻（08:00 = 北京 16:00） |
| `DATA_BACKFILL_WORKERS` | `6` | 历史回填并发线程数 |

## 常见问题

- **端口被占用**：后端默认 8000、前端默认 8082，可改 `vite.config.ts` 与 `start.sh/start.ps1`。
- **国内网络下载依赖慢**：可设置 `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` 后再运行 `setup.sh`。
- **首次启动无股票名称/行业**：这是正常的，数据分片仅含行情与财务；联网后在看板「数据管理」页点击「元数据回填」补齐名称与行业。
