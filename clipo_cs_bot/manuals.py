"""업로드된 매뉴얼을 RAG 컨텍스트로 로드한다.

manuals/ 디렉터리(설정 가능)의 .md/.markdown/.txt 파일을 읽어 하나의 텍스트로 합친다.
합친 결과는 drafter 의 시스템 프롬프트 가이드 블록에 이어 붙여 제품 사실의 1차 근거로 쓴다.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_EXTS = {".md", ".markdown", ".txt"}


def load_manuals(manuals_dir: Path, *, max_chars: int = 60000) -> str:
    """manuals_dir 안의 텍스트 매뉴얼을 파일명 순으로 읽어 하나의 마크다운으로 합친다.

    디렉터리가 없거나 대상 파일이 없으면 빈 문자열을 반환한다(기능 off).
    합계가 max_chars 를 넘으면 이후 내용을 잘라 프롬프트 폭주를 막는다.
    """
    if not manuals_dir or not manuals_dir.exists():
        return ""
    files = sorted(
        p for p in manuals_dir.rglob("*") if p.is_file() and p.suffix.lower() in _EXTS
    )
    if not files:
        return ""
    parts: list[str] = []
    total = 0
    for p in files:
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError as e:
            log.warning("매뉴얼 읽기 실패 %s: %s", p, e)
            continue
        if not text:
            continue
        block = f"## 매뉴얼: {p.name}\n{text}"
        if total + len(block) > max_chars:
            block = block[: max(0, max_chars - total)]
        if not block:
            log.warning("매뉴얼이 max_chars(%d)를 초과해 이후 파일을 잘랐습니다.", max_chars)
            break
        parts.append(block)
        total += len(block)
        if total >= max_chars:
            log.warning("매뉴얼이 max_chars(%d)를 초과해 이후 파일을 잘랐습니다.", max_chars)
            break
    return "\n\n---\n\n".join(parts)
