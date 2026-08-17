import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import {
  fetchKnowledgeNodes,
  fetchKnowledgeDimensions,
  fetchKnowledgeTaxonomy,
  fetchKnowledgeGraph,
  fetchKnowledgeMindmap,
  fetchKnowledgeNode,
  createKnowledge,
  updateKnowledge,
  deleteKnowledge,
  ingestKnowledge,
  type KnowledgeNode,
  type KnowledgeDimensions,
  type KnowledgeTaxonomy,
} from './api'
import Markdown from './Markdown'

const SUBTABS = [
  { id: 'browse', label: '知识浏览' },
  { id: 'graph', label: '知识图谱' },
  { id: 'mindmap', label: '思维导图' },
  { id: 'add', label: '录入知识' },
]

const PALETTE = [
  '#3370ff', '#12b886', '#e8590c', '#7048e8', '#e03131', '#0ca678',
  '#f08c00', '#1971c2', '#862e9c', '#c2255c', '#5f3dc4', '#099268',
  '#8a919f',
]

const AUTHORITY_COLOR: Record<string, string> = {
  权威: '#12b886', 较权威: '#3370ff', 一般: '#e8590c', 待核实: '#8a919f',
}

function KnowledgeGraphChart({
  data,
  onSelect,
}: {
  data: { categories: { name: string }[]; nodes: any[]; links: any[] }
  onSelect: (id: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (ref.current && !inst.current) inst.current = echarts.init(ref.current)
    return () => {
      inst.current?.dispose()
      inst.current = null
    }
  }, [])

  useEffect(() => {
    const chart = inst.current
    if (!chart || !data?.nodes?.length) return
    chart.off('click')
    chart.on('click', (params: any) => {
      const d = params?.data
      if (d && d.type === 'knowledge' && d.id) onSelect(d.id)
    })
    chart.setOption(
      {
        color: PALETTE,
        tooltip: {
          trigger: 'item',
          formatter: (p: any) => {
            const d = p.data || {}
            if (d.type === 'knowledge') {
              return `<b>${d.name}</b><br/>领域：${d.categoryLabel || '-'} / ${d.subcategoryLabel || '-'}<br/>风格：${d.styleLabel || '-'}<br/>${d.summary || ''}`
            }
            const kind = d.type === 'cat' ? '一级领域' : d.type === 'sub' ? '二级子类' : d.type === 'style' ? '交易风格' : '来源'
            return `<b>${d.name}</b><br/>${kind}（点击知识节点查看详情）`
          },
        },
        legend: { top: 8, left: 'center', type: 'scroll', textStyle: { fontSize: 11 } },
        series: [
          {
            type: 'graph',
            layout: 'force',
            data: data.nodes.map((n) => ({ ...n, symbolSize: n.symbolSize || 20 })),
            links: data.links,
            categories: data.categories,
            roam: true,
            draggable: true,
            force: { repulsion: 180, edgeLength: [70, 180], gravity: 0.08 },
            label: { show: true, position: 'right', fontSize: 11, color: '#1f2329' },
            emphasis: { focus: 'adjacency', label: { show: true, fontWeight: 700 } },
            lineStyle: { color: '#c9cdd4', width: 1, curveness: 0.08, opacity: 0.6 },
          },
        ],
      },
      true,
    )
  }, [data, onSelect])

  return <div className="chart" ref={ref} style={{ height: 560 }} />
}

function KnowledgeMindmapChart({
  data,
  onSelect,
}: {
  data: { tree: any }
  onSelect: (id: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (ref.current && !inst.current) inst.current = echarts.init(ref.current)
    return () => {
      inst.current?.dispose()
      inst.current = null
    }
  }, [])

  useEffect(() => {
    const chart = inst.current
    if (!chart || !data?.tree) return
    chart.off('click')
    chart.on('click', (params: any) => {
      const d = params?.data
      if (d && d.id) onSelect(d.id)
    })
    chart.setOption(
      {
        color: PALETTE,
        tooltip: { trigger: 'item', triggerOn: 'mousemove', formatter: (p: any) => p.data?.name || '' },
        series: [
          {
            type: 'tree',
            data: [data.tree],
            orient: 'LR',
            top: '4%',
            left: '6%',
            bottom: '4%',
            right: '22%',
            symbolSize: 8,
            roam: true,
            expandAndCollapse: true,
            initialTreeDepth: 2,
            animationDuration: 300,
            label: {
              position: 'left',
              verticalAlign: 'middle',
              align: 'right',
              fontSize: 12,
              color: '#1f2329',
            },
            leaves: { label: { position: 'right', align: 'left' } },
            emphasis: { focus: 'ancestor' },
          },
        ],
      },
      true,
    )
  }, [data, onSelect])

  return <div className="chart" ref={ref} style={{ height: 560 }} />
}

