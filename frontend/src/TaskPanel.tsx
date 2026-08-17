import { useEffect, useRef, useState } from 'react'
import { fetchBacktestTasks, fetchBacktestResult } from './api'

const pct = (n: number | null | undefined, d = 2) => (n != null ? `${(n * 100).toFixed(d)}%` : '-')

const STATUS_TEXT: Record<string, string> = { running: '运行中', done: '完成', error: '失败' }

function ProgressBar({ progress }: { progress: any }) {
  if (!progress) return <div className="tool-desc">正在初始化…</div>
  const pctVal = progress.total_days ? Math.round((progress.day_index / progress.total_days) * 100) : 0
  return (
    <div style={{ margin: '8px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#8a919f' }}>
        <span>
          进度 {progress.day_index}/{progress.total_days} 天（{pctVal}%）
        </span>
        <span>
          当前 {progress.date} · 市场 {progress.market_state} · 持仓 {progress.positions} · 决策 {progress.decisions} 次 · 成交 {progress.trades} 笔
        </span>
      </div>
      <div style={{ height: 8, background: '#f0f1f5', borderRadius: 4, marginTop: 6 }}>
        <div
          style={{
            height: 8,
            width: `${pctVal}%`,
            background: '#3370ff',
            borderRadius: 4,
            transition: 'width 0.3s',
          }}
        />
      </div>
    </div>
  )
}

export default function TaskPanel() {
  const [tasks, setTasks] = useState<any[]>([])
  const [selTask, setSelTask] = useState<any>(null)
  const [detail, setDetail] = useState<any>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = () => {
    fetchBacktestTasks()
      .then((d) => setTasks(d.tasks || []))
      .catch(console.error)
  }

  useEffect(() => {
    load()
    timer.current = setInterval(load, 4000)
    return () => {
      if (timer.current) clearInterval(timer.current)
    }
  }, [])

  const selectTask = async (t: any) => {
    setSelTask(t)
    if (t.result_file) {
      const d = await fetchBacktestResult(t.result_file)
      setDetail(d)
    } else {
      setDetail(null)
    }
  }

  const running = tasks.filter((t) => t.status === 'running')

  return (
    <div>
      {running.length > 0 && (
        <div className="panel">
          <h2>执行中的回测任务</h2>
          {running.map((t) => (
            <div key={t.task_id} style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 13 }}>
                任务 <b>{t.task_id}</b> · {t.params?.start} ~ {t.params?.end} · 决策间隔 {t.params?.decide_every} 天
              </div>
              <ProgressBar progress={t.progress} />
            </div>
          ))}
        </div>
      )}

      <div className="panel">
        <h2>回测任务（历史 + 运行中）</h2>
        {tasks.length === 0 ? (
          <div className="tool-desc">暂无回测任务。可在「回测」页发起。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>区间</th>
                <th>状态</th>
                <th>年化</th>
                <th>总收益</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.task_id}>
                  <td>{t.task_id}</td>
                  <td>
                    {t.params?.start} ~ {t.params?.end}
                  </td>
                  <td>
                    <span className={t.status === 'running' ? 'up' : t.status === 'error' ? 'down' : ''}>
                      {STATUS_TEXT[t.status] ?? t.status}
                    </span>
                  </td>
                  <td>{t.metrics ? pct(t.metrics.annual_return) : '-'}</td>
                  <td>{t.metrics ? pct(t.metrics.total_return) : '-'}</td>
                  <td>
                    <button className="btn" onClick={() => selectTask(t)}>查看全流程</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selTask && detail && (
        <div className="panel">
          <h2>任务全流程：{selTask.task_id}</h2>
          <div className="tool-desc">
            {detail.params?.start} ~ {detail.params?.end} · 决策间隔 {detail.params?.decide_every} 天 · 成交 {detail.trades?.length ?? 0} 笔 · 决策 {detail.decision_log?.length ?? 0} 次
          </div>

          <h3 style={{ fontSize: 14, margin: '12px 0 6px' }}>决策过程（{detail.decision_log?.length ?? 0} 次）</h3>
          {detail.decision_log?.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>市场</th>
                  <th>决策前持仓</th>
                  <th>订单</th>
                  <th style={{ textAlign: 'left' }}>思路 / 摘要</th>
                </tr>
              </thead>
              <tbody>
                {[...detail.decision_log].reverse().map((d: any, i: number) => (
                  <tr key={i} style={{ verticalAlign: 'top' }}>
                    <td>{d.date}</td>
                    <td>{d.market_state}</td>
                    <td>{d.positions_before?.length ?? 0} 只</td>
                    <td style={{ textAlign: 'left', fontSize: 11 }}>
                      {d.orders?.length
                        ? d.orders
                            .map((o: any) => `${o.action === 'buy' ? '买' : '卖'}${o.code}`)
                            .join('；')
                        : '无（hold）'}
                    </td>
                    <td style={{ textAlign: 'left', fontSize: 11, maxWidth: 360 }}>
                      {d.reasoning?.length ? (
                        <details style={{ marginBottom: 6 }}>
                          <summary style={{ cursor: 'pointer', color: '#7048e8' }}>💭 {d.reasoning.length} 轮思考</summary>
                          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.6, maxHeight: 220, overflow: 'auto', marginTop: 4 }}>
                            {d.reasoning.map((r: string, j: number) => (
                              <p key={j} style={{ margin: '4px 0', borderLeft: '2px solid #e5dbff', paddingLeft: 6 }}>{r}</p>
                            ))}
                          </div>
                        </details>
                      ) : null}
                      <div style={{ whiteSpace: 'pre-wrap' }}>{d.summary || ''}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="tool-desc">旧版回测结果无决策日志（需重新运行回测生成）。</div>
          )}

          <h3 style={{ fontSize: 14, margin: '12px 0 6px' }}>成交记录（{detail.trades?.length ?? 0} 笔）</h3>
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
                </tr>
              </thead>
              <tbody>
                {[...detail.trades].reverse().map((t: any, i: number) => (
                  <tr key={i}>
                    <td>{t.date}</td>
                    <td className={t.action === 'buy' ? 'up' : t.action === 'sell' ? 'down' : ''}>
                      {t.action === 'buy' ? '买入' : t.action === 'sell' ? '卖出' : t.action}
                    </td>
                    <td>{t.code || '-'}</td>
                    <td>{t.quantity ?? '-'}</td>
                    <td>{t.price != null ? Number(t.price).toFixed(2) : '-'}</td>
                    <td>{t.amount != null ? Math.round(t.amount).toLocaleString('zh-CN') : '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="tool-desc">无成交。</div>
          )}
        </div>
      )}
    </div>
  )
}
