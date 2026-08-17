"""LLM 驱动策略回测。用法: python scripts/run_backtest.py [start] [end] [decide_every] [stop_loss]"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agent.strategy_agent import LLMStrategyAgent  # noqa: E402
from app.backtest.engine import BacktestEngine  # noqa: E402
from app.llm.gateway import LLMGateway  # noqa: E402
from app.tools.default import default_registry  # noqa: E402

STRATEGY = """【趋势跟踪 + 温和强势选股 + 严格风控】

一、择时（由系统规则自动完成，无需你判断）
- 系统已按沪深300均线判断市场状态 market_state：bull=牛市 / range=震荡 / bear=熊市。
- 系统已给出仓位上限 max_position_pct，你的总仓位不得超过该上限。
- bear 状态系统会自动清仓，不会让你决策。

二、选股（仅 bull 状态下执行，严禁追高）
- 用 rank_by_metric(metric="turnover") 找活跃股，用 screen_by_fundamentals 找基本面好的。
- 用 analyze_price_volume 检查，必须满足：
  1) close_vs_ma20_pct > 0（站上20日均线）；
  2) close_vs_ma20_pct < 15% 且近20日涨幅 < 30%（不追高）；
  3) 量比 > 0.8。
- 优先选换手率适中（5%~20%）、ROE 较高的股票。

三、风控
- 单只 ≤ 25%，同时持有 2~3 只。
- 个股 -6% 自动止损；跌破20日均线卖出。

四、执行
- bull 状态：必须积极建仓，总仓位达到 max_position_pct 的 80%~100%，不要空仓观望。
- range 状态：震荡/无趋势，系统仓位上限为 0，一律空仓，只减仓不加仓，绝不新增买入。
- 不满足选股条件就 hold，宁缺毋滥。
"""

RESULT_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "backtest"


def run(start: str, end: str, decide_every: int = 5, stop_loss: float = -0.08) -> dict:
    gateway = LLMGateway()
    agent = LLMStrategyAgent(gateway, default_registry, STRATEGY)
    engine = BacktestEngine(initial_cash=1_000_000)

    t0 = time.time()
    result = engine.run(agent.decide, start, end, decide_every=decide_every, stop_loss=stop_loss)
    elapsed = time.time() - t0

    metrics = result["metrics"]
    print("\n===== 回测结果 =====")
    print(f"区间: {start} ~ {end}  ({result['trading_days']} 交易日)")
    print(f"总收益: {metrics['total_return']*100:.2f}%")
    print(f"年化收益: {metrics['annual_return']*100:.2f}%")
    print(f"最大回撤: {metrics['max_drawdown']*100:.2f}%")
    print(f"夏普: {metrics['sharpe']}  胜率: {metrics['win_rate']*100:.1f}%")
    print(f"最终资产: {metrics['final_value']:.0f} 元 (初始 {metrics['initial_value']:.0f} 元)")
    print(f"成交笔数: {len(result['trades'])}  耗时: {elapsed:.0f}s")

    # 保存结果
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"backtest_{start}_{end}.json"
    out.write_text(
        json.dumps(
            {"strategy": STRATEGY, "params": {"decide_every": decide_every, "stop_loss": stop_loss},
             "metrics": metrics, "equity_curve": result["equity_curve"], "trades": result["trades"]},
            ensure_ascii=False,
            default=str,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"结果已保存: {out}")
    return result


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2024-03-31"
    decide_every = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    stop_loss = float(sys.argv[4]) if len(sys.argv) > 4 else -0.08
    run(start, end, decide_every, stop_loss)
