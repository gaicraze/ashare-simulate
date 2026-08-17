import { useState } from 'react'
import { agentDecide, type DecideResult } from './api'

export default function DecidePanel({ strategy }: { strategy: string }) {
  const [decideDate, setDecideDate] = useState('2026-07-16')
  const [decideResult, setDecideResult] = useState<DecideResult | null>(null)
  const [deciding, setDeciding] = useState(false)

  const runDecide = async () => {
    setDeciding(true)
    setDecideResult(null)
    try {
      const r = await agentDecide(strategy, decideDate || undefined, 'deepseek')
      setDecideResult(r)
    } catch (e) {
      setDecideResult({ ok: false, error: String(e) })
    } finally {
      setDeciding(false)
    }
  }

  return (
    <div className="panel">
      <h2>策略决策（LLM 驱动）</h2>
      <div className="query-bar">
        <input
          value={decideDate}
          onChange={(e) => setDecideDate(e.target.value)}
          placeholder="日期 YYYY-MM-DD"
          style={{ width: 130 }}
        />
        <button className="btn" onClick={runDecide} disabled={deciding}>
          {deciding ? '分析中…' : '执行决策'}
        </button>
      </div>
      <div className="tool-desc">当前策略（来自「策略管理」）：</div>
      <pre className="tool-result" style={{ whiteSpace: 'pre-wrap', maxHeight: 120 }}>
        {strategy}
      </pre>
      {decideResult?.error && (
        <div className="tool-desc" style={{ color: '#e03131' }}>
          错误：{decideResult.error}
        </div>
      )}
      {decideResult?.conclusion && (
        <pre className="tool-result" style={{ whiteSpace: 'pre-wrap' }}>
          {decideResult.conclusion}
        </pre>
      )}
      {decideResult?.trace && decideResult.trace.length > 0 && (
        <>
          <div className="tool-desc">工具调用轨迹（{decideResult.trace.length} 次）</div>
          <table>
            <thead>
              <tr>
                <th>轮次</th>
                <th>工具</th>
                <th>参数</th>
              </tr>
            </thead>
            <tbody>
              {decideResult.trace.map((t, i) => (
                <tr key={i}>
                  <td>{t.round}</td>
                  <td>{t.tool}</td>
                  <td style={{ textAlign: 'left', fontSize: 11 }}>{JSON.stringify(t.arguments)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
