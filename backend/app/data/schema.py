"""本地数据湖的 DuckDB schema 定义。

表结构随阶段演进：
- P0: stocks / daily / indices / finances / moneyflow / sectors
- P1: daily 增加 pe_ttm/pb_mrq（估值），finances 扩展为 fundamentals 完整字段
"""
from __future__ import annotations

# 股票基础信息
STOCKS_DDL = """
CREATE TABLE IF NOT EXISTS stocks (
    code        VARCHAR PRIMARY KEY,   -- 6位代码，如 600519 / 000001
    name        VARCHAR,               -- 股票名称
    industry    VARCHAR,               -- 所属行业
    list_date   DATE,                  -- 上市日期
    delist_date DATE,                  -- 退市日期（空=在交易）
    status      VARCHAR                -- 状态标识（正常/退市/停牌等）
);
"""

# 日线行情（未复权原始价 + 复权因子 + 换手率 + 估值）
DAILY_DDL = """
CREATE TABLE IF NOT EXISTS daily (
    code         VARCHAR,
    trade_date   DATE,
    open         DOUBLE,
    high         DOUBLE,
    low          DOUBLE,
    close        DOUBLE,
    volume       DOUBLE,               -- 成交量（股）
    amount       DOUBLE,               -- 成交额（元）
    adj_factor   DOUBLE,               -- 后复权因子（用于计算复权价）
    pct_change   DOUBLE,               -- 涨跌幅（%）
    turnover     DOUBLE,               -- 换手率（%，来自 valuation.turn）
    float_mktcap DOUBLE,               -- 流通市值（元）
    pe_ttm       DOUBLE,               -- 市盈率 TTM（来自 valuation.pe_ttm）
    pb_mrq       DOUBLE,               -- 市净率 MRQ（来自 valuation.pb_mrq）
    PRIMARY KEY (code, trade_date)
);
"""

# 指数日线
INDICES_DDL = """
CREATE TABLE IF NOT EXISTS indices (
    code       VARCHAR,
    name       VARCHAR,
    trade_date DATE,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    volume     DOUBLE,
    amount     DOUBLE,
    PRIMARY KEY (code, trade_date)
);
"""

# 财务（季度，字段对齐 fundamentals.parquet）
FINANCES_DDL = """
CREATE TABLE IF NOT EXISTS finances (
    code              VARCHAR,
    report_date       DATE,            -- 报告期 stat_date
    pub_date          DATE,            -- 发布日期
    revenue           DOUBLE,          -- 营业收入
    net_profit        DOUBLE,          -- 归母净利润
    roe               DOUBLE,          -- 净资产收益率（小数，0.0275=2.75%）
    gross_margin      DOUBLE,          -- 毛利率（小数）
    net_profit_margin DOUBLE,          -- 净利率（小数）
    eps_ttm           DOUBLE,          -- 每股收益 TTM
    yoy_net_profit    DOUBLE,          -- 归母净利同比（小数）
    yoy_eps           DOUBLE,          -- EPS 同比（小数）
    yoy_equity        DOUBLE,          -- 净资产同比（小数）
    yoy_asset         DOUBLE,          -- 总资产同比（小数）
    PRIMARY KEY (code, report_date)
);
"""

# 资金流
MONEYFLOW_DDL = """
CREATE TABLE IF NOT EXISTS moneyflow (
    code             VARCHAR,
    trade_date       DATE,
    main_net_inflow  DOUBLE,           -- 主力净流入
    super_net_inflow DOUBLE,           -- 超大单净流入
    large_net_inflow DOUBLE,           -- 大单净流入
    PRIMARY KEY (code, trade_date)
);
"""

# 板块
SECTORS_DDL = """
CREATE TABLE IF NOT EXISTS sectors (
    code       VARCHAR,               -- 股票代码
    sector     VARCHAR,               -- 板块/概念名称
    trade_date DATE,                  -- 归属快照日期
    PRIMARY KEY (code, sector, trade_date)
);
"""

# 按创建顺序合并执行
DDL = "\n".join(
    [
        STOCKS_DDL,
        DAILY_DDL,
        INDICES_DDL,
        FINANCES_DDL,
        MONEYFLOW_DDL,
        SECTORS_DDL,
    ]
)

# 各表列名（供导入映射与文档使用）
TABLE_COLUMNS = {
    "stocks": ["code", "name", "industry", "list_date", "delist_date", "status"],
    "daily": [
        "code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adj_factor",
        "pct_change",
        "turnover",
        "float_mktcap",
        "pe_ttm",
        "pb_mrq",
    ],
    "indices": ["code", "name", "trade_date", "open", "high", "low", "close", "volume", "amount"],
    "finances": [
        "code",
        "report_date",
        "pub_date",
        "revenue",
        "net_profit",
        "roe",
        "gross_margin",
        "net_profit_margin",
        "eps_ttm",
        "yoy_net_profit",
        "yoy_eps",
        "yoy_equity",
        "yoy_asset",
    ],
    "moneyflow": [
        "code",
        "trade_date",
        "main_net_inflow",
        "super_net_inflow",
        "large_net_inflow",
    ],
    "sectors": ["code", "sector", "trade_date"],
}
