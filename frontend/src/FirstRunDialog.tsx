import { useState } from 'react'
import { addLlmProvider } from './api'

interface Preset {
  id: string
  label: string
  name: string
  base_url: string
  model: string
}

const PRESETS: Preset[] = [
  { id: 'deepseek', label: 'DeepSeek', name: 'DeepSeek', base_url: 'https://api.deepseek.com', model: 'deepseek-chat' },
  { id: 'minimax', label: 'MiniMax', name: 'MiniMax', base_url: 'https://api.minimaxi.com/v1', model: 'MiniMax-M2' },
  { id: 'openai', label: 'OpenAI', name: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { id: 'qwen', label: '通义千问', name: '通义千问', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { id: 'glm', label: '智谱 GLM', name: '智谱GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  { id: 'ollama', label: 'Ollama（本地）', name: 'Ollama', base_url: 'http://localhost:11434/v1', model: 'llama3' },
  { id: 'custom', label: '自定义', name: '', base_url: '', model: '' },
]

const EMPTY = { name: '', base_url: '', model: '', api_key: '' }

export default function FirstRunDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [presetId, setPresetId] = useState('deepseek')
  const [form, setForm] = useState({
    ...EMPTY,
    name: PRESETS[0].name,
    base_url: PRESETS[0].base_url,
    model: PRESETS[0].model,
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  if (!open) return null

  const applyPreset = (id: string) => {
    const p = PRESETS.find((x) => x.id === id)
    if (!p) return
    setPresetId(id)
    setForm({ name: p.name, base_url: p.base_url, model: p.model, api_key: form.api_key })
  }

  const save = async () => {
    if (!form.name || !form.base_url || !form.model || !form.api_key) {
      setError('请填写完整：名称、base_url、模型名、API Key')
      return
    }
    setSaving(true)
    setError('')
    try {
      await addLlmProvider(form)
      onClose()
    } catch (e) {
      setError('保存失败：' + String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal">
        <div className="modal-head">
          <span>👋 欢迎使用 · 请先配置大模型 API</span>
        </div>
        <div className="modal-body">
          <div className="tool-desc" style={{ lineHeight: 1.7, color: '#5b6470' }}>
            本系统由大模型驱动策略模拟与决策，需要至少一个 <b>OpenAI 兼容</b> 的模型服务。
            选择一个预设（或自定义），填入你的 API Key 即可开始。Key 仅保存在本地 <code>backend/data/llm_config.json</code>，不会上传到任何地方。
          </div>
          <div className="query-bar">
            <label style={{ width: 90, fontSize: 13, lineHeight: '34px', color: '#5b6470' }}>服务商</label>
            <select value={presetId} onChange={(e) => applyPreset(e.target.value)}>
              {PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <div className="query-bar">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="名称，如 DeepSeek"
              style={{ width: 160 }}
            />
            <input
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              placeholder="base_url，如 https://api.deepseek.com"
              style={{ width: 340 }}
            />
          </div>
          <div className="query-bar">
            <input
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              placeholder="模型名，如 deepseek-chat"
              style={{ width: 200 }}
            />
            <input
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder="API Key"
              type="password"
              style={{ width: 300 }}
            />
          </div>
          {error && <div className="tool-desc" style={{ color: '#e03131' }}>{error}</div>}
          <div className="tool-desc" style={{ marginTop: 4, marginBottom: 0 }}>
            提示：可随时在左侧「模型配置」中添加 / 切换多个模型，并为不同用途（回测、策略生成等）指定不同模型。
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose}>暂不配置</button>
          <button className="btn btn-success" onClick={save} disabled={saving}>
            {saving ? '保存中…' : '保存并开始使用'}
          </button>
        </div>
      </div>
    </div>
  )
}
