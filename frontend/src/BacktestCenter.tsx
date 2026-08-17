import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import {
  fetchBacktestTasks,
  fetchBacktestResult,
  startBacktest,
  checkReadiness,
  generateReport,
  optimizeStrategy,
  createStrategy,
  fetchStrategies,
  fetchMarketKline,
  fetchStockKline,
  fetchStockNames,
  stopBacktestTask,
  deleteBacktestTask,
  type ReadinessResult,
} from './api'
import KlineChart from './KlineChart'
import Markdown from './Markdown'

const pct = (n: number | null | undefined, d = 2) => (n != null ? `${(n * 100).toFixed(d)}%` : '-')
const STATE_LABEL: Record<string, string> = { bull: '牛市', transition: '温和看多', range: '震荡', bear: '熊市' }
const STATE_COLOR: Record<string, string> = { bull: '#e03131', transition: '#f76707', range: '#f59f00', bear: '#12b886' }
const TOOL_LABEL: Record<string, string> = {
  get_market_regime: '市场环境判断',
  get_market_snapshot: '市场快照',
  get_market_sentiment: '市场情绪温度计',
  get_latest_trade_date: '最新交易日',
  rank_by_metric: '指标排名',
  screen_by_fundamentals: '基本面筛选',
  screen_fundamental_trend: '基本面趋势筛选',
  get_stock_moneyflow: '个股资金流',
  get_moneyflow_rank: '主力资金排名',
  analyze_price_volume: '量价分析',
  get_stock_daily: '日线查询',
  get_stock_list: '股票列表',
  place_order: '下单',
}

function ProgressBar({ progress }: { progress: any }) {
  if (!progress) return <div className="tool-desc">正在初始化…</div>
  const p = progress.total_days ? Math.round((progress.day_index / progress.total_days) * 100) : 0
  return (
    <div style={{ margin: '8px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#8a919f', flexWrap: 'wrap', gap: 4 }}>
        <span>
          进度 {progress.day_index}/{progress.total_days} 天（{p}%）
        </span>
        <span>
          回测到 {progress.date} · 市场 <b style={{ color: STATE_COLOR[progress.market_state] }}>{STATE_LABEL[progress.market_state] ?? progress.market_state}</b> · 持仓 {progress.positions} 只 · 已决策 {progress.decisions} 次 · 已成交 {progress.trades?.length ?? 0} 笔
        </span>
      </div>
      <div style={{ height: 8, background: '#f0f1f5', borderRadius: 4, marginTop: 6 }}>
        <div style={{ height: 8, width: `${p}%`, background: '#3370ff', borderRadius: 4, transition: 'width 0.3s' }} />
      </div>
    </div>
  )
}

function AnalysisTrace({ analysis, orders, reasoning, nameMap }: { analysis?: any[]; orders?: any[]; reasoning?: string[]; nameMap?: Record<string, string> }) {
  return (
    <div>
      {reasoning?.length ? (
        <div style={{ marginBottom: 8, padding: '8px 10px', background: '#f3f0ff', borderRadius: 6, border: '1px solid #e5dbff' }}>
          <div style={{ fontSize: 11, color: '#7048e8', fontWeight: 600, marginBottom: 6 }}>💭 交易思路（模型逐轮思考）</div>
          {reasoning.map((r: string, j: number) => (
            <details key={j} style={{ marginBottom: 4 }} open={j === reasoning.length - 1}>
              <summary style={{ cursor: 'pointer', fontSize: 11, color: '#5f3dc4' }}>
                第 {j + 1} 轮思考{String(r).length > 120 ? `（${String(r).length} 字）` : ''}
              </summary>
              <div style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.7, color: '#1f2329', padding: '4px 0' }}>
                {r}
              </div>
            </details>
          ))}
        </div>
      ) : null}
      {analysis?.length ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {analysis.map((a: any, j: number) => (
            <details key={j} style={{ background: '#fafbfc', borderRadius: 6, padding: '6px 10px' }}>
              <summary style={{ cursor: 'pointer', color: '#1f2329', fontSize: 12 }}>
                <b style={{ color: '#3370ff' }}>{j + 1}. {TOOL_LABEL[a.tool] ?? a.tool}</b>
                {a.summary ? <span>：{String(a.summary)}</span> : ''}
              </summary>
              <div style={{ padding: '6px 8px', marginTop: 6, background: '#fff', borderRadius: 4, border: '1px solid #f0f1f5' }}>
                <div style={{ fontSize: 11, color: '#8a919f', marginBottom: 4 }}>参数：{JSON.stringify(a.args)}</div>
                <div style={{ fontSize: 11, color: '#8a919f', marginBottom: 4 }}>完整结果：</div>
                <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 11, maxHeight: 280, overflow: 'auto', margin: 0 }}>
                  {JSON.stringify(a.result ?? a, null, 1)}
                </pre>
              </div>
            </details>
          ))}
        </div>
      ) : (
        <span style={{ color: '#8a919f' }}>（无过程记录）</span>
      )}
      {orders?.length ? (
        <div style={{ marginTop: 8, padding: '6px 10px', background: '#fff7e6', borderRadius: 6 }}>
          <div style={{ fontSize: 11, color: '#8a919f', marginBottom: 4 }}>本次下单：</div>
          {orders.map((o: any, j: number) => {
            const isBuy = o.action === 'buy'
            let detail = ''
            if (o.exec_price != null) {
              detail = ` @${Number(o.exec_price).toFixed(2)}`
              if (o.amount != null) detail += `（${Math.round(o.amount).toLocaleString()} 元）`
            } else if (o.rejected) {
              detail = `（${o.rejected}）`
            } else if (isBuy) {
              detail = `（${Math.round(o.cash_amount || 0).toLocaleString()} 元）`
            } else {
              detail = `（${o.ratio === 1 ? '清仓' : `卖出 ${Math.round((o.ratio || 0) * 100)}%`}）`
            }
            return (
              <span key={j} style={{ display: 'inline-block', marginRight: 12, fontWeight: 600, color: isBuy ? '#e03131' : '#12b886' }}>
                {isBuy ? '买入' : '卖出'} {o.code}{nameMap?.[o.code] ? ` ${nameMap[o.code]}` : ''}{detail}
              </span>
            )
          })}
        </div>
      ) : (
        <div style={{ marginTop: 8, padding: '6px 10px', background: '#f5f5f5', borderRadius: 6, color: '#8a919f', fontSize: 12 }}>
          本次决策：持有观望
        </div>
      )}
    </div>
  )
}

