# 大A交易策略真实模拟器

用**真实历史行情**逐日模拟交易，验证你的股票交易策略是否真能赚钱，并在一次次模拟中不断打磨、完善它。把「一条完整的交易思路」（选股 → 持仓 → 卖出）当作**策略**，由大模型扮演“基金经理”，在历史时间轴上逐日调用工具做分析决策，输出模拟绩效与完整决策轨迹。

> ⚠️ **免责声明**：本项目仅供**学习与研究**，模拟结果不代表真实收益，不构成任何投资建议。股市有风险，入市需谨慎，请勿据此实盘交易。
>
> 本项目为个人业余时间创作，与本人工作无关。

---

## ✨ 功能特性

- **数据湖**：本地 Parquet + DuckDB 数据湖，5073 只 A 股 5 年日线（2021-05 ~ 2026-07），本地优先、在线兜底（腾讯/新浪直连 + akshare）。
- **LLM 网关**：统一网关 + 多 provider 按需路由（DeepSeek / MiniMax / OpenAI / 通义 / GLM / Ollama…任意 OpenAI 兼容服务），失败自动 fallback。
- **工具集**：统一工具注册表（内置技术面/基本面/资金流工具 + 大模型自造 SQL 工具）。
- **策略 Agent**：LLM 逐日决策循环 + 记忆，function calling 调用工具分析选股。
- **回测引擎**：撮合、账户、A 股规则（T+1 / 涨跌停 / ST 5% / 手续费 / 流动性约束）、绩效归因。
- **回测闭环**：回测报告 → 策略优化 → 保存为新策略再回测。
- **知识中心**：股票交易知识库（九大一级领域分类体系，知识图谱 + 思维导图，支持手动录入与 URL/正文「吸收」入库，策略生成/优化时 RAG 检索）。
- **交易分析中心**：选定策略后结合最新盘面（盘中自动拉取实时指数与个股快照）给出操作意见——既支持不结合仓位的个股买入意见，也支持结合真实持仓（含本金 / 可用现金 / 仓位）的买卖/加减仓/现金管理建议，并支持「补充说明」注入个性化要求（风险偏好/行业偏好/资金安排等）。
- **Web 看板**：React + TS + Vite + ECharts，策略管理 / 行情工具 / 策略决策 / 回测看板 / 数据管理 / 知识中心 / 交易分析中心。

## 📦 快速开始

### 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | ≥ 3.11 | 建议 3.12 |
| Node.js | ≥ 18 | 前端构建与开发服务器 |
| 磁盘空间 | ≥ 1.5 GB | 数据湖重建后约 700MB |

### 一键部署（推荐）

脚本会**自动检测当前环境**（操作系统、Python、Node 版本），创建虚拟环境、拉取依赖、重组数据分片并重建数据湖，最后启动服务。

```bash
# Linux / macOS
./setup.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

部署完成后浏览器访问 **http://127.0.0.1:8082**。

> 若已克隆仓库且只想启动/停止：
> ```bash
> ./start.sh start     # Linux/macOS：start | stop | status | restart
> .\start.ps1 start    # Windows：start | stop | status | restart
> ```

### 手动部署（可选）

```bash
# 1. 后端：创建虚拟环境并安装依赖
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e backend        # Windows: backend\.venv\Scripts\pip install -e backend

# 2. 前端：安装依赖
cd frontend && npm install && cd ..

# 3. 重建数据湖（重组分片 + 导入 DuckDB）
backend/.venv/bin/python scripts/rebuild_lake.py

