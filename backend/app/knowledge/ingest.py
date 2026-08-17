"""知识「吸收」管道：URL 抓取 + 正文抽取 + LLM 结构化（映射到分类体系）+ 去重落库。"""
from __future__ import annotations

import json
import re

import httpx

from ..core import config
from ..llm.gateway import LLMGateway
from . import store, taxonomy

MAX_BYTES = 2 * 1024 * 1024  # 网页正文上限 2MB
MAX_TEXT = 8000  # 交给 LLM 的正文字符上限

_STRUCTURE_PROMPT = """你是股票交易知识整理助手。请把下面一段「股票交易相关的文本」整理成结构化知识卡片，
只输出一个 JSON 对象（不要 markdown 代码块、不要输出任何其它文字），字段如下：

{{
  "title": "一句话标题（≤30字，精炼）",
  "summary": "一句话摘要（≤80字，概括核心要点）",
  "content": "用中文 Markdown 重写的知识正文：提炼核心要点、量化标准与操作规则，用要点/小标题组织，300~800字",
  "category": "一级领域，从下面选一个：{domains}",
  "subcategory": "二级子类，须是 category 对应领域下的子类（见下），不确定填「未分类」",
  "knowledge_type": "知识类型，从下面选一个：{knowledge_types}",
  "style": "交易风格，从下面选一个：{styles}",
  "market": "适用市场，从下面选一个：{markets}",
  "regime": "适用行情，从下面选一个：{regimes}",
  "source": "来源类型，从下面选一个：{sources}",
  "source_name": "具体来源名（如《海龟交易法则》或网站名，未知可空字符串）",
  "author": "作者/出处（未知可空字符串）",
  "tags": ["3~6 个中文标签词"],
  "difficulty": "入门/进阶/高级 三选一",
  "authority": "权威性评级，从下面选一个：{authorities}（经典书籍/投资大师一般为「权威」，学术/专业研报一般为「较权威」，社区/自媒体一般为「一般」或「待核实」）"
}}

一级领域与二级子类对照：
{domain_subcategories}

待整理文本：
{text}
"""


def _build_prompt(text: str) -> str:
    sub_lines = []
    for domain, subs in taxonomy.DOMAINS.items():
        sub_lines.append(f"- {domain}：{'/'.join(subs)}")
    return _STRUCTURE_PROMPT.format(
        domains="/".join(taxonomy.DOMAIN_NAMES),
        knowledge_types="/".join(taxonomy.KNOWLEDGE_TYPES),
        styles="/".join(taxonomy.STYLES),
        markets="/".join(taxonomy.MARKETS),
        regimes="/".join(taxonomy.REGIMES),
        sources="/".join(taxonomy.SOURCES),
        authorities="/".join(taxonomy.AUTHORITY_LEVELS),
        domain_subcategories="\n".join(sub_lines),
        text=text,
    )


def _parse_json(text: str) -> dict:
    """从 LLM 输出中稳健地提取 JSON 对象（容忍推理模型夹带的说明文字）。"""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    raise ValueError("LLM 输出中未找到合法 JSON 对象")


def _subcategory_of(domain: str | None, sub: str | None) -> str | None:
    """把 subcategory 规约到其领域下的合法子类。"""
    if not domain or domain not in taxonomy.DOMAINS:
        return None
    allowed = taxonomy.DOMAINS[domain]
    if not sub:
        return None
    return taxonomy.canonical(sub, allowed, allowed[0] if allowed else "未分类")


