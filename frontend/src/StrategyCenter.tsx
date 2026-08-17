import { useEffect, useState } from 'react'
import {
  fetchStrategies,
  createStrategy,
  updateStrategy,
  deleteStrategy,
  generateStrategy,
  agentDecide,
  type Strategy,
  type DecideResult,
} from './api'

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

export default function StrategyCenter({ onBacktest }: { onBacktest: (strategyId: string) => void }) {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selId, setSelId] = useState('')
  const [name, setName] = useState('')
  const [text, setText] = useState('')
  const [configText, setConfigText] = useState('')

  const [idea, setIdea] = useState('')
  const [generating, setGenerating] = useState(false)

  const [decideDate, setDecideDate] = useState('2026-07-16')
  const [decideResult, setDecideResult] = useState<DecideResult | null>(null)
  const [deciding, setDeciding] = useState(false)

  const load = (keepSel = true) => {
    fetchStrategies()
      .then((d) => {
        const list = d.strategies || []
        setStrategies(list)
        if (list.length) {
          const cur = (keepSel && list.find((s) => s.id === selId)) || list[0]
          setSelId(cur.id)
          setName(cur.name)
          setText(cur.text)
          setConfigText(cur.config ? JSON.stringify(cur.config, null, 2) : '')
        } else {
          setSelId('')
          setName('')
          setText('')
          setConfigText('')
        }
      })
      .catch(console.error)
  }

  useEffect(() => {
    load(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selectStrategy = (s: Strategy) => {
    setSelId(s.id)
    setName(s.name)
    setText(s.text)
    setConfigText(s.config ? JSON.stringify(s.config, null, 2) : '')
  }

  const newStrategy = () => {
    setSelId('')
    setName('新策略')
    setText('')
    setConfigText('')
  }

  const runGenerate = async () => {
    if (!idea.trim()) return
    setGenerating(true)
    try {
      const r = await generateStrategy(idea.trim())
      if (r.ok && r.strategy) {
        setText(r.strategy)
        setName(idea.trim().slice(0, 20))
        setConfigText(r.config ? JSON.stringify(r.config, null, 2) : '')
      } else {
        alert('生成失败：' + (r.error || '未知错误'))
      }
    } catch (e) {
      alert('生成失败：' + String(e))
    } finally {
      setGenerating(false)
    }
  }

  const applyPreset = (mode: 'system' | 'declared' | 'autonomous') => {
    const cfg: any = { version: 1, timing: { mode } }
    if (mode === 'declared') {
      cfg.timing.position_caps = { bull: 0.9, range: 0.5, bear: 0.2 }
    }
    setConfigText(JSON.stringify(cfg, null, 2))
  }

  const save = async () => {
    if (!name.trim() || !text.trim()) {
      alert('策略名称和内容不能为空')
      return
    }
    let cfg: any = undefined
    const t = configText.trim()
    if (t) {
      try {
        cfg = JSON.parse(t)
      } catch {
        alert('高级配置不是合法 JSON，请修正或清空后保存')
        return
      }
    }
    if (selId && strategies.find((s) => s.id === selId)) {
      await updateStrategy(selId, name.trim(), text, cfg)
    } else {
      const s = await createStrategy(name.trim(), text, cfg)
      setSelId(s.id)
    }
    load()
  }

  const del = async () => {
    if (!selId) return
    if (!confirm('确定删除当前策略？')) return
    await deleteStrategy(selId)
    load(false)
  }

  const runDecide = async () => {
    setDeciding(true)
    setDecideResult(null)
    try {
      setDecideResult(await agentDecide(text, decideDate || undefined, 'deepseek'))
    } catch (e) {
      setDecideResult({ ok: false, error: String(e) })
    } finally {
      setDeciding(false)
    }
  }

  const sel = strategies.find((s) => s.id === selId)

  return (
    <div>
      {/* 策略清单 */}
      <div className="panel">
        <h2>策略清单</h2>
        {strategies.length === 0 ? (
          <div className="tool-desc">暂无策略，点击下方「新建策略」或用「AI 生成」创建。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>策略名称</th>
                <th>创建时间</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s) => (
                <tr
                  key={s.id}
                  onClick={() => selectStrategy(s)}
                  style={{ cursor: 'pointer', background: s.id === selId ? '#eef2ff' : undefined }}
                >
                  <td style={{ fontWeight: s.id === selId ? 600 : 400 }}>{s.name}</td>
                  <td>{s.created_at || '-'}</td>
                  <td>{s.updated_at || '-'}</td>
                  <td>
                    <span className="btn-group">
                      <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); selectStrategy(s) }}>编辑</button>
                      <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); onBacktest(s.id) }}>启动回测</button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="query-bar" style={{ marginTop: 8 }}>
          <button className="btn" onClick={newStrategy}>+ 新建策略</button>
        </div>
      </div>

      {/* 编辑当前策略 */}
      <div className="panel">
        <h2>{sel ? `编辑策略：${sel.name}` : '新建策略'}</h2>

        <div className="query-bar">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="策略名称" style={{ width: 240 }} />
        </div>

        {/* AI 生成 */}
        <div className="query-bar" style={{ marginTop: 8 }}>
          <input
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            placeholder="用一句话描述策略思路，AI 将补全成完整策略（如：牛市买热门龙头，熊市空仓）"
            style={{ flex: 1 }}
          />
          <button className="btn btn-purple" onClick={runGenerate} disabled={generating}>
            {generating ? '生成中…' : 'AI 生成完整策略'}
          </button>
        </div>

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="策略内容（只写交易逻辑本身，可用上面「AI 生成」补全，也可手动编辑）"
          style={{
            width: '100%',
            height: 240,
            padding: 10,
            border: '1px solid #dcdfe6',
            borderRadius: 6,
            fontSize: 12,
            fontFamily: 'inherit',
            lineHeight: 1.6,
            marginTop: 8,
          }}
        />

        {/* 策略结构化配置（择时模式 + 高级 JSON） */}
        <div style={{ marginTop: 8 }}>
          <div className="tool-desc" style={{ marginBottom: 6 }}>
            择时模式（决定「谁来判断牛市/仓位」）：
          </div>
          <div className="query-bar" style={{ flexWrap: 'wrap' }}>
            <button className="btn btn-neutral" onClick={() => applyPreset('system')}>system（系统择时，向后兼容）</button>
            <button className="btn btn-neutral" onClick={() => applyPreset('declared')}>declared（按状态声明仓位上限）</button>
            <button className="btn btn-success" onClick={() => applyPreset('autonomous')}>autonomous（策略全自主）</button>
            <button className="btn btn-ghost" onClick={() => setConfigText('')}>清空（默认 system）</button>
          </div>
          <textarea
            value={configText}
            onChange={(e) => setConfigText(e.target.value)}
            placeholder='高级配置（JSON，可留空=system 预设）。例：{"version":1,"timing":{"mode":"autonomous"},"position":{"max_single_pct":0.3}}'
            style={{
              width: '100%',
              height: 90,
              padding: 8,
              border: '1px solid #dcdfe6',
              borderRadius: 6,
              fontSize: 11,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              marginTop: 6,
            }}
          />
        </div>

        <div className="query-bar" style={{ marginTop: 8 }}>
          <button className="btn" onClick={save}>保存策略</button>
          {selId && (
            <button className="btn btn-danger" onClick={del}>删除当前策略</button>
          )}
        </div>
        <div className="tool-desc" style={{ marginTop: 6 }}>
          提示：策略内容只写交易逻辑（择时/选股/风控/执行），无需写任何工具或技术细节，回测时会自动解析并调用对应工具。
        </div>
      </div>

      {/* 策略决策 */}
      <div className="panel">
        <h2>策略决策（单回合测试）</h2>
        <div className="tool-desc">用当前策略做一次单回合 LLM 决策，快速验证策略逻辑。</div>
        <div className="query-bar">
          <input type="date" value={decideDate} onChange={(e) => setDecideDate(e.target.value)} style={{ width: 150 }} />
          <button className="btn" onClick={runDecide} disabled={deciding}>
            {deciding ? '分析中…' : '执行决策'}
          </button>
        </div>
        {decideResult?.error && (
          <div className="tool-desc" style={{ color: '#e03131' }}>错误：{decideResult.error}</div>
        )}
        {decideResult?.conclusion && (
          <pre className="tool-result" style={{ whiteSpace: 'pre-wrap', marginTop: 8 }}>{decideResult.conclusion}</pre>
        )}
        {decideResult?.trace && decideResult.trace.length > 0 && (
          <>
            <div className="tool-desc" style={{ marginTop: 8 }}>工具调用轨迹（{decideResult.trace.length} 次）</div>
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
                    <td>{TOOL_LABEL[t.tool] ?? t.tool}</td>
                    <td style={{ textAlign: 'left', fontSize: 11 }}>{JSON.stringify(t.arguments)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}
