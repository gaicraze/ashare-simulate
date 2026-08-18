export interface Summary {
  db_path: string
  tables: Record<string, number>
  daily: { min_date: string | null; max_date: string | null; distinct_codes: number }
  latest_trade_date: string | null
}

export interface DailyRow {
  code: string
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number | null
  adj_factor: number | null
  pct_change: number | null
  turnover: number | null
  float_mktcap: number | null
  pe_ttm: number | null
  pb_mrq: number | null
}

export async function fetchSummary(): Promise<Summary> {
  const r = await fetch('/api/data/summary', { cache: 'no-store' })
  return r.json()
}

export async function fetchDaily(code: string, limit = 60): Promise<{ rows: DailyRow[] }> {
  const r = await fetch(`/api/data/daily?code=${code}&limit=${limit}`, { cache: 'no-store' })
  return r.json()
}

export interface StockInfo {
  code: string
  name: string | null
  industry: string | null
}

export async function searchStocks(q: string): Promise<{ rows: StockInfo[] }> {
  const r = await fetch(`/api/stocks/search?q=${encodeURIComponent(q)}`, { cache: 'no-store' })
  return r.json()
}

// ---- 个股深度分析 ----
export interface AnalysisSeries {
  trade_date: string
  close: number | null
  pct_change: number | null
}

export interface StockAnalysisResult {
  ok: boolean
  error?: string
  model?: string
  report?: string
  file?: string
  code?: string
  name?: string | null
  created_at?: string | null
  markdown?: string
  data?: {
    stock: { code: string; name: string | null; industry: string | null; list_date: string | null; status: string | null }
    quote: Record<string, any>
    technical: Record<string, any>
    fundamentals: any[]
    moneyflow: Record<string, any>
    sectors: string[]
    market: Record<string, any>
    rps: Record<string, any>
    notes: string[]
    series: AnalysisSeries[]
  }
}

export interface AnalysisHistoryItem {
  file: string
  code: string
  name: string | null
  created_at: string | null
  model: string | null
  preview: string
}

export async function analyzeStock(q: string): Promise<StockAnalysisResult> {
  const r = await fetch('/api/analysis/stock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q }),
  })
  return r.json()
}

export async function fetchAnalysisHistory(): Promise<{ items: AnalysisHistoryItem[] }> {
  const r = await fetch('/api/analysis/history', { cache: 'no-store' })
  return r.json()
}

export async function fetchAnalysisResult(file: string): Promise<StockAnalysisResult> {
  const r = await fetch(`/api/analysis/result?file=${encodeURIComponent(file)}`, { cache: 'no-store' })
  return r.json()
}

export async function deleteAnalysis(file: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/analysis/result?file=${encodeURIComponent(file)}`, { method: 'DELETE' })
  return r.json()
}

export interface ToolDef {
  type: string
  function: { name: string; description: string; parameters: Record<string, unknown> }
}

export async function fetchTools(): Promise<{ names: string[]; tools: ToolDef[] }> {
  const r = await fetch('/api/tools')
  return r.json()
}

export async function callTool(name: string, args: Record<string, unknown>) {
  const r = await fetch('/api/tools/call', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, arguments: args }),
  })
  return r.json()
}

export interface DecideResult {
  ok: boolean
  conclusion?: string
  rounds?: number
  trace?: { round: number; tool: string; arguments: Record<string, unknown>; result: unknown }[]
  error?: string
}

export async function agentDecide(
  strategy: string,
  date?: string,
  prefer?: string,
): Promise<DecideResult> {
  const r = await fetch('/api/agent/decide', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy, date, prefer }),
  })
  return r.json()
}

export async function fetchBacktestResults(): Promise<{ results: any[] }> {
  const r = await fetch('/api/backtest/results')
  return r.json()
}

export async function fetchBacktestResult(file: string): Promise<any> {
  const r = await fetch(`/api/backtest/result?file=${encodeURIComponent(file)}`)
  return r.json()
}

// ---- 策略管理 ----
export interface StrategyConfig {
  version?: number
  timing?: { mode?: string; position_caps?: Record<string, number>; liquidate_on_bear?: boolean }
  position?: { max_total_pct?: number; max_single_pct?: number; max_holdings?: number; min_cash_pct?: number }
  risk?: { stop_loss_pct?: number | null }
  execution?: { decide_every?: number; order_price?: string }
}

export interface Strategy {
  id: string
  name: string
  text: string
  created_at?: string
  updated_at?: string
  config?: StrategyConfig
}

export async function fetchStrategies(): Promise<{ strategies: Strategy[] }> {
  const r = await fetch('/api/strategies')
  return r.json()
}

export async function createStrategy(name: string, text: string, config?: StrategyConfig): Promise<Strategy> {
  const r = await fetch('/api/strategies', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, text, config }),
  })
  return r.json()
}

export async function updateStrategy(id: string, name: string, text: string, config?: StrategyConfig): Promise<Strategy> {
  const r = await fetch(`/api/strategies/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, text, config }),
  })
  return r.json()
}