export default function KnowledgeCenter() {
  const [subTab, setSubTab] = useState('browse')
  const [nodes, setNodes] = useState<KnowledgeNode[]>([])
  const [dims, setDims] = useState<KnowledgeDimensions | null>(null)
  const [taxonomy, setTaxonomy] = useState<KnowledgeTaxonomy | null>(null)
  const [filters, setFilters] = useState({
    category: '', subcategory: '', knowledge_type: '', style: '', market: '',
    regime: '', source: '', authority: '', status: '', tag: '', q: '',
  })
  const [graphData, setGraphData] = useState<any>(null)
  const [mindmapBy, setMindmapBy] = useState<'domain' | 'style' | 'source' | 'knowledge_type'>('domain')
  const [mindmapData, setMindmapData] = useState<any>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const [detail, setDetail] = useState<KnowledgeNode | null>(null)

  const [form, setForm] = useState({
    title: '', summary: '', content: '', category: '', subcategory: '',
    knowledge_type: '', style: '', market: '', regime: '', source: '',
    source_name: '', author: '', tags: '', difficulty: '', authority: '',
  })
  const [editId, setEditId] = useState<string | null>(null)
  const [ingestText, setIngestText] = useState('')
  const [ingestUrl, setIngestUrl] = useState('')
  const [ingesting, setIngesting] = useState(false)
  const [ingestMsg, setIngestMsg] = useState('')

  const loadNodes = (f = filters) => {
    fetchKnowledgeNodes(f).then((d) => setNodes(d.nodes || [])).catch(console.error)
  }
  const loadDims = () => fetchKnowledgeDimensions().then(setDims).catch(console.error)
  const loadTaxonomy = () => fetchKnowledgeTaxonomy().then(setTaxonomy).catch(console.error)
  const loadGraph = () => fetchKnowledgeGraph().then(setGraphData).catch(console.error)
  const loadMindmap = (by = mindmapBy) => fetchKnowledgeMindmap(by).then(setMindmapData).catch(console.error)

  useEffect(() => {
    loadNodes()
    loadDims()
    loadTaxonomy()
    loadGraph()
    loadMindmap()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openDetail = async (id: string) => {
    const n = await fetchKnowledgeNode(id)
    if (n && !('error' in n)) setDetail(n)
  }

  const setFilter = (k: string, v: string) => {
    const next = { ...filters, [k]: v }
    setFilters(next)
    loadNodes(next)
  }

  const clickDomain = (domain: string) => {
    const next = { ...filters, category: filters.category === domain ? '' : domain, subcategory: '' }
    setFilters(next)
    loadNodes(next)
  }

  const clickSub = (domain: string, sub: string) => {
    const next = {
      ...filters,
      category: domain,
      subcategory: filters.subcategory === sub ? '' : sub,
    }
    setFilters(next)
    loadNodes(next)
  }

  const resetFilters = () => {
    const empty = { category: '', subcategory: '', knowledge_type: '', style: '', market: '', regime: '', source: '', authority: '', status: '', tag: '', q: '' }
    setFilters(empty)
    loadNodes(empty)
  }

  const doDelete = async () => {
    if (!detail) return
    if (!confirm(`确定删除知识「${detail.title}」？`)) return
    await deleteKnowledge(detail.id)
    setDetail(null)
    loadNodes(); loadDims(); loadGraph(); loadMindmap()
  }

  const startEdit = (n: KnowledgeNode) => {
    setEditId(n.id)
    setForm({
      title: n.title || '',
      summary: n.summary || '',
      content: n.content || '',
      category: n.category || '',
      subcategory: n.subcategory || '',
      knowledge_type: n.knowledge_type || '',
      style: n.style || '',
      market: n.market || '',
      regime: n.regime || '',
      source: n.source || '',
      source_name: n.source_name || '',
      author: n.author || '',
      tags: (n.tags || []).join('、'),
      difficulty: n.difficulty || '',
      authority: n.authority || '',
    })
    setSubTab('add')
    setDetail(null)
  }

  const saveForm = async () => {
    if (!form.title.trim() || !form.content.trim()) {
      alert('标题与正文不能为空')
      return
    }
    const payload = {
      title: form.title.trim(),
      summary: form.summary.trim() || undefined,
      content: form.content,
      category: form.category || undefined,
      subcategory: form.subcategory || undefined,
      knowledge_type: form.knowledge_type || undefined,
      style: form.style || undefined,
      market: form.market || undefined,
      regime: form.regime || undefined,
      source: form.source || undefined,
      source_name: form.source_name.trim() || undefined,
      author: form.author.trim() || undefined,
      tags: form.tags.split(/[,，、;；]/).map((t) => t.trim()).filter(Boolean),
      difficulty: form.difficulty || undefined,
      authority: form.authority || undefined,
    }
    if (editId) await updateKnowledge(editId, payload)
    else await createKnowledge(payload as any)
    setEditId(null)
    setForm({ title: '', summary: '', content: '', category: '', subcategory: '', knowledge_type: '', style: '', market: '', regime: '', source: '', source_name: '', author: '', tags: '', difficulty: '', authority: '' })
    loadNodes(); loadDims(); loadGraph(); loadMindmap()
    setSubTab('browse')
  }

  const runIngest = async () => {
    if (!ingestText.trim() && !ingestUrl.trim()) {
      alert('请粘贴知识正文或提供来源链接')
      return
    }
    setIngesting(true)
    setIngestMsg('正在吸收，请稍候…')
    try {
      const r = await ingestKnowledge(ingestText.trim() || undefined, ingestUrl.trim() || undefined)
      if (r.ok && r.node) {
        setIngestMsg(`已吸收：${r.node.title}（待审核），已入库`)
        setIngestText(''); setIngestUrl('')
        loadNodes(); loadDims(); loadGraph(); loadMindmap()
      } else if (r.duplicate) {
        setIngestMsg('已存在相似知识，未重复录入')
      } else {
        setIngestMsg('吸收失败：' + (r.error || '未知错误'))
      }
    } catch (e) {
      setIngestMsg('吸收失败：' + String(e))
    } finally {
      setIngesting(false)
    }
  }

  const subCountMap: Record<string, Record<string, number>> = {}
  if (dims) {
    for (const g of dims.subcategories) {
      subCountMap[g.domain] = {}
      for (const it of g.items) subCountMap[g.domain][it.value] = it.count
    }
  }

  const select = (key: string, opts: string[]) => (
    <select value={(filters as any)[key]} onChange={(e) => setFilter(key, e.target.value)}>
      <option value="">全部{key === 'authority' ? '权威性' : key === 'regime' ? '行情' : key === 'market' ? '市场' : ''}</option>
      {opts.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  )

  return (
    <div>
      <div className="panel">
        <h2>知识中心</h2>
        <div className="tool-desc">
          按「投资决策流程」建立九大知识领域 + 二级子类的科学分类体系，覆盖选股、择时、组合仓位、风控、基本面、技术、心理、量化等；支持多维标签筛选、知识图谱与思维导图，可持续录入扩充。
        </div>
        <div className="query-bar" style={{ flexWrap: 'wrap' }}>
          {SUBTABS.map((t) => (
            <button key={t.id} className={subTab === t.id ? 'btn' : 'btn btn-ghost'} onClick={() => setSubTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {subTab === 'browse' && (
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          {/* 一级/二级分级树 */}
          <div className="panel" style={{ width: 260, flexShrink: 0, position: 'sticky', top: 16, maxHeight: '80vh', overflowY: 'auto' }}>
            <h2>知识领域</h2>
            <button
              className="btn btn-sm btn-ghost"
              onClick={resetFilters}
              style={{ marginBottom: 8, background: !filters.category && !filters.subcategory ? '#eef3ff' : undefined }}
            >
              全部（{dims?.total ?? 0}）
            </button>
            {taxonomy && Object.entries(taxonomy.domains).map(([domain, subs]) => {
              const count = dims?.domains.find((d) => d.value === domain)?.count ?? 0
              const isOpen = expanded.has(domain)
              const active = filters.category === domain && !filters.subcategory
              return (
                <div key={domain} style={{ marginBottom: 4 }}>
                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', padding: '4px 6px', borderRadius: 5, background: active ? '#eef3ff' : undefined, fontWeight: active ? 600 : 400 }}
                  >
                    <span style={{ color: '#8a919f', fontSize: 11, width: 14, textAlign: 'center' }} onClick={(e) => { e.stopPropagation(); setExpanded((s) => { const n = new Set(s); n.has(domain) ? n.delete(domain) : n.add(domain); return n }) }}>
                      {isOpen ? '▾' : '▸'}
                    </span>
                    <span style={{ flex: 1, fontSize: 13 }} onClick={() => clickDomain(domain)}>{domain}</span>
                    <span style={{ color: '#8a919f', fontSize: 11 }}>{count}</span>
                  </div>
                  {isOpen && (
                    <div style={{ paddingLeft: 18 }}>
                      {subs.map((sub) => {
                        const sc = subCountMap[domain]?.[sub] ?? 0
                        const subActive = filters.subcategory === sub
                        return (
                          <div key={sub} onClick={() => clickSub(domain, sub)} style={{ cursor: 'pointer', padding: '3px 6px', fontSize: 12, color: subActive ? '#3370ff' : '#5b6470', fontWeight: subActive ? 600 : 400, borderRadius: 4, background: subActive ? '#eef3ff' : undefined }}>
                            {sub} <span style={{ color: '#a0a6b3', fontSize: 11 }}>{sc}</span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* 列表 + 标签筛选 */}
          <div className="panel" style={{ flex: 1, minWidth: 0 }}>
            <div className="query-bar" style={{ flexWrap: 'wrap' }}>
              <input value={filters.q} onChange={(e) => setFilter('q', e.target.value)} placeholder="搜索标题/摘要/正文/来源…" style={{ width: 220 }} />
              {taxonomy && select('knowledge_type', taxonomy.knowledge_types)}
              {taxonomy && select('style', taxonomy.styles)}
              {taxonomy && select('market', taxonomy.markets)}
              {taxonomy && select('regime', taxonomy.regimes)}
              {taxonomy && select('authority', taxonomy.authorities)}
              {taxonomy && select('source', taxonomy.sources)}
            </div>
            <table>
              <thead>
                <tr>
                  <th>标题</th>
                  <th>领域 / 子类</th>
                  <th>知识类型</th>
                  <th>风格</th>
                  <th>权威性</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => (
                  <tr key={n.id} onClick={() => openDetail(n.id)} style={{ cursor: 'pointer' }}>
                    <td style={{ fontWeight: 500 }}>{n.title}</td>
                    <td>{n.category || '-'}{n.subcategory ? ` / ${n.subcategory}` : ''}</td>
                    <td>{n.knowledge_type || '-'}</td>
                    <td>{n.style || '-'}</td>
                    <td>
                      <span style={{ color: AUTHORITY_COLOR[n.authority || ''] || '#1f2329', fontWeight: 600 }}>{n.authority || '-'}</span>
                    </td>
                    <td>{n.source_name || n.source || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="tool-desc" style={{ marginTop: 8 }}>共 {nodes.length} 条，点击任意行查看详情。</div>
          </div>
        </div>
      )}

      {subTab === 'graph' && (
        <div className="panel">
          <h2>知识图谱</h2>
          <div className="tool-desc">节点按一级领域着色，中心为维度节点（领域/子类/风格/来源），点击知识节点查看详情，可拖拽、缩放。</div>
          <KnowledgeGraphChart data={graphData} onSelect={openDetail} />
        </div>
      )}

      {subTab === 'mindmap' && (
        <div className="panel">
          <h2>思维导图</h2>
          <div className="query-bar" style={{ flexWrap: 'wrap' }}>
            {([['domain', '按知识领域（含子类）'], ['style', '按交易风格'], ['source', '按来源'], ['knowledge_type', '按知识类型']] as const).map(([by, label]) => (
              <button key={by} className={mindmapBy === by ? 'btn' : 'btn btn-ghost'} onClick={() => { setMindmapBy(by); loadMindmap(by) }}>
                {label}
              </button>
            ))}
          </div>
          <KnowledgeMindmapChart data={mindmapData} onSelect={openDetail} />
        </div>
      )}

      {subTab === 'add' && (
        <div>
          <div className="panel">
            <h2>AI 吸收（粘贴正文 / 提供链接）</h2>
            <div className="tool-desc">粘贴一段股票交易知识正文，或提供来源链接，系统会抓取网页并用大模型提炼成结构化知识卡片（自动归入分类体系）后入库。</div>
            <textarea value={ingestText} onChange={(e) => setIngestText(e.target.value)} placeholder="在此粘贴知识正文（可选，与链接二选一或都填）…" style={{ width: '100%', height: 140, padding: 10, border: '1px solid #dcdfe6', borderRadius: 6, fontSize: 12, fontFamily: 'inherit', lineHeight: 1.6 }} />
            <div className="query-bar" style={{ marginTop: 8 }}>
              <input value={ingestUrl} onChange={(e) => setIngestUrl(e.target.value)} placeholder="或粘贴来源链接 http(s)://…" style={{ flex: 1 }} />
              <button className="btn btn-purple" onClick={runIngest} disabled={ingesting}>{ingesting ? '吸收中…' : '吸收并入库'}</button>
            </div>
            {ingestMsg && <div className="tool-desc" style={{ color: ingestMsg.includes('失败') ? '#e03131' : '#12b886' }}>{ingestMsg}</div>}
          </div>

          <div className="panel">
            <h2>{editId ? '编辑知识' : '手动录入知识'}</h2>
            <div className="query-bar" style={{ flexWrap: 'wrap' }}>
              <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="标题（必填）" style={{ width: 300 }} />
              {taxonomy && (
                <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value, subcategory: '' })}>
                  <option value="">一级领域</option>
                  {Object.keys(taxonomy.domains).map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              )}
              {taxonomy && form.category && (
                <select value={form.subcategory} onChange={(e) => setForm({ ...form, subcategory: e.target.value })}>
                  <option value="">二级子类</option>
                  {(taxonomy.domains[form.category] || []).map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              )}
              {taxonomy && (
                <select value={form.knowledge_type} onChange={(e) => setForm({ ...form, knowledge_type: e.target.value })}>
                  <option value="">知识类型</option>
                  {taxonomy.knowledge_types.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              )}
              {taxonomy && (
                <select value={form.style} onChange={(e) => setForm({ ...form, style: e.target.value })}>
                  <option value="">交易风格</option>
                  {taxonomy.styles.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              )}
              {taxonomy && (
                <select value={form.market} onChange={(e) => setForm({ ...form, market: e.target.value })}>
                  <option value="">适用市场</option>
                  {taxonomy.markets.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              )}
              {taxonomy && (
                <select value={form.regime} onChange={(e) => setForm({ ...form, regime: e.target.value })}>
                  <option value="">适用行情</option>
                  {taxonomy.regimes.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              )}
              {taxonomy && (
                <select value={form.authority} onChange={(e) => setForm({ ...form, authority: e.target.value })}>
                  <option value="">权威性</option>
                  {taxonomy.authorities.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              )}
              {taxonomy && (
                <select value={form.difficulty} onChange={(e) => setForm({ ...form, difficulty: e.target.value })}>
                  <option value="">难度</option>
                  {taxonomy.difficulties.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              )}
              {taxonomy && (
                <select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
                  <option value="">来源类型</option>
                  {taxonomy.sources.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              )}
            </div>
            <div className="query-bar" style={{ flexWrap: 'wrap', marginTop: 6 }}>
              <input value={form.source_name} onChange={(e) => setForm({ ...form, source_name: e.target.value })} placeholder="具体来源，如《海龟交易法则》" style={{ width: 240 }} />
              <input value={form.author} onChange={(e) => setForm({ ...form, author: e.target.value })} placeholder="作者" style={{ width: 160 }} />
              <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="标签，用顿号分隔" style={{ width: 240 }} />
            </div>
            <input value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} placeholder="一句话摘要（可选）" style={{ width: '100%', padding: '8px 12px', border: '1px solid #dcdfe6', borderRadius: 6, fontSize: 14, marginTop: 8 }} />
            <textarea value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="知识正文（支持 Markdown：**粗体**、## 标题、- 列表）（必填）" style={{ width: '100%', height: 200, padding: 10, border: '1px solid #dcdfe6', borderRadius: 6, fontSize: 12, fontFamily: 'inherit', lineHeight: 1.6, marginTop: 8 }} />
            <div className="query-bar" style={{ marginTop: 8 }}>
              <button className="btn" onClick={saveForm}>{editId ? '保存修改' : '录入知识'}</button>
              {editId && <button className="btn btn-ghost" onClick={() => { setEditId(null); setForm({ title: '', summary: '', content: '', category: '', subcategory: '', knowledge_type: '', style: '', market: '', regime: '', source: '', source_name: '', author: '', tags: '', difficulty: '', authority: '' }) }}>取消编辑</button>}
            </div>
          </div>
        </div>
      )}

      {detail && (
        <div className="modal-overlay" onClick={() => setDetail(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 880 }}>
            <div className="modal-head">
              <span>{detail.title}</span>
              <div>
                <button className="btn btn-sm" onClick={() => startEdit(detail)}>编辑</button>
                <button className="btn btn-sm btn-danger" onClick={doDelete}>删除</button>
                <button className="btn btn-sm btn-ghost" onClick={() => setDetail(null)}>关闭</button>
              </div>
            </div>
            <div className="modal-body">
              <div className="tool-desc" style={{ marginBottom: 8, lineHeight: 2 }}>
                <span className="analysis-tag" style={{ marginRight: 6 }}>{detail.category || '综合'}{detail.subcategory ? ` / ${detail.subcategory}` : ''}</span>
                <span className="analysis-tag" style={{ marginRight: 6, color: '#7048e8', background: '#f3f0ff' }}>{detail.knowledge_type || '方法规则'}</span>
                <span className="analysis-tag" style={{ marginRight: 6, color: '#12b886', background: '#e6fcf5' }}>{detail.style || '混合'}</span>
                <span className="analysis-tag" style={{ marginRight: 6, color: '#e8590c', background: '#fff4e6' }}>{detail.difficulty || '入门'}</span>
                {detail.market && <span className="analysis-tag" style={{ marginRight: 6 }}>市场：{detail.market}</span>}
                {detail.regime && <span className="analysis-tag" style={{ marginRight: 6 }}>行情：{detail.regime}</span>}
                <span className="analysis-tag" style={{ marginRight: 6, color: AUTHORITY_COLOR[detail.authority || ''] || '#1f2329', background: '#fff', border: '1px solid #dcdfe6' }}>
                  权威性：{detail.authority || '待核实'}
                </span>
                <span className="analysis-tag" style={{ marginRight: 6, color: detail.status === '已核实' ? '#12b886' : '#e8590c', background: '#fff', border: '1px solid #dcdfe6' }}>
                  {detail.status || '已收录'}
                </span>
              </div>
              <div className="tool-desc" style={{ marginBottom: 10, fontSize: 12 }}>
                来源：{detail.source_name || '-'}{detail.author ? `（${detail.author}）` : ''} · 录入方式：{detail.created_by === 'seed' ? '内置种子' : detail.created_by === 'ingest_url' ? '链接吸收' : detail.created_by === 'ingest_text' ? '正文吸收' : '手动录入'}
                · 版本 v{detail.version ?? 1} · 创建 {detail.created_at || '-'} · 更新 {detail.updated_at || '-'}
              </div>
              {detail.summary && <blockquote className="md-quote"><p>{detail.summary}</p></blockquote>}
              <div className="md-body" style={{ maxHeight: '52vh', overflowY: 'auto' }}>
                {detail.content ? <Markdown content={detail.content} /> : <p>（无正文）</p>}
              </div>
              {detail.review_note && <div className="tool-desc" style={{ marginTop: 10, color: '#e8590c' }}>审核备注：{detail.review_note}</div>}
              {(detail.tags && detail.tags.length > 0) && (
                <div style={{ marginTop: 12 }}>
                  {(detail.tags || []).map((t) => <span key={t} className="analysis-tag" style={{ marginRight: 6 }}>{t}</span>)}
                </div>
              )}
              {detail.source_url && (
                <div style={{ marginTop: 12, fontSize: 12 }}>
                  原文链接：<a href={detail.source_url} target="_blank" rel="noreferrer">{detail.source_url}</a>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
