"""知识中心 API 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..knowledge import ingest, store

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeCreate(BaseModel):
    title: str
    summary: str | None = None
    content: str | None = None
    category: str | None = None
    subcategory: str | None = None
    knowledge_type: str | None = None
    style: str | None = None
    market: str | None = None
    regime: str | None = None
    source: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    author: str | None = None
    tags: list[str] | None = None
    difficulty: str | None = None
    authority: str | None = None
    status: str | None = None
    review_note: str | None = None


class KnowledgeUpdate(BaseModel):
    title: str | None = None
    summary: str | None = None
    content: str | None = None
    category: str | None = None
    subcategory: str | None = None
    knowledge_type: str | None = None
    style: str | None = None
    market: str | None = None
    regime: str | None = None
    source: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    author: str | None = None
    tags: list[str] | None = None
    difficulty: str | None = None
    authority: str | None = None
    status: str | None = None
    review_note: str | None = None


class IngestRequest(BaseModel):
    text: str | None = None
    url: str | None = None


@router.get("/taxonomy")
def knowledge_taxonomy() -> dict:
    """导出完整分类体系（供前端分级树与下拉）。"""
    return store.taxonomy_tree()


@router.get("")
def list_knowledge(
    category: str | None = Query(None),
    subcategory: str | None = Query(None),
    knowledge_type: str | None = Query(None),
    style: str | None = Query(None),
    market: str | None = Query(None),
    regime: str | None = Query(None),
    source: str | None = Query(None),
    authority: str | None = Query(None),
    status: str | None = Query(None),
    tag: str | None = Query(None),
    q: str | None = Query(None),
) -> dict:
    return {"nodes": store.list_nodes(category, subcategory, knowledge_type, style, market, regime, source, authority, status, tag, q)}


@router.get("/dimensions")
def knowledge_dimensions() -> dict:
    return store.dimensions()


@router.get("/graph")
def knowledge_graph() -> dict:
    return store.build_graph()


@router.get("/mindmap")
def knowledge_mindmap(by: str = Query("domain", pattern="^(domain|style|source|knowledge_type)$")) -> dict:
    return store.build_mindmap(by)


@router.get("/{nid}")
def knowledge_get(nid: str) -> dict:
    node = store.get_node(nid)
    if node is None:
        return {"error": "知识不存在"}
    return node


@router.post("")
def knowledge_create(req: KnowledgeCreate) -> dict:
    if not (req.title or "").strip():
        return {"error": "标题不能为空"}
    return store.create_node(
        title=req.title.strip(),
        summary=req.summary,
        content=req.content,
        category=req.category,
        subcategory=req.subcategory,
        knowledge_type=req.knowledge_type,
        style=req.style,
        market=req.market,
        regime=req.regime,
        source=req.source,
        source_name=req.source_name,
        source_url=req.source_url,
        author=req.author,
        tags=req.tags,
        difficulty=req.difficulty,
        authority=req.authority,
        status=req.status or "已收录",
        created_by="user",
        review_note=req.review_note,
    )


@router.put("/{nid}")
def knowledge_update(nid: str, req: KnowledgeUpdate) -> dict:
    fields = {k: v for k, v in req.dict().items() if v is not None}
    node = store.update_node(nid, **fields)
    return {"error": "知识不存在"} if node is None else node


@router.delete("/{nid}")
def knowledge_delete(nid: str) -> dict:
    return {"deleted": store.delete_node(nid)}


@router.post("/ingest")
def knowledge_ingest(req: IngestRequest) -> dict:
    return ingest.ingest(req.text, req.url)