export async function deleteStrategy(id: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/strategies/${id}`, { method: 'DELETE' })
  return r.json()
}

export async function generateStrategy(idea: string): Promise<{ ok: boolean; strategy?: string; config?: StrategyConfig; error?: string }> {
  const r = await fetch('/api/strategy/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ idea }),
  })
  return r.json()
}

// ---- LLM 模型配置 ----
export interface LlmProvider {
  id: string
  name: string
  base_url: string
  model: string
  enabled: boolean
  has_key?: boolean
}

export async function fetchLlmConfig(): Promise<{
  providers: LlmProvider[]
  roles: Record<string, string>
  role_defs: { id: string; label: string }[]
}> {
  const r = await fetch('/api/llm/config')
  return r.json()
}

export async function addLlmProvider(p: { name: string; base_url: string; api_key: string; model: string }) {
  const r = await fetch('/api/llm/providers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  })
  return r.json()
}

export async function updateLlmProvider(id: string, fields: Record<string, unknown>) {
  const r = await fetch(`/api/llm/providers/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  })
  return r.json()
}

export async function deleteLlmProvider(id: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/llm/providers/${id}`, { method: 'DELETE' })
  return r.json()
}

export async function setLlmRole(role: string, providerId: string | null) {
  const r = await fetch('/api/llm/roles', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, provider_id: providerId }),
  })
  return r.json()
}

// ---- 回测启动 ----
export interface ReadinessRequirement {
  capability: string
  why?: string
  covered: boolean
}

export interface ReadinessReport {
  ready: boolean
  force?: boolean
  execution_plan?: string
  requirements?: ReadinessRequirement[]
  gaps?: { capability: string; why?: string }[]
  remedies?: { capability: string; why?: string; remedied: boolean; detail: string }[]
  rounds?: { ready: boolean; gaps: string[] }[]
}

export interface ReadinessResult {
  ok: boolean
  ready?: boolean
  readiness?: ReadinessReport
  markdown?: string
  error?: string
}

export async function checkReadiness(params: {
  strategy: string
  start: string
  end: string
  decide_every?: number
  stop_loss?: number
  initial_cash?: number
  strategy_name?: string
  config?: StrategyConfig
}): Promise<ReadinessResult> {
  const r = await fetch('/api/backtest/readiness', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  return r.json()
}

export async function startBacktest(params: {
  strategy: string
  start: string
  end: string
  decide_every?: number
  stop_loss?: number
  initial_cash?: number
  strategy_name?: string
  config?: StrategyConfig
  force?: boolean
}): Promise<{ task_id: string }> {
  const r = await fetch('/api/backtest/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  return r.json()
}

export async function fetchBacktestStatus(taskId: string): Promise<any> {
  const r = await fetch(`/api/backtest/status/${taskId}`)
  return r.json()
}

export async function fetchBacktestTasks(): Promise<{ tasks: any[] }> {
  const r = await fetch('/api/backtest/tasks')
  return r.json()
}

export async function generateReport(file: string, with_llm = true): Promise<any> {
  const r = await fetch('/api/backtest/report', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file, with_llm }),
  })
  return r.json()
}

export interface OptimizeResult {
  ok: boolean
  diagnosis?: string
  changes?: string[]
  strategy?: string
  config?: StrategyConfig | null
  file?: string
  params?: any
  strategy_name?: string
  error?: string
}

export async function optimizeStrategy(file: string, strategy?: string): Promise<OptimizeResult> {
  const r = await fetch('/api/backtest/optimize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file, strategy }),
  })
  return r.json()
}

// ---- 市场概览 + 数据更新 ----
export async function fetchMarketOverview(): Promise<any> {
  const r = await fetch('/api/market/overview', { cache: 'no-store' })
  return r.json()
}

export async function updateData(): Promise<any> {
  const r = await fetch('/api/data/update', { method: 'POST' })
  return r.json()
}

export interface DataUpdateStatus {
  scheduler: { enabled: boolean; running: boolean; last_run_ts: string | null; last_run_ok: boolean | null; last_error: string | null }
  last_update: any | null
  history: any[]
  backfill: any | null
  auto_update: boolean
  freshness: { latest_trade_date: string | null; stocks_on_latest_day: number; stocks_total: number; stale: boolean }
  source?: { active: string; akshare_installed: boolean; akshare_enabled: boolean }
}

export async function fetchDataUpdateStatus(): Promise<DataUpdateStatus> {
  const r = await fetch('/api/data/update/status')
  return r.json()
}

export async function triggerBackfill(force = false): Promise<any> {
  const r = await fetch('/api/data/backfill', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force }),
  })
  return r.json()
}

export async function fetchBackfillStatus(): Promise<any> {
  const r = await fetch('/api/data/backfill/status')
  return r.json()
}

export async function triggerMetaBackfill(): Promise<any> {
  const r = await fetch('/api/data/meta/backfill', { method: 'POST' })
  return r.json()
}

export async function fetchMarketKline(start: string, end: string): Promise<{ kline: any[] }> {
  const r = await fetch(`/api/market/kline?start=${start}&end=${end}`, { cache: 'no-store' })
  return r.json()
}

export async function fetchStockKline(code: string, start: string, end: string): Promise<{ kline: any[] }> {
  const r = await fetch(
    `/api/data/daily?code=${encodeURIComponent(code)}&start=${start}&end=${end}&limit=10000`,
    { cache: 'no-store' },
  )
  const d = await r.json()
  // daily 接口字段为 trade_date 且按日期倒序，统一为 K 线图所需的 date/升序格式
  const kline = (d.rows || [])
    .map((x: any) => ({
      date: x.trade_date,
      open: x.open,
      high: x.high,
      low: x.low,
      close: x.close,
      volume: x.volume,
    }))
    .reverse()
  return { kline }
}

export async function fetchStockNames(): Promise<{ names: Record<string, string> }> {
  const r = await fetch('/api/stocks/names')
  return r.json()
}

export async function fetchCustomTools(): Promise<{ tools: any[] }> {
  const r = await fetch('/api/tools/custom')
  return r.json()
}

export async function generateCustomTool(requirement: string): Promise<any> {
  const r = await fetch('/api/tools/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requirement }),
  })
  return r.json()
}

export async function deleteCustomTool(name: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/tools/custom/${name}`, { method: 'DELETE' })
  return r.json()
}

