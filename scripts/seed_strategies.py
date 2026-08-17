"""向策略中心批量添加经典交易策略（幂等：同名已存在则跳过）。

通过后端 POST /api/strategies 添加，自动生成 id 与时间戳并校验 config。
用法：backend/.venv/bin/python scripts/seed_strategies.py
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"


def _get_existing() -> set[str]:
    try:
        with urllib.request.urlopen(f"{BASE}/api/strategies", timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {s.get("name", "") for s in data.get("strategies", [])}
    except Exception:  # noqa: BLE001
        return set()


def _post(name: str, text: str, config: dict | None) -> dict:
    payload = json.dumps({"name": name, "text": text, "config": config}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/strategies",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


STRATEGIES: list[tuple[str, str, dict | None]] = [
    # 1. 海龟趋势突破
    (
        "海龟趋势突破(唐奇安通道)",
        """【策略目标】
经典海龟交易法则：用唐奇安通道突破捕捉中期趋势，用 ATR 动态计算仓位与止损，严格执行「截断亏损、让利润奔跑」，追求正的绝对收益与较低回撤。

【一、市场择时】
海龟本身不预测大盘、只跟随价格突破，但为控制回撤用 get_market_regime 作参考：
- 熊市（沪深300收盘跌破20日线且 MA20<MA60）：不开任何新仓，只保留已在场且未触发止损的持仓，触发离场即清仓。
- 其余状态（真bull/温和看多/震荡）：正常按突破信号执行，不因大盘择时而漏掉个股趋势。

【二、入场信号（唐奇安通道突破）】
1. 系统1（短线）：当日收盘价创近 20 日最高价 → 突破买入；离场看近 10 日最低价。
2. 系统2（中线）：当日收盘价创近 55 日最高价 → 突破买入；离场看近 20 日最低价。
3. 用 get_stock_daily 计算近 20/55 日最高价与近 10/20 日最低价，用 analyze_price_volume 确认当日量比 >1.2（放量突破，剔除假突破）。
4. 突破标的需基本面正常（非 ST、非亏损），用 get_stock_profile 快速复核。

【三、仓位与头寸管理（ATR 法）】
1. 单笔风险 = 总资产的 1%（1 个 N）。用 get_stock_ta 的 ATR（或近 20 日平均真实波幅）估算波动。
2. 头寸金额 ≈ (总资产×1%) ÷ ATR，使每笔若亏 1 个 ATR 约等于总资产 1%。
3. 同一只股票最多加仓 4 次（每次继续创出新高且未触发止损时加 1 个单位），单只仓位上限 20%。
4. 总持仓最多 8 只，现金≥10%。

【四、风控与止损】
- 硬止损：买入价 − 2N（2×ATR），收盘跌破即次日开盘清仓。
- 离场信号：收盘价跌破近 10 日（系统1）/ 20 日（系统2）最低价 → 次日开盘清仓；A 股只做多，不反向做空。
- 移动止损：浮盈≥2N 后止损位上移锁定利润，随趋势逐级抬升。

【五、执行】
- 每个交易日（decide_every=1）先检查持仓止损/离场信号，再扫描候选突破股。
- 熊市不开新仓；其余状态突破即分批建仓，绝不追高、绝不亏损加仓摊薄。
- 纪律高于判断：止损绝不拖延，突破信号出现就果断执行。""",
        {"version": 1, "timing": {"mode": "autonomous"},
         "position": {"max_total_pct": 0.9, "max_single_pct": 0.2, "max_holdings": 8, "min_cash_pct": 0.1},
         "execution": {"decide_every": 1, "order_price": "next_open"}},
    ),
    # 2. 双均线趋势跟随
    (
        "双均线趋势跟随(金叉死叉)",
        """【策略目标】
经典双均线趋势跟随：以中短期均线金叉/死叉确定买卖方向，指数与个股双重确认，只在上升趋势中做多，用均线止盈止损锁定趋势波段收益。

【一、市场择时（指数双均线）】
用 get_index_trend 判断沪深300（000300）：
- 进攻期（多头）：收盘价站上 MA20 且 MA20>MA60 → 允许开仓至仓位上限。
- 防守期（空头）：收盘价跌破 MA20 且 MA20<MA60 → 空仓或清仓所有持仓。
- 纠缠期（均线缠绕、方向不明）→ 观望，总仓位≤30%，不新增买入。

