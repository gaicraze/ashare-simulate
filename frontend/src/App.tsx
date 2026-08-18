import { useEffect, useState } from 'react'
import { fetchSummary, fetchMarketOverview, fetchLlmConfig, updateData, type Summary } from './api'
import FirstRunDialog from './FirstRunDialog'
import QuotesPanel from './QuotesPanel'
import StockAnalysisPanel from './StockAnalysisPanel'
import ToolsPanel from './ToolsPanel'
import StrategyCenter from './StrategyCenter'
import TradingCenter from './TradingCenter'
import BacktestCenter from './BacktestCenter'
import SettingsPanel from './SettingsPanel'
import KnowledgeCenter from './KnowledgeCenter'

const TABS = [
  { id: 'overview', label: '数据总览' },
  { id: 'market', label: '行情与工具' },
  { id: 'strategy', label: '策略中心' },
  { id: 'trading', label: '交易分析中心' },
  { id: 'backtest', label: '回测中心' },
  { id: 'knowledge', label: '知识中心' },
  { id: 'settings', label: '模型配置' },
]

const TABLE_LABELS: Record<string, string> = {
  stocks: '股票',
  daily: '日线',
  indices: '指数',
  finances: '财务',
  moneyflow: '资金流',
  sectors: '板块',
}

const fmt = (n: number | null | undefined) => (n != null ? n.toLocaleString('zh-CN') : '-')

export default function App() {
  const [tab, setTab] = useState('overview')
  const [summary, setSummary] = useState<Summary | null>(null)
  const [backtestStrategyId, setBacktestStrategyId] = useState('')
  const [market, setMarket] = useState<any>(null)
  const [updating, setUpdating] = useState(false)
  const [updateMsg, setUpdateMsg] = useState('')
  const [showFirstRun, setShowFirstRun] = useState(false)

  const jumpToBacktest = (strategyId: string) => {
    setBacktestStrategyId(strategyId)
    setTab('backtest')
  }

  const handleUpdate = async () => {
    setUpdating(true)
    setUpdateMsg('正在更新数据…')
    try {
      const r = await updateData()
      setUpdateMsg(r.ok ? r.message || '更新完成' : '更新失败：' + r.error)
      if (r.ok) {
        fetchSummary().then(setSummary).catch(console.error)
        fetchMarketOverview().then(setMarket).catch(console.error)
      }
    } catch (e) {
      setUpdateMsg('更新失败：' + String(e))
    } finally {
      setUpdating(false)
    }
  }

  useEffect(() => {
    fetchSummary().then(setSummary).catch(console.error)
    fetchMarketOverview().then(setMarket).catch(console.error)
    // 首次运行：未配置任何大模型时，弹窗引导配置 API
    fetchLlmConfig()
      .then((cfg) => {
        if (!cfg?.providers || cfg.providers.length === 0) setShowFirstRun(true)
      })
      .catch(console.error)
  }, [])

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="logo">大A真实模拟器</div>
        <div className="logo-sub">验证策略能否赚钱</div>
        <nav className="nav">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? 'tab active' : 'tab'}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </aside>

      <main className="content">
        {tab === 'overview' && (
          <div>
            <h1>大A交易策略真实模拟器</h1>
            <div className="subtitle">用真实行情逐日模拟 · 验证交易策略能否赚钱 · 不断打磨完善</div>
            <div className="cards">
              {summary &&
                Object.entries(summary.tables).map(([k, v]) => (
                  <div className="card" key={k}>
                    <div className="label">{TABLE_LABELS[k] ?? k}</div>
                    <div className="value">{fmt(v)}</div>
                  </div>
                ))}
              {summary && (
                <div className="card">
                  <div className="label">日线覆盖</div>
                  <div className="value" style={{ fontSize: 15 }}>
                    {summary.daily.min_date}
                    <br />~ {summary.daily.max_date}
                  </div>
                </div>
              )}
            </div>
            {market && (
              <div className="panel">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                  <h2>当日市场行情（{market.latest_trade_date}）</h2>
                  <button className="btn btn-success" onClick={handleUpdate} disabled={updating}>
                    {updating ? '更新中…' : '更新数据'}
                  </button>
                </div>
                {updateMsg && <div className="tool-desc" style={{ margin: '8px 0' }}>{updateMsg}</div>}
                {market.snapshot && (
                  <div className="cards">
                    <div className="card">
                      <div className="label">市场状态</div>
                      <div className="value">{market.market_regime}</div>
                    </div>
                    <div className="card">
                      <div className="label">上涨 / 下跌</div>
                      <div className="value" style={{ fontSize: 16 }}>
                        <span className="up">{market.snapshot.up}</span> / <span className="down">{market.snapshot.down}</span>
                      </div>
                    </div>
                    <div className="card">
                      <div className="label">涨停 / 跌停</div>
                      <div className="value" style={{ fontSize: 16 }}>
                        {market.snapshot.limit_up} / {market.snapshot.limit_down}
                      </div>
                    </div>
                    <div className="card">
                      <div className="label">平均涨跌幅</div>
                      <div className="value" style={{ color: market.snapshot.avg_pct >= 0 ? '#e03131' : '#12b886' }}>
                        {market.snapshot.avg_pct}%
                      </div>
                    </div>
                    <div className="card">
                      <div className="label">总成交额</div>
                      <div className="value" style={{ fontSize: 16 }}>
                        {Math.round((market.snapshot.total_amount || 0) / 1e8)} 亿
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className="panel">
              <h2>系统说明</h2>
              <div className="tool-desc" style={{ lineHeight: 1.7 }}>
                本系统用真实行情逐日模拟交易，验证策略是否真能赚钱：策略以自然语言描述，LLM 通过调用工具（市场环境判断、换手率排名、基本面筛选、量价分析等）做分析决策。
                <br />
                数据沉淀在本地 DuckDB 数据湖（5073 只 A 股日线，2021-05 ~ 2026-07），减少对外部接口的依赖。
                <br />
                数据源：HuggingFace traderharness-ashare-5y（本地 DuckDB + Parquet 存储）。
                <br />
                增量更新：scripts/update_daily.py（akshare）或 ashare-lake，可通过上方「更新数据」按钮触发。
                <br />
                左侧导航可切换各功能模块：行情与工具、策略中心、交易分析中心、回测中心、知识中心、模型配置。
              </div>
            </div>
          </div>
        )}

        {tab === 'market' && (
          <div>
            <QuotesPanel />
            <StockAnalysisPanel />
            <ToolsPanel />
          </div>
        )}
        {tab === 'strategy' && <StrategyCenter onBacktest={jumpToBacktest} />}
        {tab === 'trading' && <TradingCenter />}
        {tab === 'backtest' && <BacktestCenter initialStrategyId={backtestStrategyId} />}
        {tab === 'knowledge' && <KnowledgeCenter />}
        {tab === 'settings' && <SettingsPanel />}
      </main>

      <FirstRunDialog open={showFirstRun} onClose={() => setShowFirstRun(false)} />
    </div>
  )
}
