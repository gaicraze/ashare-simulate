import { useEffect, useState } from 'react'
import {
  fetchTools,
  callTool,
  fetchCustomTools,
  generateCustomTool,
  deleteCustomTool,
  type ToolDef,
} from './api'

export default function ToolsPanel() {
  const [tools, setTools] = useState<ToolDef[]>([])
  const [selTool, setSelTool] = useState('')
  const [argsJson, setArgsJson] = useState('{}')
  const [toolResult, setToolResult] = useState('')

  const [requirement, setRequirement] = useState('')
  const [generating, setGenerating] = useState(false)
  const [genMsg, setGenMsg] = useState('')
  const [customTools, setCustomTools] = useState<any[]>([])

  const load = () => {
    fetchTools()
      .then((d) => {
        setTools(d.tools || [])
        if (d.tools?.length && !selTool) setSelTool(d.tools[0].function.name)
      })
      .catch(console.error)
    fetchCustomTools()
      .then((d) => setCustomTools(d.tools || []))
      .catch(console.error)
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const runTool = async () => {
    setToolResult('调用中...')
    try {
      const args = JSON.parse(argsJson || '{}')
      const r = await callTool(selTool, args)
      setToolResult(JSON.stringify(r, null, 2))
    } catch (e) {
      setToolResult('调用失败: ' + String(e))
    }
  }

  const runGenerate = async () => {
    if (!requirement.trim()) return
    setGenerating(true)
    setGenMsg('生成中…')
    try {
      const r = await generateCustomTool(requirement.trim())
      if (r.ok) {
        setGenMsg(`已生成工具「${r.tool.name}」，已注册到工具库，可在下方调用`)
        setRequirement('')
        load()
      } else {
        setGenMsg('生成失败：' + r.error)
      }
    } catch (e) {
      setGenMsg('生成失败：' + String(e))
    } finally {
      setGenerating(false)
    }
  }

  const delCustom = async (name: string) => {
    await deleteCustomTool(name)
    load()
  }

  return (
    <div>
      <div className="panel">
        <h2>AI 生成工具（自造工具）</h2>
        <div className="tool-desc" style={{ marginBottom: 8 }}>
          描述一个数据查询需求，AI 自动生成只读 SQL 工具并注册到工具库，策略决策时可直接调用。系统会持续完善工具，不必因缺工具而降低策略。
        </div>
        <div className="query-bar">
          <input
            value={requirement}
            onChange={(e) => setRequirement(e.target.value)}
            placeholder="如：筛选ROE大于10%且市盈率为正的优质龙头股"
            style={{ flex: 1 }}
          />
          <button className="btn btn-purple" onClick={runGenerate} disabled={generating}>
            {generating ? '生成中…' : 'AI 生成工具'}
          </button>
        </div>
        {genMsg && <div className="tool-desc" style={{ marginTop: 6 }}>{genMsg}</div>}
        {customTools.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div className="tool-desc" style={{ marginBottom: 4 }}>已生成的自造工具（{customTools.length} 个）：</div>
            <table>
              <thead>
                <tr>
                  <th>工具名</th>
                  <th style={{ textAlign: 'left' }}>描述</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {customTools.map((t) => (
                  <tr key={t.name}>
                    <td>{t.name}</td>
                    <td style={{ textAlign: 'left' }}>{t.description}</td>
                    <td>
                      <button className="btn btn-danger" onClick={() => delCustom(t.name)}>
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <h2>工具调用</h2>
        <div className="query-bar">
          <select value={selTool} onChange={(e) => setSelTool(e.target.value)}>
            {tools.map((t) => (
              <option key={t.function.name} value={t.function.name}>
                {t.function.name}
              </option>
            ))}
          </select>
          <input
            value={argsJson}
            onChange={(e) => setArgsJson(e.target.value)}
            placeholder='参数JSON，如 {"code":"600519"}'
            style={{ width: 320 }}
          />
          <button className="btn" onClick={runTool}>调用</button>
        </div>
        {tools.find((t) => t.function.name === selTool) && (
          <div className="tool-desc">
            {tools.find((t) => t.function.name === selTool)!.function.description}
          </div>
        )}
        {toolResult && <pre className="tool-result">{toolResult}</pre>}
      </div>
    </div>
  )
}