def fetch_url(url: str) -> dict:
    """抓取网页并抽取标题+正文。直接访问失败时用代理兜底。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    attempts: list[dict | None] = [None]
    if config.PROXY:
        attempts.append(config.PROXY)
    last_err: Exception | None = None
    for proxy in attempts:
        try:
            with httpx.Client(
                timeout=30, follow_redirects=True, proxies=proxy,
                headers=headers, max_bytes=MAX_BYTES,
            ) as client:
                r = client.get(url)
                r.raise_for_status()
                html = r.text
                return _extract(html, str(r.url))
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"网页抓取失败：{type(last_err).__name__}: {last_err}")


def _extract(html: str, final_url: str) -> dict:
    """用 BeautifulSoup 抽取标题与正文纯文本。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(f"缺少 beautifulsoup4 依赖：{e}")

    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))
    if not text:
        raise RuntimeError("未能从网页中抽取到有效正文（可能是动态渲染页面，请改贴正文）")
    return {"title": title, "text": text[:MAX_TEXT], "url": final_url}


def structure(text: str) -> dict:
    """调用 LLM 把正文结构化，映射到分类体系，返回规范化字段 dict。"""
    gateway = LLMGateway()
    resp = gateway.chat(
        [{"role": "user", "content": _build_prompt(text)}],
        max_tokens=2000,
        role="knowledge",
        temperature=0.2,
    )
    msg = resp["choices"][0]["message"]
    # 推理模型可能把内容放在 content，也可能留空（在 reasoning_content）；兜底取后者。
    content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    data = _parse_json(content)

    category = taxonomy.canonical(data.get("category"), taxonomy.DOMAIN_NAMES, "综合")
    subcategory = _subcategory_of(category, data.get("subcategory"))
    knowledge_type = taxonomy.canonical(data.get("knowledge_type"), taxonomy.KNOWLEDGE_TYPES, "方法规则")
    style = taxonomy.canonical(data.get("style"), taxonomy.STYLES, "混合")
    market = taxonomy.canonical(data.get("market"), taxonomy.MARKETS, "通用")
    regime = taxonomy.canonical(data.get("regime"), taxonomy.REGIMES, "通用")
    source = taxonomy.canonical(data.get("source"), taxonomy.SOURCES, "用户录入")
    difficulty = taxonomy.canonical(data.get("difficulty"), taxonomy.DIFFICULTIES, "入门")
    authority = taxonomy.canonical(data.get("authority"), taxonomy.AUTHORITY_LEVELS, "待核实")

    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,，;；/、]", tags) if t.strip()]
    tags = [str(t).strip() for t in tags if str(t).strip()][:6]

    return {
        "title": str(data.get("title") or "").strip() or "未命名知识",
        "summary": str(data.get("summary") or "").strip(),
        "content": str(data.get("content") or text).strip(),
        "category": category,
        "subcategory": subcategory,
        "knowledge_type": knowledge_type,
        "style": style,
        "market": market,
        "regime": regime,
        "source": source,
        "source_name": str(data.get("source_name") or "").strip() or None,
        "author": str(data.get("author") or "").strip() or None,
        "tags": tags,
        "difficulty": difficulty,
        "authority": authority,
    }


def ingest(text: str | None = None, url: str | None = None) -> dict:
    """吸收入口：优先 URL（抓取），否则用正文；结构化后去重落库。"""
    text = (text or "").strip()
    url = (url or "").strip()

    fetched: dict | None = None
    created_by = "ingest_text"
    if url:
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "链接需以 http:// 或 https:// 开头"}
        fetched = fetch_url(url)
        text = fetched["text"]
        created_by = "ingest_url"
    elif not text:
        return {"ok": False, "error": "请粘贴知识正文或提供来源链接"}

    try:
        fields = structure(text)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"结构化失败：{type(e).__name__}: {e}"}

    if fetched:
        fields["source_url"] = fetched["url"]
        if not fields["source_name"] and fetched.get("title"):
            fields["source_name"] = fetched["title"][:80]
    elif url:
        fields["source_url"] = url

    dup = store.find_by_title_or_url(fields.get("title"), fields.get("source_url"))
    if dup:
        return {"ok": False, "duplicate": True, "existing": dup, "error": "已存在相似知识，未重复录入"}

    node = store.create_node(**fields, created_by=created_by, status="待审核")
    return {"ok": True, "node": node, "source_url": fields.get("source_url")}
