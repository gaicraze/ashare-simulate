"""默认工具集：注册全部内置工具，提供全局单例。"""
from __future__ import annotations

from .builtin_analysis import (
    AnalyzePriceVolume,
    GetLimitUpInfo,
    GetMarketRegime,
    GetMarketSentiment,
    GetRpsRank,
    GetStockProfile,
    RankByMetric,
    ScreenByFundamentals,
    ScreenFundamentalTrend,
    ScreenQualityLeaders,
)
from .builtin_data import (
    GetIndexDaily,
    GetLatestTradeDate,
    GetMarketSnapshot,
    GetStockDaily,
    GetStockList,
)
from .builtin_moneyflow import GetMoneyflowRank, GetStockMoneyflow
from .builtin_ta import GetIndexTrend, GetIndustryPerformance, GetStockTA, RankFloatMktcap
from . import custom
from .registry import ToolRegistry


def create_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_many(
        [
            GetStockDaily(),
            GetIndexDaily(),
            GetStockList(),
            GetLatestTradeDate(),
            GetMarketSnapshot(),
            GetMarketRegime(),
            AnalyzePriceVolume(),
            ScreenByFundamentals(),
            RankByMetric(),
            GetRpsRank(),
            GetLimitUpInfo(),
            GetStockProfile(),
            ScreenQualityLeaders(),
            ScreenFundamentalTrend(),
            GetMarketSentiment(),
            GetStockMoneyflow(),
            GetMoneyflowRank(),
            GetIndexTrend(),
            GetStockTA(),
            RankFloatMktcap(),
            GetIndustryPerformance(),
        ]
    )
    # 注册持久化的自造工具
    custom.register_custom_tools(reg)
    return reg


# 全局单例，供 API 与后续 LLM 网关使用
default_registry = create_default_registry()
