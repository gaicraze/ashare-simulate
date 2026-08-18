import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchStrategies,
  fetchTradingMarket,
  fetchPositions,
  updateAccount,
  upsertPosition,
  deletePosition,
  runTradingAdvice,
  fetchAdviceHistory,
  fetchAdviceResult,
  deleteAdvice,
  type Strategy,
  type Position,
  type TradingMarketContext,
  type TradingAdviceResult,
  type TradingAdviceItem,
} from './api'
import Markdown from './Markdown'

const fmtNum = (n: number | null | undefined, digits = 2) =>
  n != null ? Number(n).toFixed(digits) : '-'

const pctCls = (n: number | null | undefined) => (n != null && n < 0 ? 'down' : 'up')

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const TOOL_LABEL: Record<string, string> = {
  get_market_regime: '市场环境判断',
  get_market_snapshot: '市场快照',
  get_market_sentiment: '市场情绪温度计',
  rank_by_metric: '指标排名',
  screen_by_fundamentals: '基本面筛选',
  screen_fundamental_trend: '基本面趋势筛选',
  screen_quality_leaders: '优质龙头筛选',
  get_stock_moneyflow: '个股资金流',
  get_moneyflow_rank: '主力资金排名',
  analyze_price_volume: '量价分析',
  get_stock_profile: '龙头画像',
  get_rps_rank: 'RPS强度排名',
  get_limit_up_info: '涨停统计',
  get_live_quote: '实时行情快照',
}

