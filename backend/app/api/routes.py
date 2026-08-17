"""数据查询 API 路由。"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from ..agent.agent import Agent
from ..analysis import stock as stock_analysis
from ..analysis import store as analysis_store
from ..backtest import optimize, readiness, report, runner, tasks
from ..backtest.policy import validate_config
from ..core import config
from ..data import lake, query, scheduler, sources, updater
from ..llm import config_store
from ..llm.gateway import LLMGateway, extract_content
from ..knowledge import store as knowledge_store
from ..strategy import store as strategy_store
from ..tools import custom
from ..tools.custom import SQLTool
from ..tools.default import default_registry

router = APIRouter()

# 历史回填任务运行状态（后台线程共享）
_backfill_running: dict = {"running": False, "started_at": None, "result": None}


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = {}


class DecideRequest(BaseModel):
    strategy: str
    date: str | None = None
    max_rounds: int = 6


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    api_key: str
    model: str


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    enabled: bool | None = None


class RoleSet(BaseModel):
    role: str
    provider_id: str | None = None


class ToolGenerateRequest(BaseModel):
    requirement: str


class BacktestRunRequest(BaseModel):
    strategy: str
    start: str
    end: str
    decide_every: int = 5
    stop_loss: float | None = None
    initial_cash: float = 1_000_000
    strategy_name: str = ""
    config: dict | None = None
    force: bool = False  # 跳过回测前就绪门控，强制开跑


class StrategyCreate(BaseModel):
    name: str
    text: str
    config: dict | None = None


class StrategyUpdate(BaseModel):
    name: str | None = None
    text: str | None = None
    config: dict | None = None


class StrategyGenerateRequest(BaseModel):
    idea: str


class ReportRequest(BaseModel):
    file: str
    with_llm: bool = True


class OptimizeRequest(BaseModel):
    file: str
    strategy: str | None = None


class BackfillRequest(BaseModel):
    codes: list[str] | None = None
    force: bool = False


class StockAnalysisRequest(BaseModel):
    q: str  # 股票代码或名称


STRATEGY_GEN_PROMPT = """你是一名A股量化交易策略专家。用户会给出一个简短的策略思路（自然语言），
请你把它补全成一份完整、结构化的交易策略。策略内容只描述交易逻辑本身，不要出现任何技术实现细节（不要提函数名、工具名、接口名、代码、数据库字段等）。

必须包含以下部分（用【】标注标题）：
【策略目标】用一句话概括策略。
【一、市场择时】如何判断市场环境（牛市/熊市/震荡市），不同环境下的仓位。给出可量化的判断标准（如"指数站上20日均线且20日均线上穿60日均线视为牛市"）。
【二、选股逻辑】选什么样的股票，给出可量化的条件（如"换手率5%~20%的活跃股"、"ROE>10%"、"收盘价站上20日均线且近20日涨幅<30%的温和强势股"）。
【三、风控与止损】止损策略（如"跌破买入价 8% 止损"、"跌破 20 日均线止损"、"回撤 15% 止盈"，也可写明"长线持有不止损"——止损方式由策略自主决定，不做强制）、单只仓位上限、持仓数量、调仓频率。
【四、执行规则】什么情况下买入、卖出、持有观望，以及何时对持仓执行止损。

要求：
1. 规则要具体、可量化（如"止损 -6%"、"单只≤25%"、"近20日涨幅<30%"）。
2. 只写交易策略本身的逻辑，不要写"调用XX工具""用XX函数"之类的话。
3. 语言简洁，避免空话套话。

