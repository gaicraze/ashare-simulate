import type { ReactNode } from 'react'

// 轻量 Markdown 渲染：支持标题/段落/粗体/斜体/行内代码/列表/表格/引用/分割线。
// 不依赖第三方库，也不使用 dangerouslySetInnerHTML，避免 XSS。

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**') && p.length > 4) {
      return <strong key={i}>{p.slice(2, -2)}</strong>
    }
    if (p.startsWith('`') && p.endsWith('`') && p.length > 2) {
      return <code key={i} className="md-code">{p.slice(1, -1)}</code>
    }
    if (p.startsWith('*') && p.endsWith('*') && p.length > 2) {
      return <em key={i}>{p.slice(1, -1)}</em>
    }
    return <span key={i}>{p}</span>
  })
}

function renderTable(lines: string[], keyBase: string): ReactNode {
  const parseRow = (line: string) =>
    line
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((c) => c.trim())
  const header = parseRow(lines[0])
  const body = lines.slice(2).filter((l) => l.trim().startsWith('|'))
  return (
    <div className="md-table-wrap" key={keyBase}>
      <table className="md-table">
        <thead>
          <tr>
            {header.map((h, i) => (
              <th key={i}>{renderInline(h)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((line, ri) => {
            const cells = parseRow(line)
            return (
              <tr key={ri}>
                {header.map((_, ci) => (
                  <td key={ci}>{renderInline(cells[ci] ?? '')}</td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function Markdown({ content }: { content: string }) {
  const lines = content.split('\n')
  const nodes: ReactNode[] = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    // 表格：以 | 开头，且下一行是 |---| 分隔行
    if (trimmed.startsWith('|') && i + 1 < lines.length && /^\|?[\s:|-]+\|?$/.test(lines[i + 1].trim())) {
      let end = i + 1
      while (end < lines.length && lines[end].trim().startsWith('|')) end++
      nodes.push(renderTable(lines.slice(i, end), `t${key++}`))
      i = end
      continue
    }

    if (trimmed === '') {
      i++
      continue
    }

    // 分割线
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
      nodes.push(<hr key={`hr${key++}`} className="md-hr" />)
      i++
      continue
    }

    // 标题
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      const level = h[1].length
      const text = h[2]
      const Tag = (level <= 2 ? 'h3' : 'h4') as 'h3'
      nodes.push(
        <Tag key={`h${key++}`} className={level <= 2 ? 'md-h2' : 'md-h3'}>
          {renderInline(text)}
        </Tag>,
      )
      i++
      continue
    }

    // 引用
    if (trimmed.startsWith('>')) {
      const quoteLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''))
        i++
      }
      nodes.push(
        <blockquote key={`q${key++}`} className="md-quote">
          {quoteLines.map((ql, qi) => (
            <p key={qi}>{renderInline(ql)}</p>
          ))}
        </blockquote>,
      )
      continue
    }

    // 无序列表
    if (/^[-*+]\s+/.test(trimmed)) {
      const items: string[] = []
      while (i < lines.length && /^[-*+]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*+]\s+/, ''))
        i++
      }
      nodes.push(
        <ul key={`ul${key++}`} className="md-list">
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it)}</li>
          ))}
        </ul>,
      )
      continue
    }

    // 有序列表
    if (/^\d+[.、]\s+/.test(trimmed)) {
      const items: string[] = []
      while (i < lines.length && /^\d+[.、]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+[.、]\s+/, ''))
        i++
      }
      nodes.push(
        <ol key={`ol${key++}`} className="md-list">
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it)}</li>
          ))}
        </ol>,
      )
      continue
    }

    // 段落（合并连续非空行）
    const para: string[] = []
    while (i < lines.length && lines[i].trim() !== '' && !/^(#{1,6}\s|>|[-*+]\s|\d+[.、]\s|\|)/.test(lines[i].trim())) {
      para.push(lines[i].trim())
      i++
    }
    nodes.push(
      <p key={`p${key++}`} className="md-p">
        {renderInline(para.join(' '))}
      </p>,
    )
  }

  return <div className="md-body">{nodes}</div>
}