# 4. 启动（后端 :8000 + 前端 :8082）
./start.sh start
```

## 🔑 首次运行：配置大模型 API

首次打开看板时，若未配置任何模型，系统会**自动弹出引导弹窗**，可选择预设（DeepSeek / MiniMax / OpenAI / 通义千问 / 智谱 GLM / Ollama 本地 / 自定义），填入 API Key 即可。

- API Key 仅保存在本地 `backend/data/llm_config.json`（已加入 `.gitignore`，不会提交）。
- 也可在左侧「模型配置」页随时添加 / 删除模型，并为「回测执行、策略生成、策略优化」等不同用途单独指定模型。
- 支持任意 **OpenAI 兼容** 接口，详情见 [`docs/architecture.md`](docs/architecture.md)。

## 📊 数据说明

数据源自 HuggingFace [`ANTICH/traderharness-ashare-5y`](https://huggingface.co/datasets/ANTICH/traderharness-ashare-5y)：

| 文件 | 内容 |
|---|---|
| `daily.parquet` | 5073 只股票日线 OHLCV（约 553 万行，2021-05 ~ 2026-07） |
| `valuation.parquet` | 换手率 / PE(TTM) / PB(MRQ) / PS / 是否 ST |
| `fundamentals.parquet` | 季度财务：ROE / 净利率 / 毛利率 / 净利同比等 |
| `dividends.parquet` | 分红送转 |
| `index_300.parquet` | 沪深 300 指数日线 |

> **关于大文件**：`daily.parquet`（约 77MB）与 `valuation.parquet`（约 143MB）超过 GitHub 单文件限制，已拆成 `<50MB` 的 `*.parquet.part*` 分片随仓库分发；`backend/data/duckdb/market.duckdb`（约 700MB，可重建）不进入仓库。首次运行 `scripts/rebuild_lake.py` 会自动**重组分片 → 重建 DuckDB**。维护者在更新数据后可运行 `python scripts/split_data.py` 重新分片。

**在线数据补齐**：首次联网后，可通过看板「数据管理」页的「元数据回填 / 增量更新」补齐股票名称、行业、复权因子与最新交易日数据；也可用脚本：

```bash
python scripts/download_data.py        # 重新下载原始数据
python scripts/update_daily.py         # 增量更新当日行情
```

## 🧭 架构总览

```
策略定义(自然语言) → 结构化存储 → 回测执行(LLM逐日决策) → 交易撮合 → 绩效评估 → 结果报告 → 策略优化 → 再回测（闭环）
```

- **数据层**：`backend/app/data/` —— 本地数据湖（Parquet + DuckDB）
- **LLM 层**：`backend/app/llm/` —— 统一网关 + 多 provider 路由 + fallback
- **工具层**：`backend/app/tools/` —— 统一工具注册表（内置 + 数据查询 + 自造工具）
- **执行层**：`backend/app/agent/` —— LLM 策略执行 Agent（逐日决策循环）
- **回测层**：`backend/app/backtest/` —— 撮合、账户、A 股规则、绩效归因
- **看板层**：`frontend/` —— React + TS + Vite + ECharts

## 📁 目录结构

```
Stock
├── backend/            # FastAPI 后端
│   ├── app/
│   │   ├── api/        # 路由
│   │   ├── core/       # 配置
│   │   ├── data/       # 数据湖：schema / 导入 / 增量 / 查询
│   │   ├── llm/        # LLM 网关（多 provider 路由 + fallback）
│   │   ├── tools/      # 工具注册表 + 内置工具 + 自造工具
│   │   ├── agent/      # 策略执行 Agent
│   │   ├── backtest/   # 回测引擎
│   │   ├── strategy/   # 策略存储
│   │   ├── analysis/   # 个股深度分析
│   │   ├── trading/    # 交易分析中心（盘面快照 / 真实持仓 / 操作建议）
│   │   └── knowledge/  # 知识中心
│   ├── data/           # 本地数据（parquet / duckdb / 策略 / 知识）
│   └── pyproject.toml
├── frontend/           # React 前端看板
├── scripts/            # 数据下载 / 落库 / 重建 / 分片 / 部署
├── docs/               # 设计文档
├── setup.sh            # 一键部署（Linux/macOS）
├── setup.ps1           # 一键部署（Windows）
├── start.sh            # 启停脚本（Linux/macOS）
└── start.ps1           # 启停脚本（Windows）
```

## 📝 更新记录（Changelog）

### v0.2.0

- 新增「交易分析中心」：选定策略后结合最新盘面（盘中自动拉取实时指数与个股快照）给出操作意见，支持「个股买入意见（不结合仓位）」与「结合真实仓位的买卖建议」两种模式。
- 新增真实持仓本地管理（录入 / 更新 / 删除），持仓仅保存在本地。
- 新增账户资金管理（本金 / 可用现金），两种模式下都结合账户资金，持仓诊断结合总资产、仓位比例与相对本金盈亏，给出加减仓 / 现金管理建议。
- 新增前端「资产概览」实时展示：持仓市值 + 可用现金 = 总资产、仓位/现金比例、相对本金盈亏，持仓表显示每只持仓的现价/市值/浮盈亏。
- 新增「补充说明」输入：个性化要求（风险偏好 / 行业偏好 / 资金安排 / 规避某类股票等）优先级最高，一并作为上下文提供给模型。
- 新增操作建议历史（查看 / 导出 Markdown / 删除）。
- 新增「交易分析中心」用途的模型路由（默认走强模型）。

### v0.1.0

- 首个开源版本：数据湖 + LLM 网关 + 工具集 + 策略 Agent + 回测引擎 + 回测闭环 + 知识中心 + Web 看板。

## 📚 文档

- [架构设计](docs/architecture.md)
- [环境搭建与运行](docs/setup.md)
- [系统操作说明](docs/USAGE.md)

## 📄 License

本项目采用 [MIT License](LICENSE) 开源。

## ☕ 打赏说明

项目目前还没什么起色，自己也还没赚到钱，就先**不开通打赏**了，这里先留图占位；等以后项目能真正帮到大家、也赚到钱了，再放收款码。

<div align="center">
  <img src="docs/assets/donate-qr.png" width="240" alt="赞赏码占位" />
</div>