function CollapsibleSection({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ margin: '14px 0 6px' }}>
      <div
        onClick={() => setOpen((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setOpen((v) => !v)
          }
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          cursor: 'pointer',
          userSelect: 'none',
          fontSize: 14,
          fontWeight: 600,
          color: '#1f2329',
        }}
      >
        <span
          style={{
            display: 'inline-block',
            width: 10,
            transition: 'transform 0.15s',
            transform: open ? 'rotate(90deg)' : 'none',
            color: '#8a919f',
            fontSize: 12,
          }}
        >
          ▶
        </span>
        <span>{title}</span>
        <span style={{ fontSize: 12, color: '#8a919f', fontWeight: 400 }}>
          {open ? '收起' : '展开'}
        </span>
      </div>
      {open && <div style={{ marginTop: 8 }}>{children}</div>}
    </div>
  )
}

// 从成交记录聚合每只股票的交易与盈亏。
// finalPositions 提供期末持仓市值，用于计算「持仓中」股票的浮动盈亏与综合收益率
// （已实现 + 浮动，分母为总买入额）；缺省时持仓仅按已实现盈亏计，避免误判成全额亏损。
function aggregateStocks(trades?: any[], finalPositions?: any[]) {
  const map: Record<string, any> = {}
  for (const t of trades || []) {
    if (t.action !== 'buy' && t.action !== 'sell') continue
    if (!t.code) continue
    const s = map[t.code] || (map[t.code] = {
      code: t.code,
      buy_count: 0,
      sell_count: 0,
      buy_qty: 0,
      sell_qty: 0,
      buy_amount: 0,
      sell_amount: 0,
      realized_pnl: 0,
      sold_cost: 0,
      has_pnl: false,
      last_sell_date: '',
    })
    if (t.action === 'buy') {
      s.buy_count += 1
      s.buy_qty += t.quantity || 0
      s.buy_amount += t.amount || 0
    } else {
      s.sell_count += 1
      s.sell_qty += t.quantity || 0
      s.sell_amount += t.amount || 0
      if (t.pnl != null) {
        s.realized_pnl += t.pnl
        s.sold_cost += (t.amount || 0) - t.pnl
        s.has_pnl = true
      }
      if (t.date > s.last_sell_date) s.last_sell_date = t.date
    }
  }
  const fpMap: Record<string, any> = {}
  for (const p of finalPositions || []) fpMap[String(p.code)] = p

  return Object.values(map).map((s) => {
    const holding = s.buy_qty > s.sell_qty
    const realized = s.has_pnl ? s.realized_pnl : s.sell_qty > 0 ? s.sell_amount - s.buy_amount : 0
    const soldCost = s.has_pnl ? s.sold_cost : s.sell_qty > 0 ? s.buy_amount : 0
    let pnl = realized
    if (holding) {
      const remainingCost = s.buy_amount - soldCost
      const fp = fpMap[s.code]
      const marketValue = fp && fp.market_value != null ? fp.market_value : remainingCost
      pnl = realized + (marketValue - remainingCost)
    }
    return {
      ...s,
      pnl,
      return_pct: s.buy_amount > 0 ? (pnl / s.buy_amount) * 100 : 0,
      holding,
    }
  })
}

// 从成交记录聚合出每只股票的历史交易情况（已实现盈亏、清仓日等），供「历史持仓」展示
function summarizeTrades(trades?: any[], finalPositions?: any[]) {
  return aggregateStocks(trades, finalPositions).sort((a, b) =>
    String(b.last_sell_date).localeCompare(String(a.last_sell_date)),
  )
}

function LiveEquityChart({
  equity,
  cash,
  totalValue,
  initialCash,
  start,
  end,
}: {
  equity?: any[]
  cash?: number
  totalValue?: number
  initialCash?: number
  start?: string
  end?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)
  const [axisDates, setAxisDates] = useState<string[]>([])

  useEffect(() => {
    if (ref.current && !inst.current) inst.current = echarts.init(ref.current)
    return () => {
      inst.current?.dispose()
      inst.current = null
    }
  }, [])

  // 拉取整段回测区间的指数交易日，作为资金曲线固定不变的 x 轴，
  // 这样曲线会贴着上方 K 线的时间轴，回测推进时曲线从左向右生长，进度一目了然。
  useEffect(() => {
    if (!start || !end) return
    fetchMarketKline(start, end)
      .then((d) => setAxisDates((d.kline || []).map((k: any) => k.date)))
      .catch(() => setAxisDates([]))
  }, [start, end])

  useEffect(() => {
    const c = inst.current
    if (!c || !equity?.length) return
    // 有完整交易日列表时用固定全区间 x 轴；否则退化为按已有日期绘制
    const dates = axisDates.length ? axisDates : equity.map((e: any) => e.date)
    const idx: Record<string, number> = {}
    dates.forEach((d, i) => (idx[d] = i))
    // 未来尚未回测到的日期保持为空，让曲线只在已回测区间生长，而不是被拉伸铺满
    const data: (number | null)[] = new Array(dates.length).fill(null)
    for (const e of equity) {
      const i = idx[e.date]
      if (i !== undefined) data[i] = e.total
    }
    c.setOption({
      title: { text: '实时资金曲线（总资产）', left: 'center', textStyle: { fontSize: 13 } },
      tooltip: { trigger: 'axis' },
      grid: { left: 70, right: 20, top: 40, bottom: 30 },
      xAxis: { type: 'category', data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          type: 'line',
          name: '总资产',
          data,
          smooth: true,
          showSymbol: false,
          connectNulls: false,
          lineStyle: { color: '#3370ff' },
          areaStyle: { opacity: 0.08 },
        },
      ],
    })
  }, [equity, axisDates])
  const base = initialCash ?? equity?.[0]?.total
  const pnl = totalValue != null && base ? totalValue - base : null
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 12, color: '#8a919f', marginBottom: 6, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <span>当前现金 <b>{cash != null ? Math.round(cash).toLocaleString('zh-CN') : '-'}</b> 元</span>
        <span>
          当前总资产 <b>{totalValue != null ? Math.round(totalValue).toLocaleString('zh-CN') : '-'}</b> 元
        </span>
        <span>
          浮动盈亏{' '}
          <b style={{ color: (pnl ?? 0) >= 0 ? '#e03131' : '#12b886' }}>
            {pnl != null ? `${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString('zh-CN')} 元` : '-'}
          </b>
        </span>
      </div>
      <div className="chart" ref={ref} style={{ height: 240 }} />
    </div>
  )
}

