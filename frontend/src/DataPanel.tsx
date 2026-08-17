import { useCallback, useEffect, useState } from 'react'
import {
  fetchSummary,
  fetchDataUpdateStatus,
  triggerBackfill,
  fetchBackfillStatus,
  triggerMetaBackfill,
  updateData,
  type Summary,
  type DataUpdateStatus,
} from './api'

const LABELS: Record<string, string> = {
  stocks: '股票列表',
  daily: '日线行情',
  indices: '指数',
  finances: '财务数据',
  moneyflow: '资金流',
  sectors: '板块',
}

export default function DataPanel() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [status, setStatus] = useState<DataUpdateStatus | null>(null)
  const [backfill, setBackfill] = useState<any>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string>('')

  const refresh = useCallback(() => {
    fetchSummary().then(setSummary).catch(console.error)
    fetchDataUpdateStatus().then(setStatus).catch(console.error)
    fetchBackfillStatus().then(setBackfill).catch(console.error)
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  const doIncremental = async () => {
    setBusy('增量更新中…')
    setMsg('')
    try {
      const r = await updateData()
      setMsg(r.ok ? `增量更新完成：写入 ${r.inserted} 条，最新交易日 ${r.latest_trade_date}` : `失败：${r.error}`)
    } finally {
      setBusy(null)
      refresh()
    }
  }

  const doBackfill = async () => {
    setBusy('回填任务已启动（后台运行）…')
    setMsg('')
    try {
      const r = await triggerBackfill(false)
      setMsg(r.ok ? '历史回填已在后台启动，下方进度条会实时更新' : `启动失败：${r.error}`)
    } finally {
      setBusy(null)
      refresh()
    }
  }

  const doMeta = async () => {
    setBusy('元数据回填中…')
    setMsg('')
    try {
      const r = await triggerMetaBackfill()
      setMsg(r.ok ? `元数据回填完成：名称 ${r.name_updated} / 行业 ${r.industry_updated} / 上市日 ${r.list_date_updated} / 板块 ${r.sector_rows}` : `失败：${r.error}`)
    } finally {
      setBusy(null)
      refresh()
    }
  }

  const bf = backfill?.progress
  const bfDone = bf?.total ? bf.total - (bf.remaining ?? 0) : 0
  const bfPct = bf?.total ? Math.round((bfDone / bf.total) * 100) : 0

  return (
    <div className="panel">
      <h2>数据管理</h2>

      <div className="tool-desc" style={{ marginBottom: 10 }}>
        <span className="btn-group">
          <button className="btn" onClick={doIncremental} disabled={busy !== null}>
            增量更新当日行情
          </button>
          <button className="btn" onClick={doBackfill} disabled={busy !== null}>
            历史回填（成交额/复权因子/换手/流通市值）
          </button>
          <button className="btn" onClick={doMeta} disabled={busy !== null}>
            回填元数据（名称/行业/上市日/板块）
          </button>
        </span>
        {busy && <span style={{ marginLeft: 8 }}>{busy}</span>}
        {msg && <div style={{ marginTop: 8, color: '#2a7' }}>{msg}</div>}
      </div>

      {status && (
        <div className="tool-desc" style={{ marginBottom: 10 }}>
          自动更新：{status.auto_update ? '已开启' : '已关闭'}（每交易日收盘后自动增量）
          {status.scheduler.last_run_ts && <> · 最近调度 {status.scheduler.last_run_ts.replace('T', ' ')}</>}
          {status.last_update?.ts && <> · 最近更新 {status.last_update.ts.replace('T', ' ')}</>}
          <br />
          新鲜度：最新交易日 <b>{status.freshness.latest_trade_date ?? '—'}</b>，当日覆盖{' '}
          {status.freshness.stocks_on_latest_day}/{status.freshness.stocks_total} 只
          {status.freshness.stale && <span style={{ color: '#c33' }}>（数据滞后，请点“增量更新”）</span>}
          {status.source && (
            <>
              {' '}· 数据源：<b>{status.source.active === 'akshare' ? 'akshare（东财/新浪）' : '腾讯/新浪直连'}</b>
              {status.source.akshare_enabled && status.source.active !== 'akshare' && <>（akshare 已装未启用，设 DATA_SOURCE=akshare 切换）</>}
            </>
          )}
        </div>
      )}

      {backfill?.running && bf && (
        <div className="tool-desc" style={{ marginBottom: 10 }}>
          历史回填进度：{bfDone}/{bf.total}（{bfPct}%）已更新 {bf.rows_updated} 行
          <div style={{ background: '#eee', height: 6, borderRadius: 3, marginTop: 4 }}>
            <div style={{ width: `${bfPct}%`, background: '#3a8', height: 6, borderRadius: 3 }} />
          </div>
        </div>
      )}

      {summary && (
        <>
          <table>
            <thead>
              <tr>
                <th>数据集</th>
                <th>行数</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(summary.tables).map(([k, v]) => (
                <tr key={k}>
                  <td>{LABELS[k] ?? k}</td>
                  <td>{v.toLocaleString('zh-CN')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="tool-desc" style={{ marginTop: 8 }}>
            日线覆盖：{summary.daily.min_date} ~ {summary.daily.max_date}（{summary.daily.distinct_codes} 只股票）
            <br />
            数据源：HuggingFace traderharness-ashare-5y（本地 DuckDB + Parquet）
            <br />
            增量更新：腾讯行情快照（每交易日收盘后自动，亦可手动触发）；历史回填：腾讯后复权 K 线
          </div>
        </>
      )}
    </div>
  )
}
