"""知识中心存储：DuckDB 知识库（知识节点表 + 派生维度/关系边）。

- 知识节点是唯一事实源，维度（一级领域/二级子类/风格/来源/标签…）以字段存在。
- 图谱的「知识→维度」边在读取时按需派生；检索（RAG）基于领域关键词 + 标签匹配。
- 知识库使用独立连接锁（不共用数据湖 lake 的全局锁），避免数据回填等长任务阻塞知识读取。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import Counter

import duckdb

from ..core import config
from . import taxonomy

KNOWLEDGE_DB = config.DATA_DIR / "knowledge" / "knowledge.duckdb"

# 独立连接锁：知识库与市场数据湖（market.duckdb）是不同文件，各自独立锁即可。
# 若复用 lake 的全局锁，数据回填（后台长任务）长时间持有锁时会把知识读取一起阻塞。
_knowledge_lock = threading.Lock()

# 管理元数据枚举（从 taxonomy 引入，便于 store 内引用）
CATEGORIES = taxonomy.DOMAIN_NAMES          # 一级领域
SUBCATEGORIES = taxonomy.all_subcategories()
STYLES = taxonomy.STYLES
KNOWLEDGE_TYPES = taxonomy.KNOWLEDGE_TYPES
MARKETS = taxonomy.MARKETS
REGIMES = taxonomy.REGIMES
DIFFICULTIES = taxonomy.DIFFICULTIES
SOURCES = taxonomy.SOURCES
AUTHORITY_LEVELS = taxonomy.AUTHORITY_LEVELS
STATUSES = taxonomy.STATUSES

DDL = """
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id             VARCHAR PRIMARY KEY,
    title          VARCHAR NOT NULL,
    summary        VARCHAR,
    content        VARCHAR,
    category       VARCHAR,          -- 一级领域（知识领域）
    subcategory    VARCHAR,          -- 二级子类
    knowledge_type VARCHAR,          -- 知识类型（概念理论/方法规则/…）
    style          VARCHAR,          -- 交易风格
    market         VARCHAR,          -- 适用市场
    regime         VARCHAR,          -- 适用行情
    source         VARCHAR,          -- 来源类型
    source_name    VARCHAR,          -- 具体来源名
    source_url     VARCHAR,
    author         VARCHAR,
    tags           VARCHAR,          -- JSON 数组字符串
    difficulty     VARCHAR,
    authority      VARCHAR,          -- 权威性
    status         VARCHAR DEFAULT '已收录',
    created_by     VARCHAR,          -- seed/user/ingest_url/ingest_text
    version        INTEGER DEFAULT 1,
    review_note    VARCHAR,
    created_at     VARCHAR,
    updated_at     VARCHAR
);
"""

# 旧库升级需新增的列（相对最早版本 schema）
_NEW_COLUMNS = {
    "subcategory": "VARCHAR",
    "knowledge_type": "VARCHAR",
    "market": "VARCHAR",
    "regime": "VARCHAR",
    "authority": "VARCHAR",
    "created_by": "VARCHAR",
    "version": "INTEGER",
    "review_note": "VARCHAR",
}

_COLS = [
    "id", "title", "summary", "content", "category", "subcategory",
    "knowledge_type", "style", "market", "regime", "source", "source_name",
    "source_url", "author", "tags", "difficulty", "authority", "status",
    "created_by", "version", "review_note", "created_at", "updated_at",
]


class _KnowledgeConn:
    """包装 DuckDB 连接：close() 时释放知识库独立锁，其余透传。"""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def close(self) -> None:
        try:
            self._conn.close()
        finally:
            _knowledge_lock.release()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def _conn():
    """打开知识库连接（读写模式，知识库独立互斥锁，用毕 close）。"""
    KNOWLEDGE_DB.parent.mkdir(parents=True, exist_ok=True)
    _knowledge_lock.acquire()
    try:
        conn = duckdb.connect(str(KNOWLEDGE_DB), read_only=False)
    except Exception:
        _knowledge_lock.release()
        raise
    return _KnowledgeConn(conn)


def _ensure_columns(conn) -> None:
    """兼容旧库：补齐缺失的新列（幂等）。"""
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='knowledge_nodes'"
    ).fetchall()
    existing = {r[0] for r in rows}
    for col, col_type in _NEW_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE knowledge_nodes ADD COLUMN {col} {col_type}")


def init_knowledge_db() -> None:
    conn = _conn()
    try:
        conn.execute(DDL)
        _ensure_columns(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------

def _load_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def _dump_tags(tags: list[str] | None) -> str:
    tags = [str(t).strip() for t in (tags or []) if t and str(t).strip()]
    return json.dumps(tags, ensure_ascii=False)


def _row_to_dict(row: tuple) -> dict:
    d = dict(zip(_COLS, row))
    d["tags"] = _load_tags(d.get("tags"))
    return d


def _normalize(kwargs: dict) -> dict:
    """规范化字段：空字符串→None，tags→JSON 字符串。"""
    out: dict = {}
    for k, v in kwargs.items():
        if k == "tags":
            out[k] = _dump_tags(v if isinstance(v, list) else [v] if v else [])
        elif isinstance(v, str) and v.strip() == "":
            out[k] = None
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_nodes(
    category: str | None = None,
    subcategory: str | None = None,
    knowledge_type: str | None = None,
    style: str | None = None,
    market: str | None = None,
    regime: str | None = None,
    source: str | None = None,
    authority: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    q: str | None = None,
) -> list[dict]:
    conn = _conn()
    try:
        where: list[str] = []
        params: list = []
        for col, val in (
            ("category", category), ("subcategory", subcategory),
            ("knowledge_type", knowledge_type), ("style", style),
            ("market", market), ("regime", regime), ("source", source),
            ("authority", authority), ("status", status),
        ):
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        if q and q.strip():
            like = f"%{q.strip()}%"
            where.append("(title LIKE ? OR summary LIKE ? OR content LIKE ? OR source_name LIKE ? OR author LIKE ?)")
            params += [like, like, like, like, like]
        sql = "SELECT " + ", ".join(_COLS) + " FROM knowledge_nodes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, id"
        nodes = [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        if tag and tag.strip():
            t = tag.strip()
            nodes = [n for n in nodes if t in n.get("tags", [])]
        return nodes
    finally:
        conn.close()


def get_node(nid: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(_COLS) + " FROM knowledge_nodes WHERE id = ?", [nid]
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def create_node(
    title: str,
    summary: str | None = None,
    content: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    knowledge_type: str | None = None,
    style: str | None = None,
    market: str | None = None,
    regime: str | None = None,
    source: str | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    author: str | None = None,
    tags: list[str] | None = None,
    difficulty: str | None = None,
    authority: str | None = None,
    status: str = "已收录",
    created_by: str = "user",
    review_note: str | None = None,
) -> dict:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    node = _normalize({
        "id": uuid.uuid4().hex[:10],
        "title": title,
        "summary": summary,
        "content": content,
        "category": taxonomy.canonical(category, CATEGORIES, "综合") if category else "综合",
        "subcategory": subcategory,
        "knowledge_type": taxonomy.canonical(knowledge_type, KNOWLEDGE_TYPES, "方法规则"),
        "style": taxonomy.canonical(style, STYLES, "混合"),
        "market": taxonomy.canonical(market, MARKETS, "通用"),
        "regime": taxonomy.canonical(regime, REGIMES, "通用"),
        "source": taxonomy.canonical(source, SOURCES, "用户录入"),
        "source_name": source_name,
        "source_url": source_url,
        "author": author,
        "tags": tags or [],
        "difficulty": taxonomy.canonical(difficulty, DIFFICULTIES, "入门"),
        "authority": taxonomy.canonical(authority, AUTHORITY_LEVELS, "待核实"),
        "status": status if status in STATUSES else "已收录",
        "created_by": created_by if created_by in taxonomy.CREATED_BY else "user",
        "version": 1,
        "review_note": review_note,
        "created_at": now,
        "updated_at": now,
    })
    cols = list(node.keys())
    placeholders = ", ".join(["?"] * len(cols))
    conn = _conn()
    try:
        conn.execute(
            f"INSERT INTO knowledge_nodes ({', '.join(cols)}) VALUES ({placeholders})",
            [node[c] for c in cols],
        )
    finally:
        conn.close()
    return get_node(node["id"]) or node


def update_node(nid: str, **fields) -> dict | None:
    if not fields:
        return get_node(nid)
    # 对枚举字段做规约
    if "category" in fields and fields["category"]:
        fields["category"] = taxonomy.canonical(fields["category"], CATEGORIES, "综合")
    if "knowledge_type" in fields and fields["knowledge_type"]:
        fields["knowledge_type"] = taxonomy.canonical(fields["knowledge_type"], KNOWLEDGE_TYPES, "方法规则")
    if "style" in fields and fields["style"]:
        fields["style"] = taxonomy.canonical(fields["style"], STYLES, "混合")
    if "authority" in fields and fields["authority"]:
        fields["authority"] = taxonomy.canonical(fields["authority"], AUTHORITY_LEVELS, "待核实")
    fields = _normalize(fields)
    fields["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # 版本号递增
    if "version" not in fields:
        cur = get_node(nid)
        fields["version"] = int(cur.get("version") or 0) + 1 if cur else 1
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn = _conn()
    try:
        conn.execute(f"UPDATE knowledge_nodes SET {sets} WHERE id = ?", [*fields.values(), nid])
    finally:
        conn.close()
    return get_node(nid)


def delete_node(nid: str) -> bool:
    conn = _conn()
    try:
        conn.execute("DELETE FROM knowledge_nodes WHERE id = ?", [nid])
    finally:
        conn.close()
    return get_node(nid) is None


def find_by_title_or_url(title: str | None, url: str | None) -> dict | None:
    """去重：按标题精确匹配或来源 URL 匹配。"""
    if not title and not url:
        return None
    conn = _conn()
    try:
        if url and url.strip():
            row = conn.execute(
                "SELECT " + ", ".join(_COLS) + " FROM knowledge_nodes WHERE source_url = ? LIMIT 1",
                [url.strip()],
            ).fetchone()
            if row:
                return _row_to_dict(row)
        if title and title.strip():
            row = conn.execute(
                "SELECT " + ", ".join(_COLS) + " FROM knowledge_nodes WHERE title = ? LIMIT 1",
                [title.strip()],
            ).fetchone()
            if row:
                return _row_to_dict(row)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 种子同步（幂等：按标题 upsert，旧库增量迁移到新分类体系）
# ---------------------------------------------------------------------------

def sync_seed() -> dict:
    """把内置种子知识库写入/升级到最新分类体系，返回 {inserted, updated}。"""
    init_knowledge_db()
    from . import seed

    inserted, updated = 0, 0
    conn = _conn()
    try:
        for item in seed.SEED_KNOWLEDGE:
            title = item["title"]
            existing = conn.execute(
                "SELECT id FROM knowledge_nodes WHERE title = ? LIMIT 1", [title]
            ).fetchone()
            fields = _normalize({
                **item,
                "category": taxonomy.canonical(item.get("category"), CATEGORIES, "综合"),
                "subcategory": item.get("subcategory"),
                "knowledge_type": taxonomy.canonical(item.get("knowledge_type"), KNOWLEDGE_TYPES, "方法规则"),
                "style": taxonomy.canonical(item.get("style"), STYLES, "混合"),
                "market": taxonomy.canonical(item.get("market"), MARKETS, "通用"),
                "regime": taxonomy.canonical(item.get("regime"), REGIMES, "通用"),
                "source": taxonomy.canonical(item.get("source"), SOURCES, "用户录入"),
                "difficulty": taxonomy.canonical(item.get("difficulty"), DIFFICULTIES, "入门"),
                "authority": taxonomy.canonical(item.get("authority"), AUTHORITY_LEVELS, "待核实"),
                "created_by": "seed",
                "version": 1,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            if existing:
                cols = list(fields.keys())
                sets = ", ".join(f"{c} = ?" for c in cols)
                conn.execute(
                    f"UPDATE knowledge_nodes SET {sets} WHERE id = ?",
                    [*[fields[c] for c in cols], existing[0]],
                )
                updated += 1
            else:
                fields["id"] = uuid.uuid4().hex[:10]
                fields["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                cols = list(fields.keys())
                ph = ", ".join(["?"] * len(cols))
                conn.execute(
                    f"INSERT INTO knowledge_nodes ({', '.join(cols)}) VALUES ({ph})",
                    [fields[c] for c in cols],
                )
                inserted += 1
        # 非种子行（用户录入）缺失的新字段给默认值
        conn.execute(
            "UPDATE knowledge_nodes SET category='综合' WHERE category IS NULL OR category=''"
        )
        conn.execute(
            "UPDATE knowledge_nodes SET subcategory='未分类' WHERE subcategory IS NULL OR subcategory=''"
        )
        conn.execute(
            "UPDATE knowledge_nodes SET knowledge_type='方法规则' WHERE knowledge_type IS NULL OR knowledge_type=''"
        )
        conn.execute(
            "UPDATE knowledge_nodes SET market='通用' WHERE market IS NULL OR market=''"
        )
        conn.execute(
            "UPDATE knowledge_nodes SET regime='通用' WHERE regime IS NULL OR regime=''"
        )
        conn.execute(
            "UPDATE knowledge_nodes SET authority='待核实' WHERE authority IS NULL OR authority=''"
        )
        conn.execute(
            "UPDATE knowledge_nodes SET created_by='user' WHERE created_by IS NULL OR created_by=''"
        )
        conn.execute("UPDATE knowledge_nodes SET version=1 WHERE version IS NULL")
    finally:
        conn.close()
    return {"inserted": inserted, "updated": updated}


# ---------------------------------------------------------------------------
# 检索（RAG：供策略生成/优化注入知识）
# ---------------------------------------------------------------------------

def retrieve_knowledge(text: str, top_k: int = 5, domains: list[str] | None = None) -> list[dict]:
    """从知识库检索与文本相关的知识（领域关键词 + 标签 + 标题匹配打分）。"""
    if not (text or "").strip():
        return []
    text = text.strip()
    detected = domains or taxonomy.detect_domains(text)
    nodes = list_nodes()
    scored: list[tuple[int, dict]] = []
    for n in nodes:
        score = 0
        cat = n.get("category") or ""
        if cat in detected:
            score += 5
        # 标签命中
        for t in n.get("tags", []):
            if t and t in text:
                score += 3
        # 标题命中
        title = (n.get("title") or "").replace(" ", "")
        if title and len(title) >= 2 and title in text.replace(" ", ""):
            score += 4
        # 领域关键词命中子类/摘要
        sub = n.get("subcategory") or ""
        if sub and sub in text:
            score += 2
        if score > 0:
            scored.append((score, n))
    scored.sort(key=lambda x: -x[0])
    return [n for _, n in scored[:top_k]]


def knowledge_context(text: str, top_k: int = 3, domains: list[str] | None = None) -> tuple[str, list[dict]]:
    """构建注入到策略生成/优化提示词的「参考知识」文本块，并返回引用的知识列表。"""
    items = retrieve_knowledge(text, top_k=top_k, domains=domains)
    if not items:
        return "", []
    lines = ["【参考知识（来自知识中心，请参考其中的方法与量化标准，使策略更专业、可量化）】"]
    for i, n in enumerate(items, 1):
        head = f"{i}. 【{n.get('category')}·{n.get('subcategory') or '综合'}】{n.get('title')}"
        if n.get("authority"):
            head += f"（{n.get('authority')}）"
        lines.append(head)
        if n.get("summary"):
            lines.append(f"   {n.get('summary')}")
    return "\n".join(lines), items


# ---------------------------------------------------------------------------
# 维度统计 / 分类体系导出
# ---------------------------------------------------------------------------

def taxonomy_tree() -> dict:
    """导出完整分类体系（供前端分级树与下拉）。"""
    return {
        "domains": taxonomy.DOMAINS,
        "styles": taxonomy.STYLES,
        "knowledge_types": taxonomy.KNOWLEDGE_TYPES,
        "markets": taxonomy.MARKETS,
        "regimes": taxonomy.REGIMES,
        "difficulties": taxonomy.DIFFICULTIES,
        "sources": taxonomy.SOURCES,
        "authorities": taxonomy.AUTHORITY_LEVELS,
        "statuses": taxonomy.STATUSES,
    }


def dimensions() -> dict:
    """各维度取值与计数（供前端筛选器）。"""
    nodes = list_nodes()
    sub_map: dict[str, int] = {}
    for n in nodes:
        d = n.get("category") or "综合"
        s = n.get("subcategory") or "未分类"
        sub_map.setdefault(d, Counter())[s] += 1
    return {
        "domains": _counts(nodes, "category", taxonomy.DOMAIN_NAMES),
        "subcategories": [{"domain": d, "items": [{"value": s, "count": c} for s, c in cnt.most_common()]} for d, cnt in sub_map.items()],
        "knowledge_types": _counts(nodes, "knowledge_type", taxonomy.KNOWLEDGE_TYPES),
        "styles": _counts(nodes, "style", taxonomy.STYLES),
        "markets": _counts(nodes, "market", taxonomy.MARKETS),
        "regimes": _counts(nodes, "regime", taxonomy.REGIMES),
        "sources": _counts(nodes, "source", taxonomy.SOURCES),
        "authorities": _counts(nodes, "authority", taxonomy.AUTHORITY_LEVELS),
        "difficulties": _counts(nodes, "difficulty", taxonomy.DIFFICULTIES),
        "tags": _tag_counts(nodes),
        "total": len(nodes),
    }


def _counts(nodes: list[dict], key: str, order: list[str]) -> list[dict]:
    c = Counter(n.get(key) or "未分类" for n in nodes)
    ordered = [k for k in order if k in c] + [k for k in c if k not in order]
    return [{"value": k, "count": c[k]} for k in ordered]


def _tag_counts(nodes: list[dict]) -> list[dict]:
    c: Counter = Counter()
    for n in nodes:
        for t in n.get("tags", []):
            c[t] += 1
    return [{"value": k, "count": v} for k, v in c.most_common(60)]


# ---------------------------------------------------------------------------
# 知识图谱 / 思维导图 数据组装
# ---------------------------------------------------------------------------

def build_graph() -> dict:
    """组装 ECharts `graph` 数据：知识节点 + 维度 hub（领域/风格/来源）+ 相关边。"""
    nodes = list_nodes()
    cat_list = [c for c in taxonomy.DOMAIN_NAMES if any(n.get("category") == c for n in nodes)]
    cat_list += [c for c in sorted({n.get("category") for n in nodes if n.get("category")}) if c not in cat_list]
    if "综合" not in cat_list:
        cat_list.append("综合")
    categories = [{"name": c} for c in cat_list] + [{"name": "维度"}]
    cat_index = {c: i for i, c in enumerate(cat_list)}
    dim_index = len(cat_list)

    graph_nodes: list[dict] = []
    graph_links: list[dict] = []
    seen_hubs: set[str] = set()

    def hub(kind: str, label: str) -> str:
        hub_id = f"{kind}:{label}"
        if hub_id not in seen_hubs:
            seen_hubs.add(hub_id)
            graph_nodes.append({
                "id": hub_id, "name": label, "category": dim_index,
                "value": 0, "type": kind, "symbolSize": 34,
            })
        return hub_id

    by_id: dict[str, dict] = {}
    for n in nodes:
        nid = n["id"]
        by_id[nid] = n
        cat = n.get("category") or "综合"
        graph_nodes.append({
            "id": nid, "name": n["title"],
            "category": cat_index.get(cat, cat_index["综合"]),
            "value": 1, "type": "knowledge", "symbolSize": 22,
            "summary": n.get("summary") or "",
            "categoryLabel": cat,
            "subcategoryLabel": n.get("subcategory") or "",
            "styleLabel": n.get("style") or "",
            "sourceLabel": n.get("source_name") or n.get("source") or "",
        })
        if cat:
            graph_links.append({"source": nid, "target": hub("cat", cat), "relation": "属于"})
        if n.get("subcategory"):
            graph_links.append({"source": nid, "target": hub("sub", n["subcategory"]), "relation": "子类"})
        if n.get("style"):
            graph_links.append({"source": nid, "target": hub("style", n["style"]), "relation": "风格"})
        if n.get("source"):
            graph_links.append({"source": nid, "target": hub("source", n["source"]), "relation": "来源"})

    # 标签相关边
    related_pairs: list[tuple[str, str, int]] = []
    ids = list(by_id.keys())
    for i in range(len(ids)):
        ti = set(by_id[ids[i]].get("tags", []))
        if not ti:
            continue
        for j in range(i + 1, len(ids)):
            overlap = len(ti & set(by_id[ids[j]].get("tags", [])))
            if overlap >= 1:
                related_pairs.append((ids[i], ids[j], overlap))
    related_pairs.sort(key=lambda x: -x[2])
    for a, b, w in related_pairs[:120]:
        graph_links.append({"source": a, "target": b, "relation": "相关", "weight": w})

    return {"categories": categories, "nodes": graph_nodes, "links": graph_links}


def build_mindmap(by: str = "domain") -> dict:
    """组装 ECharts `tree` 数据。

    by=domain：一级领域 → 二级子类 → 知识（三层，体现分类体系）
    其它：by 维度 → 知识（两层）。
    """
    nodes = list_nodes()
    label_map = {"domain": "知识领域", "style": "交易风格", "source": "来源", "knowledge_type": "知识类型"}
    label = label_map.get(by, "知识领域")

    if by == "domain":
        tree_children = []
        for domain in taxonomy.DOMAIN_NAMES:
            sub_groups: dict[str, list[dict]] = {}
            for n in nodes:
                if n.get("category") == domain:
                    sub = n.get("subcategory") or "未分类"
                    sub_groups.setdefault(sub, []).append({"name": n["title"], "id": n["id"]})
            subs = [{"name": s, "children": sorted(v, key=lambda x: x["name"])} for s, v in sub_groups.items()]
            if subs:
                tree_children.append({"name": domain, "children": subs})
        return {"by": by, "label": label, "tree": {"name": "知识中心", "children": tree_children}}

    if by == "style":
        key = "style"
    elif by == "source":
        key = "source"
    else:
        key = "knowledge_type"
    groups: dict[str, list[dict]] = {}
    for n in nodes:
        k = n.get(key) or "未分类"
        groups.setdefault(k, []).append({"name": n["title"], "id": n["id"]})
    children = [{"name": k, "children": sorted(v, key=lambda x: x["name"])} for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    return {"by": by, "label": label, "tree": {"name": "知识中心", "children": children}}
