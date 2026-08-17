import { useEffect, useState } from 'react'
import { fetchStrategies, createStrategy, deleteStrategy, type Strategy } from './api'

export default function StrategyPanel({ onSelect }: { onSelect?: (s: Strategy) => void }) {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selId, setSelId] = useState('')
  const [name, setName] = useState('')
  const [text, setText] = useState('')

  const load = () => {
    fetchStrategies()
      .then((d) => {
        setStrategies(d.strategies || [])
        if (d.strategies?.length && (!selId || !d.strategies.find((s) => s.id === selId))) {
          setSelId(d.strategies[0].id)
        }
      })
      .catch(console.error)
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sel = strategies.find((s) => s.id === selId)

  const add = async () => {
    if (!name.trim() || !text.trim()) return
    await createStrategy(name.trim(), text.trim())
    setName('')
    setText('')
    load()
  }

  const del = async (id: string) => {
    await deleteStrategy(id)
    load()
  }

  return (
    <div className="panel">
      <h2>策略管理</h2>
      <div className="query-bar">
        <select value={selId} onChange={(e) => setSelId(e.target.value)}>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        {sel && onSelect && <button className="btn" onClick={() => onSelect(sel)}>加载到决策/回测</button>}
        {sel && (
          <button className="btn btn-danger" onClick={() => del(sel.id)}>
            删除
          </button>
        )}
      </div>
      {sel && <pre className="tool-result">{sel.text}</pre>}
      <div className="query-bar" style={{ marginTop: 8 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="新策略名称" style={{ width: 200 }} />
        <button className="btn" onClick={add}>新建策略</button>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="在此输入新策略的思路文本，然后点「新建策略」"
        style={{ width: '100%', height: 90, padding: 8, border: '1px solid #dcdfe6', borderRadius: 6, fontSize: 12, fontFamily: 'inherit' }}
      />
    </div>
  )
}