function RunningKline({ start, end, trades, nameMap }: { start?: string; end?: string; trades?: any[]; nameMap?: Record<string, string> }) {
  const [kline, setKline] = useState<any[]>([])
  useEffect(() => {
    if (!start || !end) return
    fetchMarketKline(start, end)
      .then((d) => setKline(d.kline || []))
      .catch(console.error)
  }, [start, end])
  if (!kline.length) return <div className="tool-desc" style={{ marginTop: 8 }}>正在加载指数 K 线…</div>
  return <KlineChart kline={kline} trades={trades || []} nameMap={nameMap} />
}

function StockKlineChart({
  code,
  start,
  end,
  trades,
  nameMap,
}: {
  code: string
  start?: string
  end?: string
  trades?: any[]
  nameMap?: Record<string, string>
}) {
  const [kline, setKline] = useState<any[]>([])
  useEffect(() => {
    if (!code || !start || !end) return
    setKline([])
    fetchStockKline(code, start, end)
      .then((d) => setKline(d.kline || []))
      .catch(console.error)
  }, [code, start, end])
  const nm = nameMap?.[code] ? ` ${nameMap[code]}` : ''
  const title = `${code}${nm} K 线（▲买入 ▼卖出）`
  if (!kline.length) return <div className="tool-desc" style={{ marginTop: 8 }}>正在加载 {code}{nm} 日 K 线…</div>
  return <KlineChart kline={kline} trades={trades || []} nameMap={nameMap} title={title} />
}