export default function TradingCenter() {
  const [market, setMarket] = useState<TradingMarketContext | null>(null)
  const [marketLoading, setMarketLoading] = useState(false)

  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selStrategyId, setSelStrategyId] = useState('')
  const [mode, setMode] = useState<'stock' | 'portfolio'>('stock')
  const [scope, setScope] = useState('')

  const [positions, setPositions] = useState<Position[]>([])
  const [acctPrincipal, setAcctPrincipal] = useState('')
  const [acctCash, setAcctCash] = useState('')
  const [posCode, setPosCode] = useState('')
  const [posQty, setPosQty] = useState('')
  const [posCost, setPosCost] = useState('')

  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<TradingAdviceResult | null>(null)
  const [history, setHistory] = useState<TradingAdviceItem[]>([])
  const timerRef = useRef<number | null>(null)

  const loadMarket = useCallback(() => {
    setMarketLoading(true)
    fetchTradingMarket()
      .then(setMarket)
      .catch(() => setMarket(null))
      .finally(() => setMarketLoading(false))
  }, [])

  const loadPositions = useCallback(() => {
    fetchPositions()
      .then((d) => {
        setPositions(d.positions || [])
        setAcctPrincipal(d.account?.principal != null ? String(d.account.principal) : '')
        setAcctCash(d.account?.available_cash != null ? String(d.account.available_cash) : '')
      })
      .catch(() => setPositions([]))
  }, [])

  const loadHistory = useCallback(() => {
    fetchAdviceHistory()
      .then((d) => setHistory(d.items || []))
      .catch(() => setHistory([]))
  }, [])

  useEffect(() => {
    loadMarket()
    loadPositions()
    loadHistory()
    fetchStrategies()
      .then((d) => {
        const list = d.strategies || []
        setStrategies(list)
        if (list.length && !selStrategyId) setSelStrategyId(list[0].id)
      })
      .catch(console.error)
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selStrategy = strategies.find((s) => s.id === selStrategyId)

  const run = async () => {
    if (!selStrategyId) {
      setError('请先在「策略中心」创建并选择一个策略')
      return
    }
    setRunning(true)
    setError(null)
    setResult(null)
    setElapsed(0)
    const t0 = Date.now()
    timerRef.current = window.setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000)
    try {
      const r = await runTradingAdvice({
        strategy_id: selStrategyId,
        mode,
        scope: mode === 'stock' ? scope.trim() : undefined,
      })
      if (!r.ok) {
        setError(r.error || '生成建议失败')
      } else {
        setResult(r)
        loadHistory()
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }

  const addPosition = async () => {
    const qty = Number(posQty)
    const cost = Number(posCost)
    if (!/^\d{6}$/.test(posCode.trim())) {
      setError('请输入6位股票代码')
      return
    }
    if (!qty || qty <= 0 || !cost || cost <= 0) {
      setError('持仓数量和成本价必须大于0')
      return
    }
    setError(null)
    try {
      const r = await upsertPosition({ code: posCode.trim(), quantity: qty, cost_price: cost })
      if (!r.ok) setError(r.error || '保存持仓失败')
      else {
        setPosCode('')
        setPosQty('')
        setPosCost('')
        loadPositions()
      }
    } catch (e) {
      setError(String(e))
    }
  }

  const saveAccount = async () => {
    const principal = acctPrincipal.trim() === '' ? null : Number(acctPrincipal)
    const cash = acctCash.trim() === '' ? null : Number(acctCash)
    if (principal != null && (Number.isNaN(principal) || principal < 0)) {
      setError('本金需为不小于 0 的数字')
      return
    }
    if (cash != null && (Number.isNaN(cash) || cash < 0)) {
      setError('可用现金需为不小于 0 的数字')
      return
    }
    setError(null)
    try {
      const r = await updateAccount({ principal, available_cash: cash })
      if (!r.ok) setError(r.error || '保存账户资金失败')
      else loadPositions()
    } catch (e) {
      setError(String(e))
    }
  }

  const removePosition = async (id: string) => {
    if (!confirm('确定删除该持仓？')) return
    try {
      await deletePosition(id)
      loadPositions()
    } catch (e) {
      setError(String(e))
    }
  }

  const viewHistory = async (file: string) => {
    setError(null)
    try {
      const r = await fetchAdviceResult(file)
      if (r.ok) setResult(r)
      else setError(r.error || '加载历史建议失败')
    } catch (e) {
      setError(String(e))
    }
  }

  const removeHistory = async (file: string) => {
    if (!confirm('确定删除该历史建议？')) return
    try {
      await deleteAdvice(file)
      loadHistory()
      if (result?.file === file) setResult(null)
    } catch (e) {
      setError(String(e))
    }
  }

  const exportCurrent = () => {
    const md = result?.markdown
    if (md) {
      downloadText(`交易建议_${result.strategy_name || result.strategy_id || 'advice'}.md`, md)
    } else if (result?.file) {
      window.location.href = `/api/trading/advice/export?file=${encodeURIComponent(result.file)}`
    }
  }

  const snap = market?.snapshot
  const liveIndex = market?.live_index || {}
  const dataModeLabel = market?.data_mode === 'intraday' ? '盘中实时' : '最新交易日日线'

  return (
    <div>
      {/* 盘面状态 */}
      <div className="panel">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <h2>🛰 盘面状态</h2>
          <button className="btn btn-sm btn-ghost" onClick={loadMarket} disabled={marketLoading}>
            {marketLoading ? '刷新中…' : '刷新盘面'}
          </button>
        </div>
        {market && (
          <>
            <div className="tool-desc" style={{ margin: '4px 0 12px' }}>
              北京时刻 {market.clock.beijing_time} · <b>{market.clock.session}</b> · 数据口径：
              <b style={{ color: market.data_mode === 'intraday' ? '#e03131' : '#3370ff' }}>{dataModeLabel}</b>
              {market.latest_trade_date ? ` · 数据湖最新交易日 ${market.latest_trade_date}` : ''}
            </div>
            <div className="cards" style={{ marginBottom: 0 }}>
              <div className="card">
                <div className="label">市场环境</div>
                <div className="value" style={{ fontSize: 16 }}>{market.regime ?? '-'}</div>
              </div>
              <div className="card">
                <div className="label">上涨 / 下跌</div>
                <div className="value" style={{ fontSize: 16 }}>
                  {snap ? (<><span className="up">{snap.up}</span> / <span className="down">{snap.down}</span></>) : '-'}
                </div>
              </div>
              <div className="card">
                <div className="label">涨停 / 跌停</div>
                <div className="value" style={{ fontSize: 16 }}>{snap ? `${snap.limit_up} / ${snap.limit_down}` : '-'}</div>
              </div>
              <div className="card">
                <div className="label">平均涨跌幅</div>
                <div className={`value ${pctCls(snap?.avg_pct)}`}>{snap ? `${fmtNum(snap.avg_pct)}%` : '-'}</div>
              </div>
              <div className="card">
                <div className="label">总成交额</div>
                <div className="value" style={{ fontSize: 16 }}>
                  {snap ? `${Math.round((snap.total_amount || 0) / 1e8)} 亿` : '-'}
                </div>
              </div>
            </div>
            {Object.keys(liveIndex).length > 0 && (
              <div className="cards" style={{ marginTop: 12, marginBottom: 0 }}>
                {Object.entries(liveIndex).map(([name, v]) => (
                  <div className="card" key={name}>
                    <div className="label">{name}</div>
                    <div className="value" style={{ fontSize: 18 }}>{fmtNum(v.price)}</div>
                    <div className={`label ${pctCls(v.pct_change)}`} style={{ marginBottom: 0 }}>
                      {fmtNum(v.pct_change)}%
                    </div>
                  </div>
                ))}
              </div>
            )}
            {market.notes && market.notes.length > 0 && (
              <div className="tool-desc" style={{ marginTop: 8, color: '#b8860b' }}>
                {market.notes.join('；')}
              </div>
            )}
          </>
        )}
      </div>

      {/* 分析设置 */}
      <div className="panel">
        <h2>🎯 分析设置</h2>
        <div className="tool-desc">
          选择策略与模式，系统结合最新盘面（盘中自动拉取实时指数与相关个股快照）给出操作意见。个股买入意见不结合真实仓位；结合真实仓位模式会针对你的持仓给出买卖/加减仓建议。
        </div>

        <div className="query-bar" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
          <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <span style={{ whiteSpace: 'nowrap' }}>策略</span>
            <select value={selStrategyId} onChange={(e) => setSelStrategyId(e.target.value)} style={{ minWidth: 200 }}>
              {strategies.length === 0 && <option value="">（暂无策略，请先到策略中心创建）</option>}
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </label>

          <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <span style={{ whiteSpace: 'nowrap' }}>模式</span>
            <select value={mode} onChange={(e) => setMode(e.target.value as 'stock' | 'portfolio')} style={{ minWidth: 160 }}>
              <option value="stock">个股买入意见（不结合仓位）</option>
              <option value="portfolio">结合真实仓位（买卖建议）</option>
            </select>
          </label>

          {mode === 'stock' && (
            <input
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              placeholder="指定股票代码/名称（留空=AI全市场选股）"
              style={{ width: 260 }}
            />
          )}

          <button className="btn btn-purple" onClick={run} disabled={running}>
            {running ? `分析中… ${elapsed}s` : '生成操作建议'}
          </button>
        </div>

        {selStrategy && (
          <div className="tool-result" style={{ maxHeight: 160, marginTop: 4 }}>
            {selStrategy.text}
          </div>
        )}

        {error && <div className="tool-desc" style={{ color: '#e03131', marginTop: 8 }}>{error}</div>}
        {running && (
          <div className="tool-desc" style={{ color: '#3370ff', marginTop: 8 }}>
            正在选股、采集盘面数据并调用大模型分析，通常需要 1~3 分钟，请稍候…
          </div>
        )}
      </div>

      {/* 持仓与账户资金管理 */}
      {mode === 'portfolio' && (
        <div className="panel">
          <h2>💼 真实持仓与账户资金</h2>
          <div className="tool-desc">录入本金、可用现金与实际持仓，系统将结合策略与最新盘面给出买卖/加减仓/止损/现金管理建议。数据仅保存在本地。</div>

          <div className="tool-desc" style={{ marginBottom: 6, color: '#1f2329' }}>账户资金（本金 / 可用现金）</div>
          <div className="query-bar" style={{ flexWrap: 'wrap' }}>
            <input value={acctPrincipal} onChange={(e) => setAcctPrincipal(e.target.value)} placeholder="本金（元）" style={{ width: 160 }} />
            <input value={acctCash} onChange={(e) => setAcctCash(e.target.value)} placeholder="可用现金（元）" style={{ width: 160 }} />
            <button className="btn btn-neutral" onClick={saveAccount}>保存账户资金</button>
          </div>

          <div className="tool-desc" style={{ margin: '10px 0 6px', color: '#1f2329' }}>持仓明细（代码 / 数量 / 成本价）</div>
          <div className="query-bar" style={{ flexWrap: 'wrap' }}>
            <input value={posCode} onChange={(e) => setPosCode(e.target.value)} placeholder="代码，如 600519" style={{ width: 130 }} />
            <input value={posQty} onChange={(e) => setPosQty(e.target.value)} placeholder="数量（股）" style={{ width: 120 }} />
            <input value={posCost} onChange={(e) => setPosCost(e.target.value)} placeholder="成本价（元）" style={{ width: 120 }} />
            <button className="btn btn-success" onClick={addPosition}>添加 / 更新持仓</button>
          </div>
          {positions.length === 0 ? (
            <div className="tool-desc" style={{ margin: 0 }}>暂无持仓记录。</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left' }}>股票</th>
                  <th>数量</th>
                  <th>成本价</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.id}>
                    <td style={{ textAlign: 'left' }}>{p.name ? `${p.name}` : p.code}{p.name ? <span style={{ color: '#8a919f' }}> {p.code}</span> : ''}</td>
                    <td>{fmtNum(p.quantity, 0)}</td>
                    <td>{fmtNum(p.cost_price)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button className="btn btn-sm btn-danger" onClick={() => removePosition(p.id)}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* 结果 */}
      {result && result.ok && (
        <div className="panel analysis-result">
          <div className="analysis-head">
            <div>
              <span className="analysis-name">📊 {result.strategy_name || '操作建议'}</span>
              <span className="analysis-tag">{result.mode === 'portfolio' ? '结合真实仓位' : '个股买入意见'}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span className="tool-desc" style={{ margin: 0 }}>
                AI 模型：{result.model || 'unknown'}{result.created_at ? ` · ${result.created_at}` : ''}
              </span>
              <button className="btn btn-sm" onClick={exportCurrent}>导出 Markdown</button>
            </div>
          </div>

          {/* 候选标的 */}
          {result.candidates && result.candidates.length > 0 && (
            <div className="analysis-report" style={{ borderTop: 'none', paddingTop: 0 }}>
              <div className="analysis-report-title">📌 候选标的（{result.candidates.length}）</div>
              <table>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>股票</th>
                    <th>现价 / 涨跌幅</th>
                    <th>PE / PB</th>
                    <th>RPS120</th>
                    <th style={{ textAlign: 'left' }}>行业</th>
                  </tr>
                </thead>
                <tbody>
                  {result.candidates.map((c: any, i: number) => {
                    const live = c.live
                    const quote = c.quote || {}
                    const price = live?.price ?? quote.close ?? null
                    const pct = live?.pct_change ?? quote.pct_change ?? null
                    const pe = live?.pe_ttm ?? quote.pe_ttm ?? null
                    const pb = live?.pb_mrq ?? quote.pb_mrq ?? null
                    return (
                      <tr key={i}>
                        <td style={{ textAlign: 'left' }}>{c.name ? `${c.name}` : c.code}{c.name ? <span style={{ color: '#8a919f' }}> {c.code}</span> : ''}</td>
                        <td className={pctCls(pct)}>{fmtNum(price)} / {fmtNum(pct)}%</td>
                        <td>{fmtNum(pe)} / {fmtNum(pb)}</td>
                        <td>{c.rps?.rps120 != null ? fmtNum(c.rps.rps120, 1) : '-'}</td>
                        <td style={{ textAlign: 'left' }}>{c.industry ?? '-'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* 持仓诊断数据 */}
          {result.positions && result.positions.length > 0 && (
            <div className="analysis-report" style={{ borderTop: 'none', paddingTop: 0 }}>
              <div className="analysis-report-title">💼 持仓快照（结合最新盘面）</div>
              <table>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>股票</th>
                    <th>数量</th>
                    <th>成本价</th>
                    <th>现价</th>
                    <th>浮盈亏</th>
                  </tr>
                </thead>
                <tbody>
                  {result.positions.map((p: any, i: number) => (
                    <tr key={i}>
                      <td style={{ textAlign: 'left' }}>{p.name ? `${p.name}` : p.code}{p.name ? <span style={{ color: '#8a919f' }}> {p.code}</span> : ''}</td>
                      <td>{fmtNum(p.quantity, 0)}</td>
                      <td>{fmtNum(p.cost_price)}</td>
                      <td>{fmtNum(p.current_price)}</td>
                      <td className={pctCls(p.pnl_pct)}>{p.pnl_pct != null ? `${fmtNum(p.pnl_pct)}%` : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 账户与持仓概览 */}
          {result.portfolio_overview &&
            (result.portfolio_overview.principal != null ||
              result.portfolio_overview.available_cash != null ||
              (result.portfolio_overview.positions_value ?? 0) > 0) && (
              <div className="analysis-report" style={{ borderTop: 'none', paddingTop: 0 }}>
                <div className="analysis-report-title">💰 账户与持仓概览</div>
                <div className="cards" style={{ marginTop: 8 }}>
                  <div className="card">
                    <div className="label">本金</div>
                    <div className="value" style={{ fontSize: 16 }}>{fmtNum(result.portfolio_overview.principal)}</div>
                  </div>
                  <div className="card">
                    <div className="label">可用现金</div>
                    <div className="value" style={{ fontSize: 16 }}>{fmtNum(result.portfolio_overview.available_cash)}</div>
                  </div>
                  <div className="card">
                    <div className="label">持仓市值</div>
                    <div className="value" style={{ fontSize: 16 }}>{fmtNum(result.portfolio_overview.positions_value)}</div>
                  </div>
                  <div className="card">
                    <div className="label">总资产</div>
                    <div className="value" style={{ fontSize: 16 }}>{fmtNum(result.portfolio_overview.total_assets)}</div>
                  </div>
                  <div className="card">
                    <div className="label">仓位 / 现金比例</div>
                    <div className="value" style={{ fontSize: 16 }}>
                      {fmtNum(result.portfolio_overview.position_ratio_pct, 1)}% / {fmtNum(result.portfolio_overview.cash_ratio_pct, 1)}%
                    </div>
                  </div>
                  <div className="card">
                    <div className="label">相对本金盈亏</div>
                    <div className={`value ${pctCls(result.portfolio_overview.total_pnl)}`} style={{ fontSize: 16 }}>
                      {fmtNum(result.portfolio_overview.total_pnl)}（{fmtNum(result.portfolio_overview.total_pnl_pct)}%）
                    </div>
                  </div>
                </div>
              </div>
            )}

          {/* 报告 */}
          <div className="analysis-report">
            <div className="analysis-report-title">📄 操作建议</div>
            <Markdown content={result.report || ''} />
          </div>

          {/* 选股轨迹 */}
          {result.pick_trace && result.pick_trace.length > 0 && (
            <div className="analysis-report">
              <div className="analysis-report-title">🔍 选股工具调用轨迹（{result.pick_trace.length} 次）</div>
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>工具</th>
                    <th style={{ textAlign: 'left' }}>参数</th>
                  </tr>
                </thead>
                <tbody>
                  {result.pick_trace.map((t: any, i: number) => (
                    <tr key={i}>
                      <td>{i + 1}</td>
                      <td>{TOOL_LABEL[t.tool] ?? t.tool}</td>
                      <td style={{ textAlign: 'left', fontSize: 11 }}>{JSON.stringify(t.arguments)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* 历史建议 */}
      <div className="panel">
        <h2>🗂 历史建议（{history.length} 份）</h2>
        {history.length === 0 ? (
          <div className="tool-desc" style={{ margin: 0 }}>暂无历史建议记录。生成一次操作建议后会自动保存到这里。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ textAlign: 'left' }}>时间</th>
                <th style={{ textAlign: 'left' }}>策略</th>
                <th>模式</th>
                <th>候选 / 持仓</th>
                <th>模型</th>
                <th style={{ textAlign: 'left' }}>摘要</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.file}>
                  <td style={{ textAlign: 'left' }}>{h.created_at ?? '-'}</td>
                  <td style={{ textAlign: 'left' }}>{h.strategy_name ?? '-'}</td>
                  <td>{h.mode_label}</td>
                  <td>{h.n_candidates} / {h.n_positions}</td>
                  <td>{h.model ?? '-'}</td>
                  <td style={{ textAlign: 'left', fontSize: 12, color: '#5b6470', maxWidth: 360 }}>{h.preview || '-'}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span className="btn-group">
                      <button className="btn btn-sm" onClick={() => viewHistory(h.file)}>查看</button>
                      <a className="btn btn-sm btn-success" href={`/api/trading/advice/export?file=${encodeURIComponent(h.file)}`} download>导出</a>
                      <button className="btn btn-sm btn-danger" onClick={() => removeHistory(h.file)}>删除</button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