【二、选股（个股均线共振）】
1. 先用 get_rps_rank(days=60) 或 screen_quality_leaders 初筛强势股。
2. 逐只用 get_stock_ta 确认：个股 MA5>MA20>MA60 多头排列、当日收盘站上 MA20，且 MA5 刚上穿 MA20（金叉）或回踩 MA20 不破后放量回升。
3. 排除：ST、亏损股（PE<0）、PE 畸高、近20日涨幅>40%（追高）、量比>3（爆量）。

【三、风控】
- 持仓 3~5 只，单只≤15%，现金≥20%。
- 止损：个股收盘跌破 MA20 或 MA5 下穿 MA20 死叉 → 次日开盘卖出；浮亏 -8% 无条件止损。
- 止盈：个股浮盈≥20% 后，收盘跌破 MA10 即止盈离场，锁定利润。

【四、执行】
- 进攻期：对出现金叉/回踩企稳的个股分批买入至目标仓位。
- 防守期：清仓，不新增买入。
- 每 2 个交易日决策一次（decide_every=2）；均线未发生变化则 hold 不动。""",
        {"version": 1, "timing": {"mode": "autonomous"},
         "position": {"max_total_pct": 0.9, "max_single_pct": 0.15, "max_holdings": 5, "min_cash_pct": 0.2},
         "risk": {"stop_loss_pct": -0.08},
         "execution": {"decide_every": 2, "order_price": "next_open"}},
    ),
    # 3. 红利低波
    (
        "红利低波(高股息防守)",
        """【策略目标】
红利低波防守策略：配置高股息率、低波动、经营稳健的行业龙头，靠分红与低回撤穿越牛熊，追求熊市抗跌、震荡市稳健的正收益。

【一、市场择时】
用 get_market_regime 判断：
- 真bull/温和看多：正常持有，仓位上限 80%。
- 震荡市：持有防守组合，仓位上限 60%。
- 熊市：降至 40% 以内，只保留股息率最高、波动最低的核心持仓，不新增买入。

【二、选股（红利+低波+质量）】
1. 用 screen_by_fundamentals(min_roe=10, min_yoy_profit=5) 初筛盈利稳健的股票。
2. 用 rank_by_metric(metric=pe_ttm) 找低估值，再用 get_stock_profile 复核：股息率高、PE/PB 低、负债率低、盈利多年稳定。
3. 用 get_stock_ta / analyze_price_volume 计算近 60 日波动率，优先低波动（振幅小、回撤浅）的公用事业/银行/消费/交运等行业龙头。
4. 排除：ST、亏损股、股息率低于市场平均、近一年暴涨过的题材股。

【三、风控】
- 持仓 5~8 只，行业分散（单一行业≤30%），单只≤12%，现金≥20%。
- 止损：仅基本面恶化（ROE 转负 / 业绩大幅下滑）或收盘跌破年线（MA250）才清仓；正常波动不止损。
- 止盈：短期暴涨脱离价值（偏离20日线>30% 且股息率显著下降）时减仓锁利。

【四、执行】
- 每 5 个交易日决策一次（decide_every=5），低频持有为主。
- 熊市只减不加；回调时分批低吸，不主动追高。""",
        {"version": 1, "timing": {"mode": "autonomous"},
         "position": {"max_total_pct": 0.8, "max_single_pct": 0.12, "max_holdings": 8, "min_cash_pct": 0.2},
         "execution": {"decide_every": 5, "order_price": "next_open"}},
    ),
    # 4. 价值质量
    (
        "价值质量(好公司好价格)",
        """【策略目标】
价值+质量策略：以「低估值 + 高ROE + 稳定盈利」三维度选股，以合理价格买入好公司并长期持有，追求穿越周期的稳定复利。

【一、市场择时】
- 用 get_market_regime：真bull/温和看多仓位上限 80%；震荡市 50%；熊市降至 30%，只持有最优质核心，逢低分批买入而不恐慌清仓。
- 用 get_market_sentiment：市场极度恐慌（大跌家数远多于大涨、成交萎缩）反而是分批买入好公司的良机。