export async function stopBacktestTask(taskId: string): Promise<{ stopped: boolean }> {
  const r = await fetch(`/api/backtest/tasks/${taskId}/stop`, { method: 'POST' })
  return r.json()
}

export async function deleteBacktestTask(taskId: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/backtest/tasks/${taskId}`, { method: 'DELETE' })
  return r.json()
}

// ---- 知识中心 ----
export interface KnowledgeNode {
  id: string
  title: string
  summary?: string | null
  content?: string | null
  category?: string | null
  subcategory?: string | null
  knowledge_type?: string | null
  style?: string | null
  market?: string | null
  regime?: string | null
  source?: string | null
  source_name?: string | null
  source_url?: string | null
  author?: string | null
  tags?: string[]
  difficulty?: string | null
  authority?: string | null
  status?: string | null
  created_by?: string | null
  version?: number | null
  review_note?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface KnowledgeTaxonomy {
  domains: Record<string, string[]>
  styles: string[]
  knowledge_types: string[]
  markets: string[]
  regimes: string[]
  difficulties: string[]
  sources: string[]
  authorities: string[]
  statuses: string[]
}

export interface KnowledgeDimensions {
  domains: { value: string; count: number }[]
  subcategories: { domain: string; items: { value: string; count: number }[] }[]
  knowledge_types: { value: string; count: number }[]
  styles: { value: string; count: number }[]
  markets: { value: string; count: number }[]
  regimes: { value: string; count: number }[]
  sources: { value: string; count: number }[]
  authorities: { value: string; count: number }[]
  difficulties: { value: string; count: number }[]
  tags: { value: string; count: number }[]
  total: number
}

export async function fetchKnowledgeNodes(filters: {
  category?: string
  subcategory?: string
  knowledge_type?: string
  style?: string
  market?: string
  regime?: string
  source?: string
  authority?: string
  status?: string
  tag?: string
  q?: string
} = {}): Promise<{ nodes: KnowledgeNode[] }> {
  const params = new URLSearchParams()
  const keys: (keyof typeof filters)[] = ['category', 'subcategory', 'knowledge_type', 'style', 'market', 'regime', 'source', 'authority', 'status', 'tag', 'q']
  for (const k of keys) {
    const v = filters[k]
    if (v) params.set(k, v)
  }
  const qs = params.toString()
  const r = await fetch(`/api/knowledge${qs ? '?' + qs : ''}`, { cache: 'no-store' })
  return r.json()
}

export async function fetchKnowledgeTaxonomy(): Promise<KnowledgeTaxonomy> {
  const r = await fetch('/api/knowledge/taxonomy', { cache: 'no-store' })
  return r.json()
}

export async function fetchKnowledgeDimensions(): Promise<KnowledgeDimensions> {
  const r = await fetch('/api/knowledge/dimensions', { cache: 'no-store' })
  return r.json()
}

export async function fetchKnowledgeGraph(): Promise<{
  categories: { name: string }[]
  nodes: any[]
  links: any[]
}> {
  const r = await fetch('/api/knowledge/graph', { cache: 'no-store' })
  return r.json()
}

export async function fetchKnowledgeMindmap(by: 'domain' | 'style' | 'source' | 'knowledge_type'): Promise<{
  by: string
  label: string
  tree: any
}> {
  const r = await fetch(`/api/knowledge/mindmap?by=${by}`, { cache: 'no-store' })
  return r.json()
}

export async function fetchKnowledgeNode(id: string): Promise<KnowledgeNode> {
  const r = await fetch(`/api/knowledge/${id}`, { cache: 'no-store' })
  return r.json()
}

export async function createKnowledge(fields: Partial<KnowledgeNode> & { title: string }): Promise<KnowledgeNode> {
  const r = await fetch('/api/knowledge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  })
  return r.json()
}

export async function updateKnowledge(id: string, fields: Partial<KnowledgeNode>): Promise<KnowledgeNode> {
  const r = await fetch(`/api/knowledge/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  })
  return r.json()
}

