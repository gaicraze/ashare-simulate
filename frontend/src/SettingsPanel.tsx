import { useEffect, useState } from 'react'
import {
  fetchLlmConfig,
  addLlmProvider,
  updateLlmProvider,
  deleteLlmProvider,
  setLlmRole,
} from './api'

const EMPTY = { name: '', base_url: '', model: '', api_key: '' }

export default function SettingsPanel() {
  const [config, setConfig] = useState<any>(null)
  const [newForm, setNewForm] = useState({ ...EMPTY })
  const [editId, setEditId] = useState('')
  const [editForm, setEditForm] = useState({ ...EMPTY })

  const load = () => {
    fetchLlmConfig().then(setConfig).catch(console.error)
  }

  useEffect(() => {
    load()
  }, [])

  const providers: any[] = config?.providers || []
  const roles: Record<string, string> = config?.roles || {}
  const roleDefs: { id: string; label: string }[] = config?.role_defs || []

  const add = async () => {
    if (!newForm.name || !newForm.base_url || !newForm.model || !newForm.api_key) {
      alert('请填写完整：名称、base_url、模型名、API Key')
      return
    }
    await addLlmProvider(newForm)
    setNewForm({ ...EMPTY })
    load()
  }

  const del = async (id: string) => {
    if (!confirm('确定删除该模型？')) return
    await deleteLlmProvider(id)
    load()
  }

  const toggle = async (p: any) => {
    await updateLlmProvider(p.id, { enabled: !p.enabled })
    load()
  }

  const setRole = async (role: string, pid: string) => {
    await setLlmRole(role, pid || null)
    load()
  }

  const startEdit = (p: any) => {
    setEditId(p.id)
    setEditForm({ name: p.name, base_url: p.base_url, model: p.model, api_key: '' })
  }

  const saveEdit = async () => {
    const fields: Record<string, unknown> = { name: editForm.name, base_url: editForm.base_url, model: editForm.model }
    if (editForm.api_key) fields.api_key = editForm.api_key
    await updateLlmProvider(editId, fields)
    setEditId('')
    load()
  }

  return (
    <div>
      {/* 用途路由 */}
      <div className="panel">
        <h2>用途 → 模型路由</h2>
        <div className="tool-desc" style={{ marginBottom: 8 }}>
          为每个使用大模型的场景单独指定模型。回测执行建议用快速模型，策略生成建议用强模型。
        </div>
        {roleDefs.map((r) => (
          <div key={r.id} className="query-bar" style={{ marginBottom: 4 }}>
            <label style={{ width: 280, fontSize: 13 }}>{r.label}</label>
            <select value={roles[r.id] || ''} onChange={(e) => setRole(r.id, e.target.value)}>
              <option value="">（未指定，自动 fallback）</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}（{p.model}）
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>

      {/* 模型列表 */}
      <div className="panel">
        <h2>模型列表</h2>
        {providers.length === 0 ? (
          <div className="tool-desc">暂无模型，请在下方添加。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>模型名</th>
                <th>base_url</th>
                <th>Key</th>
                <th>启用</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>{p.model}</td>
                  <td style={{ fontSize: 11 }}>{p.base_url}</td>
                  <td>{p.has_key ? '已配置' : '未配置'}</td>
                  <td>
                    <input type="checkbox" checked={p.enabled} onChange={() => toggle(p)} />
                  </td>
                  <td>
                    <span className="btn-group">
                      <button className="btn btn-sm" onClick={() => startEdit(p)}>编辑</button>
                      <button className="btn btn-sm btn-danger" onClick={() => del(p.id)}>删除</button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {editId && (
          <div className="panel" style={{ boxShadow: 'none', border: '1px solid #dcdfe6', marginTop: 12 }}>
            <div className="tool-desc" style={{ marginBottom: 8 }}>编辑模型（Key 留空表示不修改）</div>
            <div className="query-bar">
              <input value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} placeholder="名称" style={{ width: 120 }} />
              <input value={editForm.base_url} onChange={(e) => setEditForm({ ...editForm, base_url: e.target.value })} placeholder="base_url" style={{ width: 220 }} />
              <input value={editForm.model} onChange={(e) => setEditForm({ ...editForm, model: e.target.value })} placeholder="模型名" style={{ width: 160 }} />
              <input value={editForm.api_key} onChange={(e) => setEditForm({ ...editForm, api_key: e.target.value })} placeholder="新 API Key（可选）" type="password" style={{ width: 200 }} />
              <button className="btn" onClick={saveEdit}>保存</button>
              <button className="btn btn-ghost" onClick={() => setEditId('')}>取消</button>
            </div>
          </div>
        )}
      </div>

      {/* 添加模型 */}
      <div className="panel">
        <h2>添加模型</h2>
        <div className="tool-desc" style={{ marginBottom: 8 }}>
          任意 OpenAI 兼容的模型服务都可添加（如 DeepSeek、Minimax、Qwen、GLM、OpenAI 等）。
        </div>
        <div className="query-bar">
          <input value={newForm.name} onChange={(e) => setNewForm({ ...newForm, name: e.target.value })} placeholder="名称，如 DeepSeek" style={{ width: 130 }} />
          <input value={newForm.base_url} onChange={(e) => setNewForm({ ...newForm, base_url: e.target.value })} placeholder="base_url，如 https://api.deepseek.com" style={{ width: 240 }} />
          <input value={newForm.model} onChange={(e) => setNewForm({ ...newForm, model: e.target.value })} placeholder="模型名，如 deepseek-chat" style={{ width: 170 }} />
          <input value={newForm.api_key} onChange={(e) => setNewForm({ ...newForm, api_key: e.target.value })} placeholder="API Key" type="password" style={{ width: 200 }} />
          <button className="btn btn-success" onClick={add}>添加</button>
        </div>
      </div>
    </div>
  )
}
