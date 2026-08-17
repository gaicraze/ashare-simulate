import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import {
  fetchBacktestResults,
  fetchBacktestResult,
  startBacktest,
  fetchBacktestStatus,
  generateReport,
} from './api'

const pct = (n: number | null | undefined, digits = 2) =>
  n != null ? `${(n * 100).toFixed(digits)}%` : '-'

export default function BacktestPanel({ strategy }: { strategy: string }) {
  const [results, setResults] = useState<any[]>([])
  const [selFile, setSelFile] = useState('')
  const [detail, setDetail] = useState<any>(null)
  const [start, setStart] = useState('2024-01-01')
  const [end, setEnd] = useState('2024-12-31')
  const [decideEvery, setDecideEvery] = useState(5)
  const [running, setRunning] = useState(false)
  const [taskStatus, setTaskStatus] = useState('')
  const [report, setReport] = useState<any>(null)
  const [reportLoading, setReportLoading] = useState(false)
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInst = useRef<echarts.ECharts | null>(null)

  const loadResults = () => {
    fetchBacktestResults()
      .then((d) => {
        const list = d.results || []
        setResults(list)
        if (list.length && !list.find((r) => r.file === selFile)) {
          setSelFile(list[list.length - 1].file)
        }
      })
      .catch(console.error)
  }

  useEffect(() => {
    loadResults()
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!selFile) return
    fetchBacktestResult(selFile).then(setDetail).catch(console.error)
  }, [selFile])

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
    if (!chart || !detail?.equity_curve?.length) return
    const curve = detail.equity_curve
    chart.setOption({
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

  const runBacktest = async () => {
    if (!strategy || !start || !end) return
    setRunning(true)
    setTaskStatus('启动中…')
    try {
      const { task_id } = await startBacktest({ strategy, start, end, decide_every: decideEvery })
      pollTimer.current = setInterval(async () => {
        const s = await fetchBacktestStatus(task_id)
        if (s.status === 'done') {
          if (pollTimer.current) clearInterval(pollTimer.current)
          setRunning(false)
          setTaskStatus('完成')
          loadResults()
        } else if (s.status === 'error') {
          if (pollTimer.current) clearInterval(pollTimer.current)
          setRunning(false)
          setTaskStatus('错误：' + s.error)
        } else {
          setTaskStatus('运行中…（后台执行，请稍候）')
        }
      }, 5000)
    } catch (e) {
      setRunning(false)
      setTaskStatus('启动失败：' + String(e))
    }
  }

  const m = detail?.metrics

  const runReport = async () => {
    if (!selFile) return
    setReportLoading(true)
    setReport(null)
    try {
      const r = await generateReport(selFile)
      setReport(r)
    } catch (e) {
      setReport({ error: String(e) })
    } finally {
      setReportLoading(false)
    }
  }

  const downloadMd = () => {
    if (!report?.markdown) return
    const blob = new Blob([report.markdown], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `回测报告_${selFile.replace('.json', '')}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <>
    <div className="panel">
      <h2>回测看板</h2>

      <div className="query-bar">
        <input value={start} onChange={(e) => setStart(e.target.value)} placeholder="开始 2024-01-01" style={{ width: 130 }} />
        <input value={end} onChange={(e) => setEnd(e.target.value)} placeholder="结束 2024-12-31" style={{ width: 130 }} />
        <input
          type="number"
          value={decideEvery}
          onChange={(e) => setDecideEvery(Number(e.target.value))}
          placeholder="决策间隔(天)"
          style={{ width: 110 }}
        />
        <button className="btn" onClick={runBacktest} disabled={running}>
          {running ? '回测中…' : '启动回测'}
        </button>
      </div>
      {taskStatus && <div className="tool-desc">{taskStatus}</div>}

      {results.length > 0 && (
        <div className="query-bar">
          <select value={selFile} onChange={(e) => setSelFile(e.target.value)}>
            {results.map((r) => (
              <option key={r.file} value={r.file}>
                {r.params?.start} ~ {r.params?.end}
              </option>
            ))}
          </select>
          <button className="btn btn-success" onClick={runReport} disabled={reportLoading}>
            {reportLoading ? '生成中…' : '生成报告'}
          </button>
        </div>
      )}

      {m && (
        <>
          <div className="cards">
            <div className="card">
              <div className="label">总收益</div>
              <div className="value" style={{ color: m.total_return >= 0 ? '#e03131' : '#12b886' }}>
                {pct(m.total_return)}
              </div>
            </div>
            <div className="card">
              <div className="label">年化收益</div>
              <div className="value" style={{ color: m.annual_return >= 0 ? '#e03131' : '#12b886' }}>
                {pct(m.annual_return)}
              </div>
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
              <div className="value" style={{ fontSize: 18 }}>
                {Math.round(m.final_value).toLocaleString('zh-CN')}
              </div>
            </div>
          </div>
          <div className="chart" ref={chartRef} style={{ height: 280 }} />
          {detail?.trades?.length > 0 && (
            <>
              <div className="tool-desc" style={{ marginTop: 12 }}>
                成交记录（{detail.trades.filter((t: any) => t.action === 'buy' || t.action === 'sell').length} 笔）
              </div>
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>方向</th>
                    <th>代码</th>
                    <th>数量</th>
                    <th>价格</th>
                    <th>金额</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.trades
                    .filter((t: any) => t.action === 'buy' || t.action === 'sell')
                    .slice(-30)
                    .reverse()
                    .map((t: any, i: number) => (
                      <tr key={i}>
                        <td>{t.date}</td>
                        <td className={t.action === 'buy' ? 'up' : 'down'}>
                          {t.action === 'buy' ? '买入' : '卖出'}
                        </td>
                        <td>{t.code}</td>
                        <td>{t.quantity}</td>
                        <td>{t.price != null ? Number(t.price).toFixed(2) : '-'}</td>
                        <td>{t.amount != null ? Math.round(t.amount).toLocaleString('zh-CN') : '-'}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </div>

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
                    <div className="label">回测参数</div>
                    <div>
                      {report.params?.start} ~ {report.params?.end} · 决策间隔 {report.params?.decide_every} 天 · 止损 {report.params?.stop_loss} · 初始资金 {Number(report.params?.initial_cash || 0).toLocaleString('zh-CN')}
                    </div>
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
                  <div className="report-section">
                    <div className="label">决策统计</div>
                    <div>
                      决策 {report.decision_stats?.total_decisions} 次 · 市场状态分布 {JSON.stringify(report.decision_stats?.market_states)}
                    </div>
                  </div>
                  {report.trade_stats?.pnl_by_symbol?.length > 0 && (
                    <div className="report-section">
                      <div className="label">各标的盈亏</div>
                      <table>
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th>买入额</th>
                            <th>卖出额</th>
                            <th>净盈亏</th>
                          </tr>
                        </thead>
                        <tbody>
                          {report.trade_stats.pnl_by_symbol.map((s: any, i: number) => (
                            <tr key={i}>
                              <td>{s.code}</td>
                              <td>{Math.round(s.buy_amount).toLocaleString('zh-CN')}</td>
                              <td>{Math.round(s.sell_amount).toLocaleString('zh-CN')}</td>
                              <td className={s.pnl >= 0 ? 'up' : 'down'}>
                                {Math.round(s.pnl).toLocaleString('zh-CN')}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
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
    </>
  )
}
