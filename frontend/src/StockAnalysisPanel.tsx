import { useCallback, useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import {
  analyzeStock,
  fetchAnalysisHistory,
  fetchAnalysisResult,
  deleteAnalysis,
  type StockAnalysisResult,
  type AnalysisHistoryItem,
} from './api'
import Markdown from './Markdown'

const fmtNum = (n: number | null | undefined, digits = 2) =>
  n != null ? Number(n).toFixed(digits) : '-'

const pctCls = (n: number | null | undefined) =>
  n != null && n < 0 ? 'down' : 'up'

function maOf(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i]
    if (i >= n) sum -= closes[i - n]
    out.push(i >= n - 1 ? Number((sum / n).toFixed(2)) : null)
  }
  return out
}

function downloadText(filename: string, text: string, mime = 'text/markdown;charset=utf-8') {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export default function StockAnalysisPanel() {
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<StockAnalysisResult | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [history, setHistory] = useState<AnalysisHistoryItem[]>([])
  const chartInst = useRef<echarts.ECharts | null>(null)
  const timerRef = useRef<number | null>(null)

  // 图表容器是随分析结果条件渲染的，因此用回调 ref 在挂载/卸载时动态初始化/销毁实例，
  // 避免「首次挂载时容器尚不存在 → chartInst 始终为空 → 走势图不显示」的问题。
  const attachChart = useCallback((node: HTMLDivElement | null) => {
    if (node) {
      if (!chartInst.current || chartInst.current.getDom() !== node) {
        chartInst.current?.dispose()
        chartInst.current = echarts.init(node)
      }
    } else {
      chartInst.current?.dispose()
      chartInst.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      chartInst.current?.dispose()
      chartInst.current = null
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [])

  const loadHistory = useCallback(() => {
    fetchAnalysisHistory()
      .then((d) => setHistory(d.items || []))
      .catch(() => setHistory([]))
  }, [])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  const run = async () => {
    const q = input.trim()
    if (!q) {
      setError('请输入股票代码或名称')
      return
    }
    setLoading(true)
    setError(null)
    setResult(null)
    setElapsed(0)
    const t0 = Date.now()
    timerRef.current = window.setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000)
    try {
      const r = await analyzeStock(q)
      if (!r.ok) {
        setError(r.error || '分析失败')
        setResult(null)
      } else {
        setResult(r)
        loadHistory()
      }
    } catch (e) {
      setError(String(e))
      setResult(null)
    } finally {
      setLoading(false)
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }

  const viewHistory = async (file: string) => {
    setError(null)
    try {
      const r = await fetchAnalysisResult(file)
      if (r.ok) {
        setResult(r)
      } else {
        setError(r.error || '加载历史分析失败')
      }
    } catch (e) {
      setError(String(e))
    }
  }

  const removeHistory = async (file: string) => {
    if (!confirm('确定删除该历史分析？')) return
    try {
      await deleteAnalysis(file)
      loadHistory()
      if (result?.file === file) setResult(null)
    } catch (e) {
      setError(String(e))
    }
  }

  const exportCurrent = () => {
    const md = result?.markdown
    if (md) {
      const code = result?.code || result?.data?.stock?.code || 'stock'
      const name = result?.name || result?.data?.stock?.name || ''
      downloadText(`个股深度分析_${code}${name ? '_' + name : ''}.md`, md)
    } else if (result?.file) {
      window.location.href = `/api/analysis/export?file=${encodeURIComponent(result.file)}`
    }
  }

  // 绘制收盘价 + MA20 + MA60 走势
  useEffect(() => {
    const chart = chartInst.current
    const series = result?.data?.series
    if (!chart || !series || series.length === 0) return
    const dates = series.map((s) => s.trade_date)
    const closes = series.map((s) => s.close ?? 0)
    const ma20 = maOf(closes, 20)
    const ma60 = maOf(closes, 60)
    chart.setOption({
      title: { text: `${result?.data?.stock.name ?? ''} 近 ${series.length} 日走势`, left: 'center', textStyle: { fontSize: 13 } },
      tooltip: { trigger: 'axis' },
      legend: { data: ['收盘', 'MA20', 'MA60'], top: 24, textStyle: { fontSize: 11 } },
      grid: { left: 55, right: 20, top: 55, bottom: 30 },
      xAxis: { type: 'category', data: dates, boundaryGap: false },
      yAxis: { type: 'value', scale: true },
      series: [
        { name: '收盘', type: 'line', data: closes, showSymbol: false, lineStyle: { color: '#3370ff', width: 1.5 } },
        { name: 'MA20', type: 'line', data: ma20, showSymbol: false, lineStyle: { color: '#f0a020', width: 1 } },
        { name: 'MA60', type: 'line', data: ma60, showSymbol: false, lineStyle: { color: '#7048e8', width: 1 } },
      ],
    })
  }, [result])

  const d = result?.data
  const stock = d?.stock
  const quote = d?.quote || {}
  const tech = d?.technical || {}
  const fin = d?.fundamentals?.[0] || {}
  const mf = d?.moneyflow || {}
  const market = d?.market || {}
  const rps = d?.rps || {}

  return (
    <div className="panel">
      <h2>🔬 个股深度分析</h2>
      <div className="tool-desc">
        输入股票代码或名称，系统从本地数据湖采集行情、财务、资金流、估值、技术指标等多维数据，交给大模型生成深度研究报告（基本面 / 技术面 / 资金面 / 估值 / 风险 / 综合研判）。分析结果会自动保存，可在下方「历史分析」中查阅与导出。
      </div>
      <div className="query-bar">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !loading && run()}
          placeholder="输入股票代码或名称，如 600519 / 贵州茅台 / 300750"
          style={{ width: 320 }}
        />
        <button className="btn btn-purple" onClick={run} disabled={loading}>
          {loading ? `分析中… ${elapsed}s` : '开始分析'}
        </button>
      </div>

      {error && <div className="tool-desc" style={{ color: '#c33' }}>{error}</div>}
      {loading && (
        <div className="tool-desc" style={{ color: '#3370ff' }}>
          正在采集数据并调用大模型分析，通常需要 1~3 分钟，请稍候…
        </div>
      )}

      {result && result.ok && d && (
        <div className="analysis-result">
          {/* 标题行 */}
          <div className="analysis-head">
            <div>
              <span className="analysis-name">{stock?.name ?? '-'}</span>
              <span className="analysis-code">{stock?.code}</span>
              {stock?.industry && <span className="analysis-tag">{stock.industry}</span>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span className="tool-desc" style={{ margin: 0 }}>
                AI 模型：{result.model || 'unknown'}
                {result.created_at ? ` · ${result.created_at}` : ''}
              </span>
              <button className="btn btn-sm" onClick={exportCurrent}>导出 Markdown</button>
            </div>
          </div>

          {/* 走势图 */}
          <div className="chart analysis-chart" ref={attachChart} />

          {/* 关键指标 */}
          <div className="cards" style={{ marginTop: 12 }}>
            <div className="card">
              <div className="label">最新价</div>
              <div className="value">{fmtNum(quote.close)}</div>
            </div>
            <div className="card">
              <div className="label">涨跌幅</div>
              <div className={`value ${pctCls(quote.pct_change)}`}>{fmtNum(quote.pct_change)}%</div>
            </div>
            <div className="card">
              <div className="label">PE(TTM) / PB</div>
              <div className="value" style={{ fontSize: 16 }}>{fmtNum(quote.pe_ttm)} / {fmtNum(quote.pb_mrq)}</div>
            </div>
            <div className="card">
              <div className="label">ROE（最新财报）</div>
              <div className="value">{fmtNum(fin.roe_pct)}%</div>
            </div>
            <div className="card">
              <div className="label">净利同比</div>
              <div className={`value ${pctCls(fin.yoy_net_profit_pct)}`}>{fmtNum(fin.yoy_net_profit_pct)}%</div>
            </div>
            <div className="card">
              <div className="label">近20日主力净流入</div>
              <div className={`value ${pctCls(mf.main_net_inflow_sum_yi)}`}>
                {mf.has_data ? fmtNum(mf.main_net_inflow_sum_yi) + '亿' : '暂无'}
              </div>
            </div>
            <div className="card">
              <div className="label">RPS120 强度</div>
              <div className="value">{rps.rps120 != null ? fmtNum(rps.rps120, 1) : '-'}</div>
            </div>
            <div className="card">
              <div className="label">距52周高点</div>
              <div className={`value ${pctCls(tech.distance_from_high_pct)}`}>{fmtNum(tech.distance_from_high_pct)}%</div>
            </div>
            <div className="card">
              <div className="label">大盘环境</div>
              <div className="value" style={{ fontSize: 16 }}>{market.regime ?? '-'}</div>
            </div>
          </div>

          {d.notes && d.notes.length > 0 && (
            <div className="tool-desc" style={{ marginTop: 8, color: '#b8860b' }}>
              数据提示：{d.notes.join('；')}
            </div>
          )}

          {/* 深度研究报告 */}
          <div className="analysis-report">
            <div className="analysis-report-title">📄 深度研究报告</div>
            <Markdown content={result.report || ''} />
          </div>
        </div>
      )}

      {/* 历史分析 */}
      <div className="analysis-report" style={{ marginTop: 20 }}>
        <div className="analysis-report-title">🗂 历史分析（{history.length} 份）</div>
        {history.length === 0 ? (
          <div className="tool-desc" style={{ margin: 0 }}>
            暂无历史分析记录。完成一次分析后会自动保存到这里。
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ textAlign: 'left' }}>股票</th>
                <th>分析时间</th>
                <th>模型</th>
                <th style={{ textAlign: 'left' }}>报告摘要</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.file}>
                  <td style={{ textAlign: 'left' }}>{h.name ? `${h.name}` : h.code}{h.name ? <span style={{ color: '#8a919f' }}> {h.code}</span> : ''}</td>
                  <td>{h.created_at ?? '-'}</td>
                  <td>{h.model ?? '-'}</td>
                  <td style={{ textAlign: 'left', fontSize: 12, color: '#5b6470', maxWidth: 420 }}>{h.preview || '-'}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span className="btn-group">
                      <button className="btn btn-sm" onClick={() => viewHistory(h.file)}>查看</button>
                      <a className="btn btn-sm btn-success" href={`/api/analysis/export?file=${encodeURIComponent(h.file)}`} download>
                        导出
                      </a>
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