export async function deleteKnowledge(id: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/knowledge/${id}`, { method: 'DELETE' })
  return r.json()
}

export async function ingestKnowledge(text?: string, url?: string): Promise<{
  ok: boolean
  node?: KnowledgeNode
  duplicate?: boolean
  existing?: KnowledgeNode
  error?: string
}> {
  const r = await fetch('/api/knowledge/ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, url }),
  })
  return r.json()
}

// ---- 交易分析中心 ----
export interface TradingMarketContext {
  clock: { beijing_time: string; weekday: number; is_trading: boolean; market_open: boolean; session: string }
  latest_trade_date: string | null
  regime: string | null
  index_close: number | null
  ma20: number | null
  ma60: number | null
  snapshot: {
    total: number
    up: number
    down: number
    limit_up: number
    limit_down: number
    avg_pct: number
    total_amount: number
  } | null
  live_index: Record<string, { name: string; symbol: string; price: number; prev_close: number; pct_change: number }>
  data_mode: string
  notes: string[]
}

export interface Position {
  id: string
  code: string
  name: string | null
  quantity: number
  cost_price: number
  updated_at?: string
}

export interface Account {
  principal: number | null
  available_cash: number | null
  updated_at?: string
}

export interface PortfolioOverview {
  principal: number | null
  available_cash: number | null
  positions_value: number | null
  total_assets: number | null
  position_ratio_pct: number | null
  cash_ratio_pct: number | null
  total_pnl: number | null
  total_pnl_pct: number | null
}

export interface PortfolioSnapshot {
  clock: { beijing_time: string; weekday: number; is_trading: boolean; market_open: boolean; session: string }
  positions: any[]
  account: Account
  overview: PortfolioOverview
}

export interface TradingAdviceItem {
  file: string
  created_at: string | null
  strategy_name: string | null
  mode: string
  mode_label: string
  model: string | null
  n_candidates: number
  n_positions: number
  preview: string
}

export interface TradingAdviceResult {
  ok: boolean
  error?: string
  file?: string
  model?: string
  strategy_id?: string
  strategy_name?: string
  mode?: string
  created_at?: string
  market?: TradingMarketContext
  candidates?: any[]
  positions?: any[]
  account?: Account
  portfolio_overview?: PortfolioOverview
  notes?: string
  pick_trace?: { tool: string; arguments: Record<string, unknown>; result: unknown }[]
  report?: string
  markdown?: string
}

export async function fetchTradingMarket(): Promise<TradingMarketContext> {
  const r = await fetch('/api/trading/market', { cache: 'no-store' })
  return r.json()
}

export async function fetchPositions(): Promise<{ positions: Position[]; account: Account }> {
  const r = await fetch('/api/trading/positions', { cache: 'no-store' })
  return r.json()
}

export async function fetchPortfolio(): Promise<PortfolioSnapshot> {
  const r = await fetch('/api/trading/portfolio', { cache: 'no-store' })
  return r.json()
}

export async function updateAccount(p: { principal?: number | null; available_cash?: number | null }): Promise<{ ok: boolean; error?: string; account?: Account }> {
  const r = await fetch('/api/trading/account', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  })
  return r.json()
}

export async function upsertPosition(p: { code: string; name?: string; quantity: number; cost_price: number }): Promise<{ ok: boolean; error?: string; position?: Position }> {
  const r = await fetch('/api/trading/positions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  })
  return r.json()
}

export async function deletePosition(id: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/trading/positions/${id}`, { method: 'DELETE' })
  return r.json()
}

export async function runTradingAdvice(params: { strategy_id: string; mode: string; scope?: string; notes?: string }): Promise<TradingAdviceResult> {
  const r = await fetch('/api/trading/advice', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  return r.json()
}

export async function fetchAdviceHistory(): Promise<{ items: TradingAdviceItem[] }> {
  const r = await fetch('/api/trading/advice/history', { cache: 'no-store' })
  return r.json()
}

export async function fetchAdviceResult(file: string): Promise<TradingAdviceResult> {
  const r = await fetch(`/api/trading/advice/result?file=${encodeURIComponent(file)}`, { cache: 'no-store' })
  return r.json()
}

export async function deleteAdvice(file: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/trading/advice/result?file=${encodeURIComponent(file)}`, { method: 'DELETE' })
  return r.json()
}
