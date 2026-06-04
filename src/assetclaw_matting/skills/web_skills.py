from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


def search_web(query: str, max_results: int = 6, timeout_seconds: int = 15) -> dict[str, Any]:
    cleaned_query = _clean_query(query)
    limit = max(1, min(int(max_results or 6), 10))
    timeout = max(3, min(int(timeout_seconds or 15), 45))
    response = requests.get(
        "https://duckduckgo.com/html/",
        params={"q": cleaned_query},
        headers={"User-Agent": "AssetClawBot/1.0 (+readonly web search)"},
        timeout=timeout,
    )
    response.raise_for_status()
    parser = _DuckDuckGoParser()
    parser.feed(response.text)
    items = parser.items()[:limit]
    return {
        "ok": True,
        "query": cleaned_query,
        "count": len(items),
        "items": items,
        "source": "duckduckgo_html",
    }


def research(query: str, max_results: int = 5, max_pages: int = 3, max_chars_per_page: int = 3500, timeout_seconds: int = 15) -> dict[str, Any]:
    search = search_web(query, max_results=max_results, timeout_seconds=timeout_seconds)
    pages: list[dict[str, Any]] = []
    for item in search.get("items", [])[: max(1, min(int(max_pages or 3), 5))]:
        url = str(item.get("url") or "")
        if not url:
            continue
        try:
            page = fetch_url(url, max_chars=max_chars_per_page, timeout_seconds=timeout_seconds)
            pages.append(
                {
                    "ok": True,
                    "url": url,
                    "title": page.get("title") or item.get("title") or "",
                    "text": page.get("text") or "",
                    "truncated": page.get("truncated", False),
                }
            )
        except Exception as exc:
            pages.append({"ok": False, "url": url, "title": item.get("title") or "", "error": str(exc)})
    answer = _synthesize_research_answer(query, search.get("items", []), pages)
    return {
        "ok": True,
        "query": search.get("query"),
        "answer": answer,
        "items": search.get("items", []),
        "pages": pages,
        "source_count": len([page for page in pages if page.get("ok")]),
    }


def fetch_url(url: str, max_chars: int = 4000, timeout_seconds: int = 20) -> dict[str, Any]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an http(s) URL")
    limit = max(200, min(int(max_chars or 4000), 12000))
    timeout = max(3, min(int(timeout_seconds or 20), 60))
    response = requests.get(
        url,
        headers={"User-Agent": "AssetClawBot/1.0 (+readonly fetch)"},
        timeout=timeout,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    text = response.text
    title = ""
    if "html" in content_type.lower() or "<html" in text[:500].lower():
        parser = _TextExtractor()
        parser.feed(text)
        title = parser.title.strip()
        text = parser.text()
    else:
        text = _clean_text(text)
    return {
        "ok": True,
        "url": url,
        "status_code": response.status_code,
        "content_type": content_type,
        "title": title,
        "text": text[:limit],
        "truncated": len(text) > limit,
    }


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._items: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = set((attr.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._flush_current()
            self._current = {"title": "", "url": _normalize_search_url(attr.get("href") or ""), "snippet": ""}
            self._capture = "title"
            self._parts = []
        elif self._current is not None and ("result__snippet" in classes or "result__extras__url" in classes):
            self._capture = "snippet"
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture and self._current is not None:
            value = _clean_text(html.unescape(" ".join(self._parts)))
            if value:
                if self._capture == "snippet" and self._current.get("snippet"):
                    self._current["snippet"] = f"{self._current['snippet']} {value}".strip()
                else:
                    self._current[self._capture] = value
            self._capture = None
            self._parts = []
        if tag == "div":
            self._flush_current()

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def items(self) -> list[dict[str, str]]:
        self._flush_current()
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in self._items:
            url = item.get("url", "")
            title = item.get("title", "")
            if not url or not title or url in seen:
                continue
            seen.add(url)
            item["domain"] = urlparse(url).netloc
            deduped.append(item)
        return deduped

    def _flush_current(self) -> None:
        if self._current and self._current.get("title") and self._current.get("url"):
            self._items.append(self._current)
        self._current = None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._in_title = False
        self.title = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        self._parts.append(data)

    def text(self) -> str:
        return _clean_text("\n".join(self._parts))


def _clean_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _clean_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
    if not cleaned:
        raise ValueError("query is required")
    if len(cleaned) > 300:
        cleaned = cleaned[:300].strip()
    return cleaned


def _normalize_search_url(url: str) -> str:
    raw = html.unescape(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if raw.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return "https://duckduckgo.com" + raw
    return raw


def _synthesize_research_answer(query: str, items: list[dict[str, Any]], pages: list[dict[str, Any]]) -> str:
    if _looks_like_full_lyrics_request(query):
        return (
            "我可以帮你定位歌词来源，但不能搬运整首歌词全文。"
            "建议打开音乐平台、官方 MV/专辑页或可信歌词页查看；我可以基于你贴出的短句做歌意解析，或写同氛围原创小段。"
        )
    terms = _query_terms(query)
    bullets: list[str] = []
    for page in pages:
        if not page.get("ok"):
            continue
        sentence = _best_sentence(str(page.get("text") or ""), terms)
        title = str(page.get("title") or urlparse(str(page.get("url") or "")).netloc)
        if sentence:
            bullets.append(f"{title}：{sentence}")
        if len(bullets) >= 4:
            break
    if not bullets:
        for item in items[:4]:
            snippet = str(item.get("snippet") or "").strip()
            if snippet:
                bullets.append(f"{item.get('title')}：{snippet}")
    if not bullets:
        return "我找到了一些候选结果，但页面摘要不够干净。你可以让我指定某个 URL 深读，或换一个更具体的关键词。"
    return "我先按搜索结果整合出这些要点：\n" + "\n".join(f"- {item}" for item in bullets)


def _query_terms(query: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_\-]{3,}|[\u4e00-\u9fff]{2,}", query.lower())
    stop = {"搜索", "查找", "帮我", "一下", "这个", "那个", "什么", "怎么", "如何", "全部", "全文", "完整"}
    return [word for word in words if word not in stop][:8]


def _best_sentence(text: str, terms: list[str]) -> str:
    sentences = re.split(r"(?<=[。！？.!?])\s+|\n+", _clean_text(text))
    best = ""
    best_score = -1
    for sentence in sentences:
        clean = sentence.strip()
        if len(clean) < 20:
            continue
        lower = clean.lower()
        score = sum(1 for term in terms if term in lower)
        if score > best_score:
            best = clean
            best_score = score
    return best[:350]


def _looks_like_full_lyrics_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "歌词" in compact and any(word in compact for word in ("全部", "全文", "完整", "整首", "全首"))
