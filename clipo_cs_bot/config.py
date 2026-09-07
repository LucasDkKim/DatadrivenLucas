"""환경 변수 기반 설정.

모든 값은 .env 또는 프로세스 환경 변수에서 읽는다. 필수 값이 없으면 시작 시점에 바로 실패한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"환경 변수 {name} 가 설정되어 있지 않습니다. .env.example 을 참고하세요.")
    return value or ""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Slack
    slack_bot_token: str
    slack_app_token: str
    slack_channel_id: str

    # Claude
    anthropic_api_key: str
    claude_model: str
    claude_effort: str
    claude_max_tokens: int
    claude_fallbacks: bool

    # Notion 가이드
    notion_token: str
    notion_guide_page_id: str
    guide_refresh_minutes: int
    guide_fallback_path: Path
    guide_cache_path: Path

    # 동작
    state_file: Path
    dry_run: bool
    context_history_days: int
    max_prior_inquiries: int
    max_similar_examples: int
    poll_interval_seconds: int

    @property
    def guide_page_id(self) -> str:
        """URL 또는 UUID 어느 쪽을 넣어도 32자리 hex ID로 정규화한다."""
        raw = self.notion_guide_page_id.strip()
        hex_only = "".join(ch for ch in raw if ch.isalnum())
        return hex_only[-32:] if len(hex_only) >= 32 else raw


def load_settings(*, require_slack: bool = True, require_claude: bool = True) -> Settings:
    return Settings(
        slack_bot_token=_env("SLACK_BOT_TOKEN", required=require_slack),
        slack_app_token=_env("SLACK_APP_TOKEN"),
        slack_channel_id=_env("SLACK_CHANNEL_ID", "C083Y7EG9QT"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", required=require_claude),
        claude_model=_env("CLAUDE_MODEL", "claude-opus-5"),
        claude_effort=_env("CLAUDE_EFFORT", "high"),
        claude_max_tokens=int(_env("CLAUDE_MAX_TOKENS", "16000")),
        claude_fallbacks=_env_bool("CLAUDE_FALLBACKS", True),
        notion_token=_env("NOTION_TOKEN"),
        notion_guide_page_id=_env("NOTION_GUIDE_PAGE_ID", "3d417e5c8cf181afbf54eb5e23de248b"),
        guide_refresh_minutes=int(_env("GUIDE_REFRESH_MINUTES", "60")),
        guide_fallback_path=Path(_env("GUIDE_FALLBACK_PATH", str(PROJECT_DIR / "guide" / "cs_guide.md"))),
        guide_cache_path=Path(_env("GUIDE_CACHE_PATH", str(PROJECT_DIR / ".state" / "cs_guide.cache.md"))),
        state_file=Path(_env("STATE_FILE", str(PROJECT_DIR / ".state" / "processed.json"))),
        dry_run=_env_bool("DRY_RUN", False),
        context_history_days=int(_env("CONTEXT_HISTORY_DAYS", "120")),
        max_prior_inquiries=int(_env("MAX_PRIOR_INQUIRIES", "3")),
        max_similar_examples=int(_env("MAX_SIMILAR_EXAMPLES", "3")),
        poll_interval_seconds=int(_env("POLL_INTERVAL_SECONDS", "60")),
    )
