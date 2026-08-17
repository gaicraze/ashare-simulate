import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { fetchDaily, searchStocks, type DailyRow, type StockInfo } from './api'

// 大数格式化：万/亿（用于成交量、成交额、流通市值）
const fmtBig = (n: number | null | undefined) => {
  if (n == null) return '-'
  const abs = Math.abs(n)
  if (abs >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (abs >= 1e4) return (n / 1e4).toFixed(2) + '万'
  return n.toLocaleString('zh-CN')
}

const num2 = (n: number | null | undefined) => (n != null ? n.toFixed(2) : '-')

// 完整字段列定义：覆盖 daily 表全部数据项（含换手率/流通市值/PE/PB/复权因子）
interface ColDef {
  key: string
  label: string
  render: (r: DailyRow) => string
  cls?: (r: DailyRow) => string | undefined
}

const COLUMNS: ColDef[] = [
  { key: 'trade_date', label: '日期', render: (r) => r.trade_date },
  { key: 'open', label: '开盘', render: (r) => num2(r.open) },
  { key: 'high', label: '最高', render: (r) => num2(r.high) },
  { key: 'low', label: '最低', render: (r) => num2(r.low) },
  { key: 'close', label: '收盘', render: (r) => num2(r.close) },
  {
    key: 'pct_change',
    label: '涨跌幅%',
    render: (r) => (r.pct_change != null ? r.pct_change.toFixed(2) : '-'),
    cls: (r) => (r.pct_change != null && r.pct_change >= 0 ? 'up' : 'down'),
  },
  { key: 'volume', label: '成交量(股)', render: (r) => fmtBig(r.volume) },
  { key: 'amount', label: '成交额(元)', render: (r) => fmtBig(r.amount) },
  { key: 'turnover', label: '换手率%', render: (r) => num2(r.turnover) },
  { key: 'float_mktcap', label: '流通市值(元)', render: (r) => fmtBig(r.float_mktcap) },
  { key: 'pe_ttm', label: 'PE(TTM)', render: (r) => num2(r.pe_ttm) },
  { key: 'pb_mrq', label: 'PB', render: (r) => num2(r.pb_mrq) },
  { key: 'adj_factor', label: '复权因子', render: (r) => num2(r.adj_factor) },
]

export default function QuotesPanel() {
  const [input, setInput] = useState('')
  const [code, setCode] = useState<string | null>(null)
  const [name, setName] = useState<string | null>(null)
  const [rows, setRows] = useState<DailyRow[]>([])
  const [matches, setMatches] = useState<StockInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInst = useRef<echarts.ECharts | null>(null)

  // 初始化图表实例（不加载任何数据，等用户确认查询后再加载）
  useEffect(() => {
    if (chartRef.current && !chartInst.current) {
      chartInst.current = echarts.init(chartRef.current)
    }
    return () => {
      chartInst.current?.dispose()
      chartInst.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartInst.current
    if (!chart || rows.length === 0) return
    const reversed = [...rows].reverse()
    const title = name ? `${code} ${name}` : (code ?? '')
    chart.setOption({
      title: { text: `${title} 近 ${rows.length} 日收盘`, left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      grid: { left: 60, right: 20, top: 45, bottom: 35 },
      xAxis: { type: 'category', data: reversed.map((r) => r.trade_date) },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          type: 'line',
          data: reversed.map((r) => r.close),
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#3370ff' },
          areaStyle: { opacity: 0.08 },
        },
      ],
    })
  }, [rows, code, name])

  const selectStock = async (s: StockInfo) => {
    setMatches([])
    setCode(s.code)
    setName(s.name)
    setInput(s.code)
    setError(null)
    setLoading(true)
    try {
      const d = await fetchDaily(s.code, 60)
      setRows(d.rows || [])
    } catch (e) {
      setError(String(e))
      setRows([])
    } finally {
      setLoading(false)
    }
  }

  const doQuery = async () => {
    const q = input.trim()
    if (!q) {
      setError('请输入股票代码或名称')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await searchStocks(q)
      const list = res.rows || []
      if (list.length === 0) {
        setError('未找到匹配的股票，请检查代码或名称')
        setMatches([])
        setCode(null)
        setName(null)
        setRows([])
        return
      }
      if (list.length === 1) {
        await selectStock(list[0])
      } else {
        setMatches(list)
        setCode(null)
        setName(null)
        setRows([])
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="panel">
      <h2>行情查询</h2>
      <div className="query-bar">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && doQuery()}
          placeholder="输入股票代码或名称，如 600519 / 贵州茅台"
        />
        <button className="btn" onClick={doQuery} disabled={loading}>
          {loading ? '查询中…' : '查询'}
        </button>
      </div>

      {error && <div className="tool-desc" style={{ color: '#c33', margin: '8px 0' }}>{error}</div>}

      {matches.length > 0 && (
        <div className="tool-desc" style={{ margin: '8px 0' }}>
          找到 {matches.length} 只匹配股票，请选择：
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 6 }}>
            {matches.map((s) => (
              <button className="btn btn-sm" key={s.code} onClick={() => selectStock(s)}>
                {s.code} {s.name ?? ''}
                {s.industry ? `（${s.industry}）` : ''}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="chart-wrap">
        <div className="chart" ref={chartRef} />
        {rows.length === 0 && !loading && (
          <div className="chart-hint">输入股票代码或名称并点击「查询」，即可加载近 60 日行情</div>
        )}
      </div>

      {rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ minWidth: 980 }}>
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.trade_date}>
                  {COLUMNS.map((c) => (
                    <td key={c.key} className={c.cls?.(r)}>
                      {c.render(r)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