{knowledge}
用户思路：{idea}
"""


@router.get("/data/summary")
def data_summary() -> dict:
    """数据湖总览：各表行数 + 日线覆盖范围。"""
    return {
        "db_path": str(config.DB_PATH),
        "tables": lake.table_summary(config.DB_PATH),
        "daily": query.get_daily_date_range(config.DB_PATH),
        "latest_trade_date": query.get_latest_trade_date(config.DB_PATH),
    }


@router.get("/market/overview")
def market_overview() -> dict:
    """当日最新行情概览：最新交易日 + 市场状态 + 涨跌/涨跌停/成交额。"""
    conn = lake.get_connection(config.DB_PATH, read_only=True)
    try:
        row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
        latest = str(row[0]) if row and row[0] else None
        if not latest:
            return {"latest_trade_date": None, "market_regime": "未知", "snapshot": None}
        snap = conn.execute(
            """
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN pct_change > 0 THEN 1 ELSE 0 END) AS up,
              SUM(CASE WHEN pct_change < 0 THEN 1 ELSE 0 END) AS down,
              SUM(CASE WHEN pct_change >= 9.8 THEN 1 ELSE 0 END) AS limit_up,
              SUM(CASE WHEN pct_change <= -9.8 THEN 1 ELSE 0 END) AS limit_down,
              ROUND(AVG(pct_change), 3) AS avg_pct,
              ROUND(SUM(amount), 0) AS total_amount
            FROM daily WHERE trade_date = ?
            """,
            [latest],
        ).fetchone()
        sr = conn.execute(
            """
            SELECT close,
              AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
              AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60
            FROM indices WHERE code='000300' AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 1
            """,
            [latest],
        ).fetchone()
        regime = "未知"
        if sr and sr[0] and sr[1] and sr[2]:
            close, ma20, ma60 = sr[0], sr[1], sr[2]
            if close > ma20 > ma60:
                regime = "牛市"
            elif close < ma20 < ma60:
                regime = "熊市"
            else:
                regime = "震荡市"
        return {
            "latest_trade_date": latest,
            "market_regime": regime,
            "snapshot": {
                "total": snap[0],
                "up": snap[1],
                "down": snap[2],
                "limit_up": snap[3],
                "limit_down": snap[4],
                "avg_pct": snap[5],
                "total_amount": snap[6],
            },
        }
    finally:
        conn.close()


@router.get("/market/kline")
def market_kline(start: str, end: str) -> dict:
    """返回沪深300指数在指定区间的日 K 线（供回测 K 线图）。"""
    conn = lake.get_connection(config.DB_PATH, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT trade_date AS date, open, high, low, close, volume
            FROM indices WHERE code='000300' AND trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
            """,
            [start, end],
        ).fetchall()
        return {"kline": [{"date": str(r[0]), "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]}
    finally:
        conn.close()


@router.post("/data/update")
def data_update() -> dict:
    """手动触发数据增量更新（腾讯快照全市场当日行情）。"""
    return scheduler.scheduler.run_once()


@router.get("/data/update/status")
def data_update_status() -> dict:
    """数据更新状态：调度器 + 最近一次更新 + 历史回填进度 + 新鲜度 + 数据源。"""
    return {
        "scheduler": scheduler.scheduler.status(),
        **updater.get_update_status(),
        "freshness": updater.freshness(),
        "source": sources.data_source_status(),
    }


@router.get("/data/source")
def data_source() -> dict:
    """当前在线数据源与 akshare 可用性（DATA_SOURCE=akshare 切换）。"""
    return sources.data_source_status()


@router.post("/data/backfill")
def data_backfill(req: BackfillRequest | None = None) -> dict:
    """触发历史回填（daily 的 amount/adj_factor/turnover/float_mktcap）。

    逐股拉取腾讯后复权 K 线，网络量大、耗时较长，故在后台线程执行；
    进度写入 data/update_state.json，可经 GET /data/update/status 查询。
    """
    req = req or BackfillRequest()
    if _backfill_running["running"]:
        return {"ok": False, "error": "已有回填任务在运行中", "backfill": updater.get_update_status()["backfill"]}

    def worker() -> None:
        _backfill_running["running"] = True
        _backfill_running["started_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _backfill_running["result"] = updater.backfill_daily_fields(
                codes=req.codes, resume=not req.force, force=req.force
            )
        except Exception as e:  # noqa: BLE001
            _backfill_running["result"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            _backfill_running["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True, "force": req.force}


@router.get("/data/backfill/status")
def data_backfill_status() -> dict:
    return {
        "running": _backfill_running["running"],
        "started_at": _backfill_running["started_at"],
        "result": _backfill_running["result"],
        "progress": updater.get_update_status()["backfill"],
    }


@router.post("/data/meta/backfill")
def data_meta_backfill() -> dict:
    """回填 stocks 元数据（name/industry/list_date）并重建 sectors 表（新浪源）。"""
    def worker() -> dict:
        try:
            return updater.backfill_stock_meta()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return worker()


@router.post("/data/backfill/days")
def data_backfill_days() -> dict:
    """补齐数据湖的日期空洞（缺失交易日）。后台线程执行。"""
    if _backfill_running["running"]:
        return {"ok": False, "error": "已有回填任务在运行中"}

    def worker() -> None:
        _backfill_running["running"] = True
        _backfill_running["started_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _backfill_running["result"] = updater.fill_missing_daily_days()
        except Exception as e:  # noqa: BLE001
            _backfill_running["result"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            _backfill_running["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True}


@router.post("/data/backfill/moneyflow")
def data_backfill_moneyflow() -> dict:
    """触发资金流回填（新浪个股资金流，全历史）。后台线程执行，可断点续跑。"""
    if _backfill_running["running"]:
        return {"ok": False, "error": "已有回填任务在运行中"}

    def worker() -> None:
        _backfill_running["running"] = True
        _backfill_running["started_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _backfill_running["result"] = updater.backfill_moneyflow()
        except Exception as e:  # noqa: BLE001
            _backfill_running["result"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            _backfill_running["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True}


@router.get("/data/daily")
def daily(
    code: str | None = Query(None, description="6位股票代码"),
    start: str | None = Query(None, description="起始日期 YYYY-MM-DD"),
    end: str | None = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(1000, ge=1, le=10000),
) -> dict:
    return {"rows": query.get_daily(config.DB_PATH, code, start, end, limit)}


@router.get("/data/stocks")
def stocks() -> dict:
    return {"rows": query.get_stock_list(config.DB_PATH)}


@router.get("/stocks/names")
def stock_names() -> dict:
    """返回股票代码→名称映射。"""
    conn = lake.get_connection(config.DB_PATH, read_only=True)
    try:
        rows = conn.execute("SELECT code, name FROM stocks WHERE name IS NOT NULL AND name != ''").fetchall()
        return {"names": {r[0]: r[1] for r in rows}}
    finally:
        conn.close()


@router.get("/stocks/search")
def stock_search(q: str = Query("", description="股票代码或名称关键字")) -> dict:
    """按代码前缀或名称子串搜索股票（供行情中心按名称查询）。"""
    return {"rows": query.search_stocks(config.DB_PATH, q)}


@router.post("/analysis/stock")
def stock_analysis_run(req: StockAnalysisRequest) -> dict:
    """个股深度分析：按代码/名称采集多维数据 + LLM 生成结构化研报。

    结果会自动落盘保存（供「历史分析」查阅与导出），响应里附带 file / markdown 等字段。
    """
    q = (req.q or "").strip()
    if not q:
        return {"ok": False, "error": "请输入股票代码或名称"}
    try:
        s = stock_analysis.resolve_stock(q)
        if not s:
            return {"ok": False, "error": f"未找到匹配的股票：{q}"}
        result = stock_analysis.analyze(s["code"])
        file = analysis_store.save_analysis(result).name
        full = analysis_store.load_analysis(file)
        return {"ok": True, **full}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/analysis/history")
def analysis_history() -> dict:
    """列出历史个股深度分析结果（元信息 + 摘要预览）。"""
    return {"items": analysis_store.list_analyses()}


@router.get("/analysis/result")
def analysis_result(file: str = Query(..., description="分析结果文件名")) -> dict:
    """读取某次个股深度分析的完整结果。"""
    d = analysis_store.load_analysis(file)
    if d is None:
        return {"ok": False, "error": "结果不存在"}
    return {"ok": True, **d}


@router.get("/analysis/export")
def analysis_export(file: str = Query(..., description="分析结果文件名")) -> Response:
    """把某次个股深度分析导出为 Markdown 文件下载。"""
    d = analysis_store.load_analysis(file)
    if d is None:
        return Response(content="结果不存在", status_code=404)
    filename = Path(file).stem + ".md"
    return Response(
        content=d["markdown"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/analysis/result")
def analysis_delete(file: str = Query(..., description="分析结果文件名")) -> dict:
    """删除某次历史个股深度分析结果。"""
    return {"deleted": analysis_store.delete_analysis(file)}


@router.get("/tools")
def list_tools() -> dict:
    """列出全部可用工具及其 schema（供 LLM function calling）。"""
    return {"names": default_registry.list_names(), "tools": default_registry.list_schemas()}


@router.post("/tools/call")
def call_tool(req: ToolCallRequest) -> dict:
    """调用指定工具。"""
    try:
        result = default_registry.call(req.name, **req.arguments)
        return {"ok": True, "name": req.name, "result": result}
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


TOOL_GEN_PROMPT = """你是数据分析工具生成器。用户需要一个数据查询工具，请生成一个 SQL 工具的 JSON 定义。
系统是 DuckDB 数据库，表结构：
- daily(code, trade_date, open, high, low, close, volume, amount, pct_change, turnover, pe_ttm, pb_mrq)：个股日线
- stocks(code, name, industry, list_date, status)：股票基础信息
- finances(code, report_date, pub_date, revenue, net_profit, roe, gross_margin, net_profit_margin, eps_ttm, yoy_net_profit)：季度财务
- indices(code, name, trade_date, open, high, low, close, volume, amount)：指数日线
- moneyflow(code, trade_date, main_net_inflow, super_net_inflow, large_net_inflow)：资金流
- sectors(code, sector, trade_date)：板块

要求：
1. 只生成 SELECT 查询（禁止写操作）。
2. 尽量让 SQL 自包含：不需要外部输入时直接用固定条件（如 roe > 0.1），不要用参数。
3. 确实需要外部输入时，用命名参数 :xxx，且必须在 parameters.properties 里完整定义该参数（type/description），并加入 required。
4. 只输出 JSON（不要 markdown 代码块），格式：
{"name":"工具名snake_case","description":"工具描述","parameters":{"type":"object","properties":{...},"required":[...]},"sql":"SELECT ..."}

用户需求：{requirement}
"""


@router.get("/tools/custom")
def list_custom_tools() -> dict:
    """列出已生成的自造工具。"""
    return {"tools": custom.list_custom_defs()}


@router.post("/tools/generate")
def tool_generate(req: ToolGenerateRequest) -> dict:
    """用 LLM 根据需求生成一个自造 SQL 工具，校验后注册。"""
    try:
        gateway = LLMGateway()
        resp = gateway.chat(
            [{"role": "user", "content": TOOL_GEN_PROMPT.replace("{requirement}", req.requirement)}],
            max_tokens=1000,
            role="strategy_gen",
            temperature=0.2,
        )
        content = extract_content(resp)
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:])
            if content.rstrip().endswith("```"):
                content = content.rstrip()[:-3]
        import json as _json
        def_ = _json.loads(content)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"生成/解析失败：{type(e).__name__}: {e}"}

    err = custom.validate_tool_def(def_)
    if err:
        return {"ok": False, "error": err, "def": def_}

    name = def_["name"]
    if name in default_registry:
        return {"ok": False, "error": f"工具 {name} 已存在"}

    tool = SQLTool(name, def_["description"], def_.get("parameters", {}), def_["sql"])
    default_registry.register(tool)
    custom.save_custom_tool(def_)
    return {"ok": True, "tool": def_}


@router.delete("/tools/custom/{name}")
def delete_custom_tool(name: str) -> dict:
    """删除一个自造工具。"""
    return {"deleted": custom.delete_custom_tool(name)}


@router.post("/agent/decide")
def agent_decide(req: DecideRequest) -> dict:
    """单回合策略决策：LLM 根据策略思路调用工具分析并输出结论。"""
    try:
        gateway = LLMGateway()
        agent = Agent(gateway, default_registry)
        result = agent.decide(req.strategy, req.date, req.max_rounds, role="decide")
        return {"ok": True, **result}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/backtest/results")
def backtest_results() -> dict:
    """列出历史回测结果。"""
    return {"results": runner.list_results()}


@router.get("/backtest/result")
def backtest_result(file: str = Query(..., description="结果文件名")) -> dict:
    """读取某次回测的完整结果。"""
    d = runner.load_result(file)
    if d is None:
        return {"error": "结果不存在"}
    return d


@router.post("/backtest/run")
def backtest_run(req: BacktestRunRequest) -> dict:
    """异步启动一次回测，返回 task_id 供轮询。"""
    from datetime import datetime
    for ds in (req.start, req.end):
        try:
            datetime.strptime(ds, "%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": f"日期 {ds} 不合法（需 YYYY-MM-DD 有效日期，如 2026-06-30）"}
    if req.start >= req.end:
        return {"ok": False, "error": "开始日期必须早于结束日期"}

    # 回测前就绪门控：大模型先判断策略能否被现有工具严格执行，
    # 不满足则先自动造工具/报告缺口，确认能执行后才真正开跑（force=True 可跳过）。
    if not req.force:
        try:
            gate = readiness.gate(req.strategy, default_registry)
            if not gate["ready"]:
                return {
                    "ok": False,
                    "ready": False,
                    "error": "策略所需的工具/数据尚不满足，已自动尝试补救，请补齐缺口后再回测（或 force=true 强制开跑）。",
                    "readiness": gate,
                    "markdown": readiness.gaps_markdown(gate),
                }
        except Exception as e:  # noqa: BLE001
            # 就绪检查本身失败不应阻断回测，降级为放行并记录原因
            gate = {"ready": True, "check_error": f"{type(e).__name__}: {e}"}

    task_id = tasks.create_task(
        {
            "start": req.start,
            "end": req.end,
            "decide_every": req.decide_every,
            "stop_loss": req.stop_loss,
            "initial_cash": req.initial_cash,
            "strategy_name": req.strategy_name,
        }
    )

    def worker() -> None:
        try:
            def progress_cb(p: dict) -> None:
                tasks.update_progress(task_id, p)

            payload = runner.run_backtest(
                req.strategy,
                req.start,
                req.end,
                req.decide_every,
                req.stop_loss,
                req.initial_cash,
                strategy_name=req.strategy_name,
                progress_cb=progress_cb,
                stop_check=lambda: tasks.should_stop(task_id),
                config=req.config,
            )
            # 用户中途终止：不落"完成"结果，保持 stopped 状态
            if tasks.should_stop(task_id):
                return
            result_file = runner.save_result(payload).name
            tasks.complete_task(task_id, result_file, payload["metrics"])
        except Exception as e:  # noqa: BLE001
            tasks.fail_task(task_id, f"{type(e).__name__}: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return {"task_id": task_id}


@router.post("/backtest/readiness")
def backtest_readiness(req: BacktestRunRequest) -> dict:
    """回测前就绪检查（只判断不跑）：返回执行计划、能力缺口、自动补救结果与结论。

    相比 /backtest/run 内的瞬时门控，这里额外调用大模型产出「执行计划」，
    方便先看「这个策略打算怎么执行、缺什么」再决定是否回测。
    """
    try:
        gate = readiness.gate(req.strategy, default_registry, force=req.force)
        # 补充 LLM 执行计划（best-effort，失败不影响就绪结论）
        if not gate.get("execution_plan"):
            gate["execution_plan"] = readiness.plan_execution(req.strategy)
        return {"ok": True, "ready": gate["ready"], "readiness": gate, "markdown": readiness.gaps_markdown(gate)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/backtest/status/{task_id}")
def backtest_status(task_id: str) -> dict:
    return tasks.get_task(task_id)


@router.get("/backtest/tasks")
def backtest_tasks() -> dict:
    """列出全部回测任务（运行中 + 历史）。"""
    return {"tasks": tasks.list_all_tasks()}


@router.post("/backtest/tasks/{task_id}/stop")
def backtest_stop(task_id: str) -> dict:
    """终止运行中的回测任务。"""
    return {"stopped": tasks.stop_task(task_id)}


@router.delete("/backtest/tasks/{task_id}")
def backtest_delete(task_id: str) -> dict:
    """删除回测任务（运行中则终止，历史则删除结果文件）。"""
    return {"deleted": tasks.remove_task(task_id)}


@router.post("/backtest/report")
def backtest_report(req: ReportRequest) -> dict:
    """为某次回测结果生成报告（含可选 LLM 总结）。"""
    result = runner.load_result(req.file)
    if result is None:
        return {"error": "结果不存在"}
    rep = report.build_report(result)
    if req.with_llm:
        try:
            rep["summary"] = report.llm_summary(result)
        except Exception as e:  # noqa: BLE001
            rep["summary"] = f"（LLM 总结失败：{type(e).__name__}: {e}）"
    rep["markdown"] = report.to_markdown(rep)
    return rep


@router.post("/backtest/optimize")
def backtest_optimize(req: OptimizeRequest) -> dict:
    """基于某次回测结果优化策略：诊断短板 + 产出优化后的策略文本与配置。

    这是「构建策略 → 实施回测 → 优化策略」闭环的收口一步：把报告里暴露的问题
    （亏损个股、月度回撤、胜率、市场状态分布）反哺成可量化的策略改进。
    """
    result = runner.load_result(req.file)
    if result is None:
        return {"error": "结果不存在"}
    try:
        out = optimize.optimize_strategy(result, req.strategy)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    # 附带回测元信息，便于前端展示「这是针对哪次回测做的优化」
    out["file"] = req.file
    out["params"] = result.get("params")
    out["strategy_name"] = result.get("strategy_name", "")
    return out


@router.get("/strategies")
def list_strategies() -> dict:
    """列出全部策略。"""
    return {"strategies": strategy_store.list_strategies()}


@router.post("/strategies")
def create_strategy(req: StrategyCreate) -> dict:
    err = validate_config(req.config)
    if err:
        return {"error": err}
    return strategy_store.create_strategy(req.name, req.text, req.config)


@router.put("/strategies/{sid}")
def update_strategy(sid: str, req: StrategyUpdate) -> dict:
    err = validate_config(req.config)
    if err:
        return {"error": err}
    s = strategy_store.update_strategy(sid, req.name, req.text, req.config)
    return {"error": "策略不存在"} if s is None else s


@router.delete("/strategies/{sid}")
def delete_strategy(sid: str) -> dict:
    return {"deleted": strategy_store.delete_strategy(sid)}


@router.post("/strategy/generate")
def strategy_generate(req: StrategyGenerateRequest) -> dict:
    """用 LLM 把自然语言策略思路补全成完整策略。

    生成时从知识中心检索相关知识作为参考（RAG），让策略更专业、可量化。
    生成的策略默认使用 autonomous 择时模式（策略文本里已含择时/仓位/止损要求，
    由 LLM 按文本自主执行），也可在保存前于前端手动改为 system/declared。
    """
    knowledge_block, refs = knowledge_store.knowledge_context(req.idea, top_k=3)
    prompt = STRATEGY_GEN_PROMPT.format(idea=req.idea, knowledge=knowledge_block)
    try:
        gateway = LLMGateway()
        resp = gateway.chat([{"role": "user", "content": prompt}], max_tokens=2000, temperature=0.3, role="strategy_gen")
        strategy_text = extract_content(resp)
        if not strategy_text:
            return {"ok": False, "error": "策略生成模型未返回内容（请稍后重试或更换模型）"}
        return {
            "ok": True,
            "strategy": strategy_text,
            "config": {"version": 1, "timing": {"mode": "autonomous"}},
            "knowledge_refs": [{"id": n["id"], "title": n["title"]} for n in refs],
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---- LLM 模型配置管理 ----
@router.get("/llm/config")
def llm_config() -> dict:
    """返回模型配置：providers 列表 + 用途(role)路由。"""
    cfg = config_store.load_config()
    # 不回传 api_key 明文（仅返回是否已配置）
    providers = [{k: ("" if k == "api_key" else v) for k, v in p.items()} for p in cfg["providers"]]
    providers = [{**p, "has_key": bool(next((x for x in cfg["providers"] if x["id"] == p["id"]), {}).get("api_key"))} for p in providers]
    return {"providers": providers, "roles": cfg["roles"], "role_defs": config_store.ROLES}


@router.post("/llm/providers")
def llm_add_provider(req: ProviderCreate) -> dict:
    p = config_store.add_provider(req.name, req.base_url, req.api_key, req.model)
    return {k: ("" if k == "api_key" else v) for k, v in p.items()}


@router.put("/llm/providers/{pid}")
def llm_update_provider(pid: str, req: ProviderUpdate) -> dict:
    fields = {k: v for k, v in req.dict().items() if v is not None}
    p = config_store.update_provider(pid, **fields)
    if p is None:
        return {"error": "模型不存在"}
    return {k: ("" if k == "api_key" else v) for k, v in p.items()}


@router.delete("/llm/providers/{pid}")
def llm_delete_provider(pid: str) -> dict:
    return {"deleted": config_store.delete_provider(pid)}


@router.put("/llm/roles")
def llm_set_role(req: RoleSet) -> dict:
    config_store.set_role(req.role, req.provider_id)
    return {"ok": True}
