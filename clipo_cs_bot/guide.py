"""1차 응대 가이드(RAG 참조 문서) 로더.

우선순위:
1. Notion API (NOTION_TOKEN + 페이지 ID) - 블록을 마크다운으로 변환, GUIDE_REFRESH_MINUTES 동안 캐시
2. 캐시 파일 (.state/cs_guide.cache.md) - Notion 호출 실패 시
3. 저장소에 포함된 스냅샷 (guide/cs_guide.md) - 토큰이 없거나 모두 실패 시

가이드는 Claude 시스템 프롬프트에 그대로 들어가므로, 노션 문서를 고치면 다음 갱신 주기부터 초안에 반영된다.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)


def _rich_text(items: list[dict[str, Any]]) -> str:
    out = []
    for it in items:
        text = it.get("plain_text", "")
        ann = it.get("annotations", {})
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("bold"):
            text = f"**{text}**"
        href = it.get("href")
        if href and it.get("type") == "text":
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


class NotionGuideFetcher:
    def __init__(self, token: str, page_id: str):
        from notion_client import Client  # 지연 임포트: 토큰 없을 때 의존성 강제하지 않음

        self.client = Client(auth=token)
        self.page_id = page_id

    def _children(self, block_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = None
        while True:
            resp = self.client.blocks.children.list(block_id=block_id, start_cursor=cursor, page_size=100)
            results.extend(resp.get("results", []))
            if not resp.get("has_more"):
                return results
            cursor = resp.get("next_cursor")

    def _render(self, blocks: list[dict[str, Any]], depth: int = 0) -> list[str]:
        lines: list[str] = []
        indent = "  " * depth
        number = 0
        for b in blocks:
            t = b.get("type")
            data = b.get(t, {}) or {}
            text = _rich_text(data.get("rich_text", [])) if "rich_text" in data else ""

            if t == "numbered_list_item":
                number += 1
            else:
                number = 0

            if t == "heading_1":
                lines += ["", f"# {text}"]
            elif t == "heading_2":
                lines += ["", f"## {text}"]
            elif t == "heading_3":
                lines += ["", f"### {text}"]
            elif t == "paragraph":
                lines.append(f"{indent}{text}" if text else "")
            elif t == "bulleted_list_item":
                lines.append(f"{indent}- {text}")
            elif t == "numbered_list_item":
                lines.append(f"{indent}{number}. {text}")
            elif t == "to_do":
                mark = "x" if data.get("checked") else " "
                lines.append(f"{indent}- [{mark}] {text}")
            elif t == "quote":
                lines += [f"{indent}> {ln}" for ln in text.splitlines() or [""]]
            elif t == "callout":
                icon = (data.get("icon") or {}).get("emoji", "")
                lines += [f"{indent}> {icon} {ln}".rstrip() for ln in text.splitlines() or [""]]
            elif t == "code":
                lines += ["", f"{indent}```", *[f"{indent}{ln}" for ln in text.splitlines()], f"{indent}```", ""]
            elif t == "divider":
                lines += ["", "---", ""]
            elif t == "toggle":
                lines.append(f"{indent}- {text}")
            elif t == "table":
                rows = self._children(b["id"])
                for i, row in enumerate(rows):
                    cells = [_rich_text(c) for c in row.get("table_row", {}).get("cells", [])]
                    lines.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        lines.append("|" + "---|" * len(cells))
                lines.append("")
                continue  # table_row 는 위에서 처리했으므로 재귀하지 않음
            elif t == "child_page":
                lines.append(f"{indent}- (하위 페이지) {data.get('title', '')}")
            elif t in {"image", "file", "pdf", "video", "bookmark", "embed"}:
                lines.append(f"{indent}({t} 생략)")
            elif text:
                lines.append(f"{indent}{text}")

            if b.get("has_children") and t not in {"table", "child_page"}:
                lines += self._render(self._children(b["id"]), depth + 1)
        return lines

    def fetch_markdown(self) -> str:
        page = self.client.pages.retrieve(page_id=self.page_id)
        title_prop = page.get("properties", {}).get("title", {}).get("title", [])
        title = _rich_text(title_prop) if title_prop else "클리포 CS 1차 응대 가이드"
        body = "\n".join(self._render(self._children(self.page_id)))
        return f"# {title}\n\n{body.strip()}\n"


class GuideStore:
    """가이드 텍스트를 메모리 + 파일에 캐시하고 주기적으로 Notion 에서 갱신한다."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._text: str | None = None
        self._loaded_at: float = 0.0
        self._source = "none"

    @property
    def source(self) -> str:
        return self._source

    def _stale(self) -> bool:
        return (time.time() - self._loaded_at) > self.settings.guide_refresh_minutes * 60

    def get(self, *, force_refresh: bool = False) -> str:
        if self._text and not force_refresh and not self._stale():
            return self._text

        if self.settings.notion_token:
            try:
                fetcher = NotionGuideFetcher(self.settings.notion_token, self.settings.guide_page_id)
                text = fetcher.fetch_markdown()
                if len(text) > 500:
                    self._set(text, "notion")
                    self._write_cache(text)
                    return text
                log.warning("Notion 가이드가 비정상적으로 짧습니다(%d자). 캐시/스냅샷을 사용합니다.", len(text))
            except Exception as e:  # noqa: BLE001 - 네트워크/권한 오류는 폴백으로 처리
                log.warning("Notion 가이드 조회 실패: %s. 캐시/스냅샷을 사용합니다.", e)

        cache = self.settings.guide_cache_path
        if cache.exists():
            self._set(cache.read_text(encoding="utf-8"), f"cache:{cache}")
            return self._text or ""

        fallback = self.settings.guide_fallback_path
        if fallback.exists():
            self._set(fallback.read_text(encoding="utf-8"), f"snapshot:{fallback}")
            return self._text or ""

        raise RuntimeError("가이드를 어디에서도 불러오지 못했습니다. NOTION_TOKEN 또는 guide/cs_guide.md 를 확인하세요.")

    def _set(self, text: str, source: str) -> None:
        self._text = text
        self._loaded_at = time.time()
        self._source = source
        log.info("가이드 로드 완료 (%s, %d자)", source, len(text))

    def _write_cache(self, text: str) -> None:
        try:
            path: Path = self.settings.guide_cache_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            log.warning("가이드 캐시 저장 실패: %s", e)