【二、选股（好公司+好价格）】
1. 用 screen_by_fundamentals(min_roe=15, min_net_margin=10, min_yoy_profit=10) 初筛高盈利、高增长股票。
2. 用 rank_by_metric(metric=pe_ttm) 与 rank_by_metric(metric=pb_mrq) 找估值偏低者；用 get_stock_profile 复核：ROE 连续多年≥15%、毛利率/净利率高且稳定、有护城河的行业龙头（消费/医药/高端制造等）。
3. 用 screen_fundamental_trend 确认基本面趋势向上（盈利持续改善）。
4. 排除：PE<0（亏损）、PE>50（过贵）、ROE<10、负债率过高、靠一次性收益粉饰利润的股票。

【三、风控】
- 持仓 4~6 只，单只≤20%，现金≥20%。
- 卖出条件：买入逻辑破坏才卖——ROE/净利率明显恶化、业绩变脸、或估值已极度高估（PE 远超行业均值）→ 卖出；纯股价波动不触发止损。
- 用「低估值安全边际 + 公司质量」替代紧止损，避免被波动洗出。

【四、执行】
- 每 5 个交易日决策一次（decide_every=5），低频换手。
- 熊市逢低分批买入优质股，牛市估值过高时分批减仓；长期持有让复利发挥作用。""",
        {"version": 1, "timing": {"mode": "autonomous"},
         "position": {"max_total_pct": 0.8, "max_single_pct": 0.2, "max_holdings": 6, "min_cash_pct": 0.2},
         "execution": {"decide_every": 5, "order_price": "next_open"}},
    ),
    # 5. 动量轮动
    (
        "动量轮动(RPS强势股)",
        """【策略目标】
动量轮动策略：买入近期相对强度（RPS）最高、趋势最强的强势股，定期轮换淘汰走弱个股，追求「强者恒强」带来的超额收益。

【一、市场择时】
用 get_market_regime：
- 真bull/温和看多：满仓轮动，仓位上限 85%。
- 震荡市：半仓参与，仓位上限 50%，只做 RPS 前 3% 的最强龙头。
- 熊市：空仓（动量策略在熊市最危险，必须空仓规避）。

【二、选股（动量+趋势）】
1. 用 get_rps_rank(date=决策日, days=120) 取 RPS120 前 20，再用 days=60 复核短期动量。
2. 逐只用 get_stock_ta / analyze_price_volume 确认：现价站上 MA5 且 MA5>MA10>MA20 多头排列、近20日涨幅居前但未过热（偏离20日线<25%）、量比 1~3 健康放量。
3. 用 get_industry_performance 识别强势主线行业，优先选择强势行业内的龙头股。
4. 排除：ST、换手率>30% 的过度炒作股、近5日累计涨幅>15% 的追高标的。

【三、风控与轮动】
- 持仓 5~8 只，单只≤15%，现金≥15%。
- 止损/换股：个股收盘跌破 MA10、或 RPS 排名明显下滑（跌出前 50%）→ 卖出，换成新的强势股。
- 每 3 个交易日决策一次（decide_every=3），定期检查持仓动量，汰弱留强。

【四、执行】
- 牛市/看多：按 RPS 排名买入最强龙头至目标仓位。
- 震荡市：轻仓只做最强；熊市：清仓空仓。
- 核心是「汰弱留强」：不断把资金从走弱股轮动到走强股。""",
        {"version": 1, "timing": {"mode": "autonomous"},
         "position": {"max_total_pct": 0.85, "max_single_pct": 0.15, "max_holdings": 8, "min_cash_pct": 0.15},
         "execution": {"decide_every": 3, "order_price": "next_open"}},
    ),
    # 6. 主力资金跟随
    (
        "主力资金跟随(资金流)",
        """【策略目标】
主力资金跟随策略：跟随主力资金的持续净流入方向选股，量价资金共振确认买入，资金流出即离场，追求与主力同向的波段收益。

【一、市场择时】
用 get_market_regime + get_market_sentiment：
- 市场放量上涨（成交额环比上升、涨跌家数比>1）：积极跟随，仓位上限 80%。
- 缩量震荡：轻仓，仓位上限 40%。
- 放量下跌 / 情绪冰点：空仓或清仓，等待资金回流。