export default function BacktestCenter({ initialStrategyId }: { initialStrategyId?: string }) {
  const [strategies, setStrategies] = useState<any[]>([])
  const [selStrategyId, setSelStrategyId] = useState('')
  const [start, setStart] = useState('2024-01-01')
  const [end, setEnd] = useState('2024-12-31')
  const [decideEvery, setDecideEvery] = useState(5)
  const [initialCash, setInitialCash] = useState(1000000)
  const [starting, setStarting] = useState(false)
  const [readiness, setReadiness] = useState<ReadinessResult | null>(null)
  const [readinessLoading, setReadinessLoading] = useState(false)

  const [tasks, setTasks] = useState<any[]>([])
  const [selTaskId, setSelTaskId] = useState('')
  const selTaskIdRef = useRef(selTaskId)
  const [detail, setDetail] = useState<any>(null)

  const [report, setReport] = useState<any>(null)
  const [reportLoading, setReportLoading] = useState(false)
  const [optResult, setOptResult] = useState<any>(null)
  const [optLoading, setOptLoading] = useState(false)
  const [optSaving, setOptSaving] = useState(false)
  const [kline, setKline] = useState<any[]>([])
  const [klineStock, setKlineStock] = useState<{ code: string } | null>(null)
  const [nameMap, setNameMap] = useState<Record<string, string>>({})

  const chartRef = useRef<HTMLDivElement>(null)
  const chartInst = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    fetchStrategies()
      .then((d) => {
        const list = d.strategies || []
        setStrategies(list)
        if (list.length && !selStrategyId) setSelStrategyId(list[0].id)
      })
      .catch(console.error)
    fetchStockNames()
      .then((d) => setNameMap(d.names || {}))
      .catch(console.error)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (initialStrategyId) {
      setSelStrategyId(initialStrategyId)
    }
  }, [initialStrategyId])

  useEffect(() => {
    selTaskIdRef.current = selTaskId
  }, [selTaskId])

  const loadTasks = () => {
    fetchBacktestTasks()
      .then((d) => {
        // 按发起时间（created_at）降序；无 created_at 的历史任务排在末尾（保持原有相对顺序）
        const list = (d.tasks || []).slice().sort((a, b) => {
          const ta = a.created_at || ''
          const tb = b.created_at || ''
          if (ta === tb) return 0
          return ta > tb ? -1 : 1
        })
        setTasks(list)
        // 仅当当前选中的任务不存在于列表时才自动选中（首次加载或任务被删除）
        // 列表已按发起时间降序，最新任务在首位
        if (list.length && !list.find((t) => t.task_id === selTaskIdRef.current)) {
          setSelTaskId(list[0].task_id)
        }
      })
      .catch(console.error)
  }

  useEffect(() => {
    loadTasks()
    const timer = setInterval(loadTasks, 4000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const t = tasks.find((x) => x.task_id === selTaskId)
    if (t?.result_file) {
      fetchBacktestResult(t.result_file).then(setDetail).catch(console.error)
    } else if (t) {
      setDetail(null)
    }
  }, [selTaskId, tasks])

  useEffect(() => {
    if (detail?.params?.start && detail?.params?.end) {
      fetchMarketKline(detail.params.start, detail.params.end)
        .then((d) => setKline(d.kline || []))
        .catch(console.error)
    }
  }, [detail])

  useEffect(() => {
    return () => {
      chartInst.current?.dispose()
      chartInst.current = null
    }
  }, [])

  useEffect(() => {
    const el = chartRef.current
    const curve = detail?.equity_curve
    // 容器未挂载或暂无数据：清理旧实例，避免它残留绑定到已卸载的 DOM
    if (!el || !curve?.length) {
      if (chartInst.current) {
        chartInst.current.dispose()
        chartInst.current = null
      }
      return
    }
    // 图表容器随「结果详情」条件渲染，切换任务（详情先置空再加载）时会被卸载重建；
    // 若实例仍绑定在旧 DOM 上，setOption 会画到已脱离文档的节点导致曲线空白。
    // 这里用 getDom() 比对，不一致就销毁重建实例。
    if (!chartInst.current || chartInst.current.getDom() !== el) {
      chartInst.current?.dispose()
      chartInst.current = echarts.init(el)
    }
    chartInst.current.setOption({
      title: { text: '策略净值曲线', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      grid: { left: 70, right: 20, top: 45, bottom: 40 },
      xAxis: { type: 'category', data: curve.map((e: any) => e.date) },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          type: 'line',
          name: '净值',
          data: curve.map((e: any) => e.total),
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#3370ff' },
          areaStyle: { opacity: 0.08 },
        },
      ],
    })
  }, [detail])

  const selStrategy = strategies.find((s) => s.id === selStrategyId)

  const isValidDate = (s: string) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false
    const [y, m, d] = s.split('-').map(Number)
    const dt = new Date(y, m - 1, d)
    return (
      !isNaN(dt.getTime()) &&
      dt.getFullYear() === y &&
      dt.getMonth() === m - 1 &&
      dt.getDate() === d
    )
  }

  const buildBacktestParams = () => ({
    strategy: selStrategy?.text ?? '',
    strategy_name: selStrategy?.name ?? '',
    start,
    end,
    decide_every: decideEvery,
    initial_cash: initialCash,
    config: selStrategy?.config,
  })

  // 第一步：点「启动回测」后，先做回测前就绪检查（大模型判断怎么执行 + 能力核对），
  // 弹出结果让用户看到判断过程，再由用户决定是否真正启动。
  const runBacktest = async () => {
    if (!selStrategy) {
      alert('请先选择要回测的策略')
      return
    }
    if (!isValidDate(start) || !isValidDate(end)) {
      alert('开始/结束日期不合法，请用 YYYY-MM-DD 格式的有效日期（如 2026-06-30，注意每月天数）')
      return
    }
    if (start >= end) {
      alert('开始日期必须早于结束日期')
      return
    }
    setReadinessLoading(true)
    setReadiness(null)
    try {
      const rep = await checkReadiness(buildBacktestParams())
      setReadiness(rep)
    } catch (e) {
      setReadiness({ ok: false, error: '就绪检查失败：' + String(e) })
    } finally {
      setReadinessLoading(false)
    }
  }

  // 第二步：用户在就绪检查弹窗里点「确认启动」（或缺口时点「强制启动」）
  const confirmRunBacktest = async (force = false) => {
    if (!selStrategy) return
    setStarting(true)
    try {
      const resp: any = await startBacktest({ ...buildBacktestParams(), force })
      if (resp?.task_id) {
        setReadiness(null)
        setSelTaskId(resp.task_id)
        loadTasks()
      } else {
        // 被门控拦下（正常情况下就绪检查已通过不会走到这里，防御性兜底）
        setReadiness(resp as ReadinessResult)
      }
    } catch (e) {
      alert('启动失败：' + String(e))
      setReadiness(null)
    } finally {
      setStarting(false)
    }
  }

  const runReport = async () => {
    const t = tasks.find((x) => x.task_id === selTaskId)
    if (!t?.result_file) return
    setReportLoading(true)
    setReport(null)
    try {
      setReport(await generateReport(t.result_file))
    } catch (e) {
      setReport({ error: String(e) })
    } finally {
      setReportLoading(false)
    }
  }

  const runOptimize = async () => {
    const t = tasks.find((x) => x.task_id === selTaskId)
    if (!t?.result_file) return
    setOptLoading(true)
    setOptResult(null)
    try {
      setOptResult(await optimizeStrategy(t.result_file, detail?.strategy))
    } catch (e) {
      setOptResult({ ok: false, error: String(e) })
    } finally {
      setOptLoading(false)
    }
  }

  const saveOptimized = async () => {
    if (!optResult?.strategy) return
    const baseName = detail?.strategy_name || selStrategy?.name || '策略'
    const name = `${baseName}·优化版`
    setOptSaving(true)
    try {
      await createStrategy(name, optResult.strategy, optResult.config ?? undefined)
      await loadTasks()
      const d = await fetchStrategies()
      setStrategies(d.strategies || [])
      alert(`已保存为「${name}」，可在策略中心查看并再次回测。`)
    } catch (e) {
      alert('保存失败：' + String(e))
    } finally {
      setOptSaving(false)
    }
  }

  const stopTask = async (taskId: string) => {
    if (!confirm('确定终止该回测任务？')) return
    await stopBacktestTask(taskId)
    loadTasks()
  }

  const delTask = async (taskId: string) => {
    if (!confirm('确定删除该回测任务？')) return
    await deleteBacktestTask(taskId)
    if (selTaskId === taskId) setSelTaskId('')
    setDetail(null)
    loadTasks()
  }

  const downloadMd = () => {
    if (!report?.markdown) return
    const blob = new Blob([report.markdown], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `回测报告_${selTaskId}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  const runningTasks = tasks.filter((t) => t.status === 'running')
  const selTask = tasks.find((t) => t.task_id === selTaskId)
  const m = detail?.metrics

  // 按个股聚合交易情况（已实现盈亏以卖出记录 pnl 为准，持仓中的浮动盈亏以期末市值计算）
  const stockSummary = useMemo(
    () => aggregateStocks(detail?.trades, detail?.final_positions).sort((a, b) => b.pnl - a.pnl),
    [detail],
  )

  // 月度收益
  const monthlyReturns = useMemo(() => {
    const map: Record<string, { start: number; end: number }> = {}
    for (const e of detail?.equity_curve || []) {
      const month = String(e.date).slice(0, 7)
      if (!map[month]) map[month] = { start: e.total, end: e.total }
      map[month].end = e.total
    }
    return Object.entries(map).map(([month, v]) => ({
      month,
      ret: v.start > 0 ? (v.end / v.start - 1) * 100 : 0,
    }))
  }, [detail])

  return (
    <div>
      {/* 选择策略 */}
      <div className="panel">
        <h2>选择回测策略</h2>
        <div className="query-bar">
          <select value={selStrategyId} onChange={(e) => setSelStrategyId(e.target.value)}>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          {strategies.length === 0 && <span className="tool-desc">暂无策略，请先在「策略中心」创建。</span>}
        </div>
        {selStrategy && (
          <pre className="tool-result" style={{ maxHeight: 140, marginTop: 8 }}>{selStrategy.text}</pre>
        )}
      </div>

      {/* 启动回测 */}
      <div className="panel">
        <h2>启动回测</h2>
        <div className="tool-desc" style={{ marginBottom: 10 }}>
          选股范围：<b>全市场约 5073 只 A 股</b>，AI 每个决策日通过工具自主筛选，<b>不设预定义股票池</b>。
        </div>
        <div className="query-bar" style={{ flexWrap: 'wrap' }}>
          <label className="field">
            <span>开始日期</span>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="field">
            <span>结束日期</span>
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <label className="field">
            <span>决策间隔(交易日)</span>
            <input type="number" value={decideEvery} onChange={(e) => setDecideEvery(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>初始本金(元)</span>
            <input type="number" value={initialCash} onChange={(e) => setInitialCash(Number(e.target.value))} />
          </label>
          <button className="btn" onClick={runBacktest} disabled={starting || readinessLoading || runningTasks.length > 0}>
            {readinessLoading ? '回测前检查中…' : starting ? '启动中…' : '启动回测'}
          </button>
        </div>
        <div className="param-hints">
          <div>· 点「启动回测」后，系统会<b>先做回测前就绪检查</b>：大模型判断该策略要怎么执行、列出所需能力并逐项核对工具是否具备，确认能严格执行后才会真正开跑。</div>
          <div>· <b>决策间隔</b>：每隔 N 个交易日，AI 决策一次（调仓/选股）。默认 5 = 约每周一次；设 1 = 每日决策（更贴近真实，但耗时更长）。<b>它不是股票池数量，也不是持仓数量</b>——无论设多少，AI 每次都在全市场选股。</div>
          <div>· <b>止损</b>：由策略文本自行定义（如"跌破买入价 8% 止损"），AI 在决策日据此自主判断是否止损，系统不做强制止损。</div>
          <div>· 决策日当天，AI 会判断市场状态、在全市场筛选个股、决定买卖与止损；非决策日仅做熊市避险检查。</div>
        </div>
        {runningTasks.length > 0 && (
          <div className="tool-desc">提示：回测期间可停留在本页，进度会每 4 秒自动刷新。</div>
        )}
      </div>

      {/* 运行中进度 */}
      {runningTasks.length > 0 && (
        <div className="panel" style={{ borderLeft: '3px solid #3370ff' }}>
          <h2>回测进行中</h2>
          {runningTasks.map((t) => (
            <div key={t.task_id} style={{ marginBottom: 16, paddingBottom: 12, borderBottom: '1px solid #f0f1f5' }}>
              <div style={{ fontSize: 13, marginBottom: 4 }}>
                任务 <b>{t.task_id}</b> · {t.params?.start} ~ {t.params?.end} · 决策间隔 {t.params?.decide_every} 天
              </div>
              <ProgressBar progress={t.progress} />
              {t.progress?.equity_curve && t.progress.equity_curve.length > 0 && (
                <>
                  <RunningKline start={t.params?.start} end={t.params?.end} trades={t.progress.trades || []} nameMap={nameMap} />
                  <LiveEquityChart
                    equity={t.progress.equity_curve}
                    cash={t.progress.cash}
                    totalValue={t.progress.total_value}
                    initialCash={t.params?.initial_cash}
                    start={t.params?.start}
                    end={t.params?.end}
                  />
                </>
              )}
              {t.progress?.positions_detail?.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>
                    实时持仓（{t.progress.positions_detail.length} 只）：
                  </div>
                  <table>
                    <thead>
                      <tr>
                        <th>股票</th>
                        <th>数量</th>
                        <th>成本价</th>
                        <th>现价</th>
                        <th>盈亏</th>
                        <th>市值</th>
                      </tr>
                    </thead>
                    <tbody>
                      {t.progress.positions_detail.map((p: any, j: number) => (
                        <tr key={j}>
                          <td>{p.code}{nameMap[p.code] ? ` ${nameMap[p.code]}` : ''}</td>
                          <td>{p.quantity ?? '-'}</td>
                          <td>{p.avg_cost != null ? Number(p.avg_cost).toFixed(2) : '-'}</td>
                          <td>{p.price != null ? Number(p.price).toFixed(2) : '-'}</td>
                          <td className={p.pnl_pct >= 0 ? 'up' : 'down'}>{p.pnl_pct != null ? `${p.pnl_pct.toFixed(2)}%` : '-'}</td>
                          <td>{p.market_value != null ? Math.round(p.market_value).toLocaleString('zh-CN') : '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {(() => {
                const closed = summarizeTrades(t.progress?.trades).filter((s: any) => s.buy_qty > 0 && !s.holding)
                if (!closed.length) return null
                return (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>
                      历史持仓（已清仓 {closed.length} 只）：
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th>股票</th>
                          <th>买入次数</th>
                          <th>卖出次数</th>
                          <th>已实现盈亏</th>
                          <th>收益率</th>
                          <th>清仓日期</th>
                        </tr>
                      </thead>
                      <tbody>
                        {closed.map((s: any, j: number) => (
                          <tr key={j}>
                            <td>{s.code}{nameMap[s.code] ? ` ${nameMap[s.code]}` : ''}</td>
                            <td>{s.buy_count}</td>
                            <td>{s.sell_count}</td>
                            <td className={s.pnl >= 0 ? 'up' : 'down'}>{Math.round(s.pnl).toLocaleString('zh-CN')}</td>
                            <td className={s.return_pct >= 0 ? 'up' : 'down'}>{s.return_pct.toFixed(2)}%</td>
                            <td>{s.last_sell_date || '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              })()}
              {t.progress?.decision_log && t.progress.decision_log.length > 0 && (
                <CollapsibleSection title={`历史决策过程（${t.progress.decision_log.length} 次，最新在前）`}>
                  {[...t.progress.decision_log].reverse().map((d: any, i: number) => (
                    <div key={i} style={{ marginBottom: 8, padding: '8px 10px', background: '#fafbfc', borderRadius: 6 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                        {d.date} · 市场{' '}
                        <span style={{ color: STATE_COLOR[d.market_state] }}>
                          {STATE_LABEL[d.market_state] ?? d.market_state}
                        </span>
                      </div>
                      <AnalysisTrace analysis={d.analysis} orders={d.orders} reasoning={d.reasoning} nameMap={nameMap} />
                    </div>
                  ))}
                </CollapsibleSection>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 任务列表 */}
      <div className="panel">
        <h2>回测任务（历史）</h2>
        {tasks.length === 0 ? (
          <div className="tool-desc">暂无回测任务。设置区间后点「启动回测」。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>策略</th>
                <th>区间</th>
                <th>状态</th>
                <th>年化</th>
                <th>总收益</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr
                  key={t.task_id}
                  onClick={() => setSelTaskId(t.task_id)}
                  style={{ cursor: 'pointer', background: t.task_id === selTaskId ? '#eef2ff' : undefined }}
                >
                  <td>{t.task_id}</td>
                  <td>{t.params?.strategy_name || '-'}</td>
                  <td>{t.params?.start} ~ {t.params?.end}</td>
                  <td>
                    <span className={t.status === 'running' ? 'up' : t.status === 'error' ? 'down' : ''}>
                      {t.status === 'running' ? '运行中' : t.status === 'done' ? '完成' : t.status === 'error' ? '失败' : t.status === 'stopped' ? '已终止' : t.status === 'interrupted' ? '已中断' : t.status}
                    </span>
                  </td>
                  <td>{t.metrics ? pct(t.metrics.annual_return) : '-'}</td>
                  <td>{t.metrics ? pct(t.metrics.total_return) : '-'}</td>
                  <td style={{ whiteSpace: 'nowrap' }} onClick={(e) => e.stopPropagation()}>
                    <span className="btn-group">
                      {t.status === 'running' && (
                        <button className="btn btn-danger" onClick={() => stopTask(t.task_id)}>终止</button>
                      )}
                      <button className="btn btn-danger" onClick={() => delTask(t.task_id)}>删除</button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 结果详情 */}
      {selTask && detail && (
        <div className="panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <h2>回测结果详情（{selTask.params?.start} ~ {selTask.params?.end}）</h2>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-success" onClick={runReport} disabled={reportLoading}>
                {reportLoading ? '生成中…' : '生成报告'}
              </button>
              <button className="btn btn-purple" onClick={runOptimize} disabled={optLoading}>
                {optLoading ? '优化中…' : '优化策略'}
              </button>
            </div>
          </div>

          {m && (
            <>
              <div className="cards">
                <div className="card">
                  <div className="label">总收益</div>
                  <div className="value" style={{ color: m.total_return >= 0 ? '#e03131' : '#12b886' }}>{pct(m.total_return)}</div>
                </div>
                <div className="card">
                  <div className="label">年化收益</div>
                  <div className="value" style={{ color: m.annual_return >= 0 ? '#e03131' : '#12b886' }}>{pct(m.annual_return)}</div>
                </div>
                <div className="card">
                  <div className="label">最大回撤</div>
                  <div className="value">{pct(m.max_drawdown)}</div>
                </div>
                <div className="card">
                  <div className="label">夏普比率</div>
                  <div className="value">{m.sharpe}</div>
                </div>
                <div className="card">
                  <div className="label">胜率</div>
                  <div className="value">{pct(m.win_rate)}</div>
                </div>
                <div className="card">
                  <div className="label">最终资产</div>
                  <div className="value" style={{ fontSize: 18 }}>{Math.round(m.final_value).toLocaleString('zh-CN')}</div>
                </div>
              </div>
              <KlineChart kline={kline} trades={detail.trades || []} nameMap={nameMap} />
              <h3 style={{ fontSize: 14, margin: '14px 0 6px' }}>策略净值曲线</h3>
              <div className="chart" ref={chartRef} style={{ height: 260 }} />

              {/* 月度收益 */}
              <CollapsibleSection title="月度收益">
                <table>
                  <thead>
                    <tr>
                      <th>月份</th>
                      <th>收益率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monthlyReturns.map((r) => (
                      <tr key={r.month}>
                        <td>{r.month}</td>
                        <td className={r.ret >= 0 ? 'up' : 'down'}>{r.ret.toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CollapsibleSection>
            </>
          )}

          {/* 决策过程 */}
          <CollapsibleSection title={`决策过程（${detail.decision_log?.length ?? 0} 次）`}>
            {detail.decision_log?.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>决策日</th>
                    <th>市场</th>
                    <th style={{ textAlign: 'left' }}>决策摘要</th>
                    <th style={{ textAlign: 'left' }}>完整决策过程（工具调用 + 下单）</th>
                  </tr>
                </thead>
                <tbody>
                  {[...detail.decision_log].reverse().map((d: any, i: number) => (
                    <tr key={i} style={{ verticalAlign: 'top' }}>
                      <td>{d.date}</td>
                      <td>
                        <span style={{ color: STATE_COLOR[d.market_state] }}>{STATE_LABEL[d.market_state] ?? d.market_state}</span>
                      </td>
                      <td style={{ textAlign: 'left', fontSize: 12, fontWeight: 500, whiteSpace: 'pre-wrap', lineHeight: 1.7, maxWidth: 340 }}>
                        {d.summary
                          ? String(d.summary).replace(/(\d{6})/g, (m: string) => `${m}${nameMap[m] ? ` ${nameMap[m]}` : ''}`)
                          : d.orders?.length
                            ? '有下单'
                            : '持有观望'}
                      </td>
                      <td style={{ textAlign: 'left', fontSize: 11 }}>
                        <AnalysisTrace analysis={d.analysis} orders={d.orders} reasoning={d.reasoning} nameMap={nameMap} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="tool-desc">旧版回测结果无决策日志（需重新运行回测生成）。</div>
            )}
          </CollapsibleSection>

          {/* 个股交易汇总 */}
          {stockSummary.length > 0 && (
            <CollapsibleSection title={`个股交易汇总（${stockSummary.length} 只）`}>
              <table>
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>买入次数</th>
                    <th>卖出次数</th>
                    <th>总买入额</th>
                    <th>总卖出额</th>
                    <th>净盈亏</th>
                    <th>收益率</th>
                    <th>状态</th>
                    <th>K线</th>
                  </tr>
                </thead>
                <tbody>
                  {stockSummary.map((s) => (
                    <tr key={s.code}>
                      <td>{s.code}{nameMap[s.code] ? ` ${nameMap[s.code]}` : ''}</td>
                      <td>{s.buy_count}</td>
                      <td>{s.sell_count}</td>
                      <td>{Math.round(s.buy_amount).toLocaleString('zh-CN')}</td>
                      <td>{Math.round(s.sell_amount).toLocaleString('zh-CN')}</td>
                      <td className={s.pnl >= 0 ? 'up' : 'down'}>{Math.round(s.pnl).toLocaleString('zh-CN')}</td>
                      <td className={s.return_pct >= 0 ? 'up' : 'down'}>{s.return_pct.toFixed(1)}%</td>
                      <td>{s.holding ? '持仓中' : '已清仓'}</td>
                      <td>
                        <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }} onClick={() => setKlineStock({ code: s.code })}>
                          查看
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CollapsibleSection>
          )}

          {/* 成交记录 */}
          <CollapsibleSection title={`成交记录（${detail.trades?.length ?? 0} 笔）`}>
            {detail.trades?.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>方向</th>
                    <th>代码</th>
                    <th>数量</th>
                    <th>价格</th>
                    <th>金额</th>
                    <th style={{ textAlign: 'left' }}>摘要</th>
                  </tr>
                </thead>
                <tbody>
                  {[...detail.trades].reverse().map((t: any, i: number) => (
                    <tr key={i}>
                      <td>{t.date}</td>
                      <td className={t.action === 'buy' ? 'up' : t.action === 'sell' ? 'down' : ''}>
                        {t.action === 'buy' ? '买入' : t.action === 'sell' ? '卖出' : t.action}
                      </td>
                      <td>{t.code || '-'}{t.code && nameMap[t.code] ? ` ${nameMap[t.code]}` : ''}</td>
                      <td>{t.quantity ?? '-'}</td>
                      <td>{t.price != null ? Number(t.price).toFixed(2) : '-'}</td>
                      <td>{t.amount != null ? Math.round(t.amount).toLocaleString('zh-CN') : '-'}</td>
                      <td style={{ textAlign: 'left', fontSize: 11 }}>
                        {t.summary
                          ? String(t.summary).replace(t.code, `${t.code}${nameMap[t.code] ? ` ${nameMap[t.code]}` : ''}`)
                          : `${t.action === 'buy' ? '买入' : '卖出'} ${t.code}${nameMap[t.code] ? ` ${nameMap[t.code]}` : ''}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="tool-desc">无成交。</div>
            )}
          </CollapsibleSection>
        </div>
      )}

      {/* 个股 K 线模态框 */}
      {klineStock && (
        <div className="modal-overlay" onClick={() => setKlineStock(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span>个股 K 线：{klineStock.code}{nameMap[klineStock.code] ? ` ${nameMap[klineStock.code]}` : ''}</span>
              <div>
                <button className="btn btn-ghost" onClick={() => setKlineStock(null)}>关闭</button>
              </div>
            </div>
            <div className="modal-body">
              <StockKlineChart
                code={klineStock.code}
                start={selTask?.params?.start}
                end={selTask?.params?.end}
                trades={(detail?.trades || []).filter((t: any) => t.code === klineStock.code)}
                nameMap={nameMap}
              />
            </div>
          </div>
        </div>
      )}

      {/* 报告模态框 */}
      {report && (
        <div className="modal-overlay" onClick={() => setReport(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span>回测结果报告</span>
              <div>
                <button className="btn" onClick={downloadMd}>下载 Markdown</button>
                <button className="btn btn-ghost" onClick={() => setReport(null)}>关闭</button>
              </div>
            </div>
            <div className="modal-body">
              {report.error ? (
                <div className="tool-desc" style={{ color: '#e03131' }}>{report.error}</div>
              ) : (
                <>
                  <div className="report-section">
                    <div className="label">策略内容</div>
                    {report.strategy_name && <div style={{ fontWeight: 600, marginBottom: 6 }}>{report.strategy_name}</div>}
                    {report.strategy ? (
                      <pre className="tool-result" style={{ whiteSpace: 'pre-wrap', maxHeight: 200 }}>{report.strategy}</pre>
                    ) : (
                      <div className="tool-desc">（旧版结果无策略内容）</div>
                    )}
                  </div>
                  <div className="report-section">
                    <div className="label">绩效指标</div>
                    <div>
                      总收益 {pct(report.metrics?.total_return)} · 年化 {pct(report.metrics?.annual_return)} · 最大回撤 {pct(report.metrics?.max_drawdown)} · 夏普 {report.metrics?.sharpe} · 胜率 {pct(report.metrics?.win_rate)}
                    </div>
                  </div>
                  <div className="report-section">
                    <div className="label">交易统计</div>
                    <div>
                      成交 {report.trade_stats?.total_records} 笔（买入 {report.trade_stats?.buys} / 卖出 {report.trade_stats?.sells}）· 交易标的 {report.trade_stats?.symbols_traded} 只
                    </div>
                  </div>
                  {report.trade_stats?.stock_summary?.length > 0 && (
                    <div className="report-section">
                      <div className="label">个股交易汇总（{report.trade_stats.stock_summary.length} 只）</div>
                      <table>
                        <thead>
                          <tr><th>股票</th><th>买</th><th>卖</th><th>净盈亏</th><th>收益率</th><th>状态</th></tr>
                        </thead>
                        <tbody>
                          {report.trade_stats.stock_summary.map((s: any, i: number) => (
                            <tr key={i}>
                              <td>{s.code}{(s.name || nameMap[s.code]) ? ` ${s.name || nameMap[s.code]}` : ''}</td>
                              <td>{s.buy_count}</td>
                              <td>{s.sell_count}</td>
                              <td className={s.pnl >= 0 ? 'up' : 'down'}>{Math.round(s.pnl).toLocaleString('zh-CN')}</td>
                              <td className={s.return_pct >= 0 ? 'up' : 'down'}>{s.return_pct}%</td>
                              <td>{s.holding ? '持仓中' : '已清仓'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {report.monthly_returns?.length > 0 && (
                    <div className="report-section">
                      <div className="label">月度收益</div>
                      <table>
                        <thead><tr><th>月份</th><th>收益率</th></tr></thead>
                        <tbody>
                          {report.monthly_returns.map((r: any, i: number) => (
                            <tr key={i}>
                              <td>{r.month}</td>
                              <td className={r.ret >= 0 ? 'up' : 'down'}>{r.ret}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {report.decision_stats?.decision_summaries?.length > 0 && (
                    <div className="report-section">
                      <div className="label">决策摘要（最近 5 次）</div>
                      {report.decision_stats.decision_summaries.slice(-5).map((d: any, i: number) => (
                        <div key={i} style={{ fontSize: 12, marginBottom: 6, lineHeight: 1.6 }}>
                          <b>{d.date}（{d.market_state}）</b>：{d.summary}
                        </div>
                      ))}
                    </div>
                  )}
                  {report.summary && (
                    <div className="report-section">
                      <div className="label">智能总结（LLM 生成）</div>
                      <pre className="tool-result" style={{ whiteSpace: 'pre-wrap' }}>{report.summary}</pre>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 策略优化模态框 */}
      {optResult && (
        <div className="modal-overlay" onClick={() => setOptResult(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span>策略优化（基于本次回测）</span>
              <div>
                <button className="btn btn-success" onClick={saveOptimized} disabled={optSaving || !optResult?.strategy}>
                  {optSaving ? '保存中…' : '保存为新策略'}
                </button>
                <button className="btn btn-ghost" onClick={() => setOptResult(null)}>关闭</button>
              </div>
            </div>
            <div className="modal-body">
              {optResult.error || optResult.ok === false ? (
                <div className="tool-desc" style={{ color: '#e03131' }}>{optResult.error || '优化失败'}</div>
              ) : (
                <>
                  {optResult.diagnosis && (
                    <div className="report-section">
                      <div className="label">诊断</div>
                      <pre className="tool-result" style={{ whiteSpace: 'pre-wrap' }}>{optResult.diagnosis}</pre>
                    </div>
                  )}
                  {optResult.changes?.length > 0 && (
                    <div className="report-section">
                      <div className="label">优化措施（{optResult.changes.length} 条）</div>
                      <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7 }}>
                        {optResult.changes.map((c: string, i: number) => (
                          <li key={i} style={{ fontSize: 12 }}>{c}</li>
                        ))}
                      </ol>
                    </div>
                  )}
                  {optResult.strategy && (
                    <div className="report-section">
                      <div className="label">优化后的策略文本</div>
                      <pre className="tool-result" style={{ whiteSpace: 'pre-wrap', maxHeight: 300 }}>{optResult.strategy}</pre>
                    </div>
                  )}
                  {optResult.config && Object.keys(optResult.config).length > 0 && (
                    <div className="report-section">
                      <div className="label">优化后的结构化配置</div>
                      <pre className="tool-result" style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(optResult.config, null, 2)}</pre>
                    </div>
                  )}
                  <div className="tool-desc" style={{ marginTop: 8 }}>
                    保存后将作为新策略出现在「策略中心」，可直接对其再次启动回测，形成「构建策略 → 回测 → 优化」的迭代闭环。
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 回测前就绪检查模态框 */}
      {readiness && (
        <div className="modal-overlay" onClick={() => { if (!readinessLoading) setReadiness(null) }}>
          <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span>回测前就绪检查</span>
              <div>
                <button className="btn btn-ghost" onClick={() => setReadiness(null)} disabled={readinessLoading}>关闭</button>
              </div>
            </div>
            <div className="modal-body">
              {readiness.ok === false && readiness.error ? (
                <div className="tool-desc" style={{ color: '#e03131' }}>{readiness.error}</div>
              ) : (
                <>
                  {(() => {
                    const ready = readiness.readiness?.ready ?? readiness.ready ?? false
                    const plan = readiness.readiness?.execution_plan
                    const reqs = readiness.readiness?.requirements || []
                    const gaps = readiness.readiness?.gaps || []
                    const remedies = readiness.readiness?.remedies || []
                    return (
                      <>
                        <div
                          style={{
                            padding: '12px 14px',
                            borderRadius: 8,
                            background: ready ? '#e6f7ef' : '#fff4e6',
                            border: `1px solid ${ready ? '#12b886' : '#f76707'}`,
                            marginBottom: 14,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 10,
                            fontSize: 15,
                            fontWeight: 600,
                          }}
                        >
                          <span style={{ fontSize: 20 }}>{ready ? '✅' : '⚠️'}</span>
                          <span style={{ color: ready ? '#0a8a6a' : '#e8590c' }}>
                            {ready ? '策略所需能力已全部具备，可以严格执行并启动回测' : '策略存在能力/数据缺口，建议先补齐（也可强制启动）'}
                          </span>
                        </div>

                        {plan && (
                          <div className="report-section">
                            <div className="label">大模型执行计划（这个策略打算怎么执行）</div>
                            <pre className="tool-result" style={{ whiteSpace: 'pre-wrap' }}>{plan}</pre>
                          </div>
                        )}

                        {reqs.length > 0 && (
                          <div className="report-section">
                            <div className="label">能力核对（{reqs.filter((r: any) => r.covered).length}/{reqs.length} 项具备）</div>
                            <table>
                              <thead>
                                <tr><th style={{ width: 40 }}>状态</th><th>能力</th><th style={{ textAlign: 'left' }}>策略对应要求</th></tr>
                              </thead>
                              <tbody>
                                {reqs.map((r: any, i: number) => (
                                  <tr key={i}>
                                    <td style={{ textAlign: 'center' }}>{r.covered ? '✅' : '❌'}</td>
                                    <td style={{ fontWeight: 500 }}>{r.capability}</td>
                                    <td style={{ textAlign: 'left', fontSize: 12, color: '#5a6169' }}>{r.why || '-'}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}

                        {remedies.length > 0 && (
                          <div className="report-section">
                            <div className="label">自动补救（先造工具）</div>
                            <ul className="md-list" style={{ margin: 0, paddingLeft: 20 }}>
                              {remedies.map((r: any, i: number) => (
                                <li key={i} style={{ fontSize: 12, lineHeight: 1.7 }}>
                                  {r.remedied ? '✅' : '⚠️'} <b>{r.capability}</b>：{r.detail}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {gaps.length > 0 && (
                          <div className="report-section">
                            <div className="label" style={{ color: '#e8590c' }}>仍缺（需造工具或补数据）</div>
                            <ul className="md-list" style={{ margin: 0, paddingLeft: 20 }}>
                              {gaps.map((g: any, i: number) => (
                                <li key={i} style={{ fontSize: 12, lineHeight: 1.7 }}>
                                  <b>{g.capability}</b>{g.why ? `：${g.why}` : ''}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {readiness.markdown && (
                          <div className="report-section">
                            <div className="label">检查报告</div>
                            <Markdown content={readiness.markdown} />
                          </div>
                        )}
                      </>
                    )
                  })()}
                </>
              )}
            </div>
            {readiness.ok !== false || !readiness.error ? (
              <div className="modal-foot" style={{ padding: '12px 16px', borderTop: '1px solid #f0f1f5', display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                <button className="btn btn-ghost" onClick={() => setReadiness(null)}>取消</button>
                {(readiness.readiness?.ready ?? readiness.ready ?? false) ? (
                  <button className="btn btn-success" onClick={() => confirmRunBacktest(false)} disabled={starting}>
                    {starting ? '启动中…' : '确认启动回测'}
                  </button>
                ) : (
                  <button className="btn btn-warning" onClick={() => confirmRunBacktest(true)} disabled={starting}>
                    {starting ? '启动中…' : '仍要强制启动（跳过门控）'}
                  </button>
                )}
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  )
}
