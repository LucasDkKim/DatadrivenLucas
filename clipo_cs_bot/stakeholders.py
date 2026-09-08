"""[에스컬레이션] 블록의 담당 조직 라벨을 Slack 멘션으로 매핑한다.

stakeholders.json (설정 가능 경로) 예:
{
  "CS담당": ["<@U000000CS>"],
  "개발팀": ["<!subteam^S00000DEV|@dev>"],
  "AI조직": ["<@U0000AI1>", "<@U0000AI2>"],
  "기획": ["<@U0000PLAN>"]
}
"_" 로 시작하는 키(주석용)는 무시한다. 파일이 없으면 빈 맵 → 태깅 off.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def load_stakeholder_map(path: Path) -> dict[str, list[str]]:
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("담당자 맵 로드 실패 %s: %s", path, e)
        return {}
    out: dict[str, list[str]] = {}
    if not isinstance(data, dict):
        return {}
    for k, v in data.items():
        if not k or str(k).startswith("_"):
            continue
        vals = [v] if isinstance(v, str) else list(v) if isinstance(v, (list, tuple)) else []
        cleaned = [str(x).strip() for x in vals if str(x).strip()]
        if cleaned:
            out[str(k)] = cleaned
    return out


def tags_for_escalation(escalation_text: str, stakeholder_map: dict[str, list[str]]) -> list[str]:
    """에스컬레이션 텍스트에 등장하는 조직 라벨의 Slack 멘션을 순서대로(중복 제거) 반환한다."""
    if not escalation_text or not stakeholder_map:
        return []
    mentions: list[str] = []
    for label, ms in stakeholder_map.items():
        if label and label in escalation_text:
            for m in ms:
                if m not in mentions:
                    mentions.append(m)
    return mentions