【二、选股（资金流入）】
1. 用 get_moneyflow_rank(date=决策日, days=10) 取近10日主力资金累计净流入前 20，再用 days=20 复核持续性。
2. 逐只用 get_stock_moneyflow 确认：主力/超大单连续净流入、流入量占成交额比例高。
3. 用 analyze_price_volume 确认量价配合：资金净流入 + 股价温和上涨（涨幅未透支）、量比健康，剔除「放量滞涨 / 高开低走」的出货形态。
4. 排除：ST、短期暴涨过高的纯情绪股、资金流入但股价已破位下跌的背离股。

【三、风控】
- 持仓 4~6 只，单只≤15%，现金≥20%。
- 止损：个股主力资金连续3日净流出、或收盘跌破10日线 → 次日开盘卖出；浮亏 -7% 无条件止损。
- 止盈：主力资金开始净流出 + 股价滞涨 → 减仓/清仓。

【四、执行】
- 每 2 个交易日决策一次（decide_every=2），资金面变化快需高频跟踪。
- 资金流入确认即买，资金拐头即卖，严格纪律不恋战。""",
        {"version": 1, "timing": {"mode": "autonomous"},
         "position": {"max_total_pct": 0.8, "max_single_pct": 0.15, "max_holdings": 6, "min_cash_pct": 0.2},
         "risk": {"stop_loss_pct": -0.07},
         "execution": {"decide_every": 2, "order_price": "next_open"}},
    ),
    # 7. 均值回归
    (
        "均值回归(超跌反弹)",
        """【策略目标】
均值回归策略：在优质股出现短期超跌（RSI超卖、布林带下轨、负乖离过大）时分批低吸，反弹至均值附近卖出，赚取均值回归的价差。

【一、市场择时】
- 用 get_market_regime：熊市 / 单边下跌趋势中抢反弹风险极高，禁止（空仓）。
- 震荡市：最适合均值回归，仓位上限 60%。
- 真bull/温和看多：只对回踩不破趋势的优质股做回归，仓位上限 70%。

【二、选股（超跌+优质）】
1. 标的必须是优质股（基本面不恶化）：用 screen_quality_leaders 或 screen_by_fundamentals(min_roe=10) 初筛。
2. 用 get_stock_ta 找超跌信号：RSI(6)<30 或 RSI(12)<35、收盘价触及/跌破布林带下轨、股价偏离20日线<-10%（负乖离过大）。
3. 用 analyze_price_volume 确认缩量止跌：下跌量能萎缩、出现企稳/下影线，量比<1。
4. 排除：ST、亏损股、业绩暴雷股（基本面恶化的超跌是价值陷阱，坚决不碰）。

【三、风控与止盈】
- 持仓 4~6 只，单只≤12%，现金≥30%（留足子弹分批）。
- 止损：买入后继续破位（再跌 -5% 或跌破关键支撑）→ 无条件止损，绝不越跌越买（避免价值陷阱）。
- 止盈：反弹至 MA20/布林中轨附近、或浮盈达 +8%~+15% → 分批止盈，见好就收。
- 分批买入：超跌后分 2~3 笔买入，每笔间隔（或每再跌 3%）加一笔。

【四、执行】
- 每 1~2 个交易日决策一次（decide_every=1），超跌反弹窗口短需快速反应。
- 严格「只做优质股超跌、严格止损、见好就收」，不贪婪、不恋战。""",
        {"version": 1, "timing": {"mode": "autonomous"},
         "position": {"max_total_pct": 0.7, "max_single_pct": 0.12, "max_holdings": 6, "min_cash_pct": 0.3},
         "execution": {"decide_every": 1, "order_price": "next_open"}},
    ),
]


def main() -> int:
    existing = _get_existing()
    added, skipped = 0, 0
    for name, text, config in STRATEGIES:
        if name in existing:
            print(f"[跳过] 已存在：{name}")
            skipped += 1
            continue
        try:
            resp = _post(name, text, config)
            if "error" in resp:
                print(f"[失败] {name}: {resp['error']}")
                return 1
            print(f"[添加] {name} -> id={resp.get('id')}")
            added += 1
        except Exception as e:  # noqa: BLE001
            print(f"[异常] {name}: {type(e).__name__}: {e}")
            return 1
    print(f"\n完成：新增 {added} 个，跳过 {skipped} 个。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
