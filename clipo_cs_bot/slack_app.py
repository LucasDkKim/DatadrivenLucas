"""Slack 연동: 신규 문의 감지 → 초안 생성 → 스레드에 댓글.

두 가지 실행 모드를 지원한다.
- Socket Mode (기본): Slack 이벤트를 실시간으로 받는다. 공개 URL 불필요. SLACK_APP_TOKEN(xapp-) 필요.
- Polling: N초마다 conversations.history 를 읽어 미처리 문의를 찾는다. 봇 토큰만 있으면 된다.

둘 다 같은 `InquiryProcessor` 를 쓴다. 중복 응답 방지는 (1) 처리 완료 ts 를 담은 상태 파일, (2) 스레드에 우리 봇 댓글이 이미 있는지 확인, 두 겹이다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import Settings
from .context import ChannelHistory, build_context, find_inquiry_by_ts
from .drafter import Draft, Drafter
from .guide import GuideStore
from .parser import Inquiry, parse_inquiry

log = logging.getLogger(__name__)

DRAFT_HEADER = ":robot_face: *1차 응대 초안* — 클리포 CS 도우미 · _검수 전 발송 금지_"
DRAFT_FOOTER = (
    "_담당자가 검수·수정한 뒤 clipo.ai 백오피스에 등록해 주세요. "
    "초안이 어긋난 점은 이 스레드에 남겨 주시면 가이드에 반영합니다._"
)


class ProcessedStore:
    """처리한 메시지 ts 를 JSON 파일에 보관한다 (재시작해도 중복 댓글 방지)."""

    def __init__(self, path: Path, *, keep: int = 3000):
        self.path = path
        self.keep = keep
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        if path.exists():
            try:
                self._items = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                log.warning("상태 파일을 읽지 못했습니다(%s). 새로 시작합니다.", e)

    def has(self, ts: str) -> bool:
        with self._lock:
            return ts in self._items

    def add(self, ts: str, info: dict) -> None:
        with self._lock:
            self._items[ts] = {"at": time.time(), **info}
            if len(self._items) > self.keep:
                for old in sorted(self._items, key=lambda k: self._items[k]["at"])[: len(self._items) - self.keep]:
                    self._items.pop(old, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=1), encoding="utf-8")


def format_draft_message(inquiry: Inquiry, draft: Draft) -> str:
    """Slack mrkdwn 으로 초안을 꾸민다. 초안 본문은 복사하기 쉽게 코드 블록으로."""
    lines = [DRAFT_HEADER]
    if inquiry.url:
        lines.append(f"문의: <{inquiry.url}|{inquiry.title or '바로가기'}>")
    lines.append("")
    if draft.sections:
        sec = draft.sections
        lines.append(f"*[분류]* {sec.get('분류', '(미분류)')}")
        lines.append("*[응대 초안]*")
        lines.append("```")
        lines.append(sec.get("응대 초안", "(초안 없음)"))
        lines.append("```")
        lines.append("*[크로스체크 필요]*")
        lines.append(sec.get("크로스체크 필요", "숫자·금액 없음 — 해당 없음"))
        lines.append("*[에스컬레이션]*")
        lines.append(sec.get("에스컬레이션", "정보성 답변 — 즉시 회신 가능"))
    else:
        lines.append(draft.raw)
    if draft.warnings:
        lines.append("")
        lines.extend(f":warning: {w}" for w in draft.warnings)
    lines.append("")
    lines.append(DRAFT_FOOTER)
    return "\n".join(lines)


class InquiryProcessor:
    def __init__(self, settings: Settings, client: WebClient):
        self.settings = settings
        self.client = client
        self.guide = GuideStore(settings)
        self.drafter = Drafter(settings)
        self.store = ProcessedStore(settings.state_file)
        self.bot_user_id: str | None = None
        self._busy = threading.Lock()
        try:
            self.bot_user_id = client.auth_test().get("user_id")
        except SlackApiError as e:
            log.warning("auth.test 실패(%s). 자기 댓글 중복 확인을 건너뜁니다.", e.response.get("error"))

    # ---- 감지 ----------------------------------------------------------------

    def _already_replied(self, ts: str) -> bool:
        if not self.bot_user_id:
            return False
        try:
            resp = self.client.conversations_replies(channel=self.settings.slack_channel_id, ts=ts, limit=100)
        except SlackApiError:
            return False
        return any(m.get("user") == self.bot_user_id for m in resp.get("messages", []) if m.get("ts") != ts)

    def should_handle(self, event: dict) -> Inquiry | None:
        if event.get("channel") != self.settings.slack_channel_id:
            return None
        if event.get("subtype") not in (None, "bot_message"):
            return None  # message_changed / deleted / join 등은 무시
        if event.get("thread_ts") and event.get("thread_ts") != event.get("ts"):
            return None  # 스레드 답글은 무시
        inq = parse_inquiry(event.get("text", ""), event.get("ts", ""))
        if not inq or not inq.is_pending:
            return None
        return inq

    # ---- 처리 ----------------------------------------------------------------

    def build_draft(self, inquiry: Inquiry) -> tuple[Draft, str]:
        history = ChannelHistory(self.client, self.settings.slack_channel_id, days=self.settings.context_history_days)
        context = build_context(
            history,
            inquiry,
            bot_user_id=self.bot_user_id,
            max_prior=self.settings.max_prior_inquiries,
            max_similar=self.settings.max_similar_examples,
        )
        guide = self.guide.get()
        draft = self.drafter.draft(inquiry, context, guide)
        return draft, format_draft_message(inquiry, draft)

    def handle(self, inquiry: Inquiry, *, force: bool = False) -> bool:
        """초안을 만들어 스레드에 올린다. 올렸으면 True."""
        with self._busy:  # 문의가 몰려도 Claude 호출은 직렬로
            if not force and (self.store.has(inquiry.ts) or self._already_replied(inquiry.ts)):
                log.info("이미 처리한 문의 %s, 건너뜀", inquiry.ts)
                return False
            log.info("문의 처리 시작 ts=%s kind=%s 유형=%s", inquiry.ts, inquiry.kind, inquiry.category)
            try:
                draft, message = self.build_draft(inquiry)
            except Exception:  # noqa: BLE001 - 어떤 오류든 채널에 알리고 계속 돈다
                log.exception("초안 생성 실패 ts=%s", inquiry.ts)
                if not self.settings.dry_run:
                    self._post(inquiry.ts, ":x: 1차 응대 초안 생성에 실패했습니다. 담당자가 직접 작성해 주세요. (로그 확인 필요)")
                return False

            if self.settings.dry_run:
                print("\n" + "=" * 70 + f"\n[DRY RUN] ts={inquiry.ts}\n" + message + "\n" + "=" * 70)
            else:
                self._post(inquiry.ts, message)
            self.store.add(inquiry.ts, {"kind": inquiry.kind, "category": draft.category, "model": draft.model})
            return True

    def _post(self, thread_ts: str, text: str) -> None:
        self.client.chat_postMessage(
            channel=self.settings.slack_channel_id,
            thread_ts=thread_ts,
            text=text,
            unfurl_links=False,
            unfurl_media=False,
        )

    # ---- 수동/폴링 진입점 ----------------------------------------------------

    def process_ts(self, ts: str, *, force: bool = False) -> bool:
        history = ChannelHistory(self.client, self.settings.slack_channel_id, days=self.settings.context_history_days)
        inq = find_inquiry_by_ts(history, ts)
        if not inq:
            log.error("ts=%s 메시지를 문의 알림으로 해석하지 못했습니다.", ts)
            return False
        return self.handle(inq, force=force)

    def scan_pending(self, *, since_hours: float) -> int:
        """아직 답변되지 않았고 우리 댓글도 없는 문의를 찾아 처리한다. 처리 건수 반환."""
        history = ChannelHistory(self.client, self.settings.slack_channel_id, days=max(1, int(since_hours / 24) + 1))
        cutoff = time.time() - since_hours * 3600
        handled = 0
        for inq in sorted(history.inquiries(), key=lambda i: float(i.ts)):
            if float(inq.ts) < cutoff or not inq.is_pending:
                continue
            if self.handle(inq):
                handled += 1
        return handled


# ---- 실행 모드 ---------------------------------------------------------------


def run_socket_mode(settings: Settings) -> None:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    if not settings.slack_app_token:
        raise RuntimeError("Socket Mode 에는 SLACK_APP_TOKEN(xapp-...) 이 필요합니다. 없으면 `poll` 모드를 쓰세요.")

    app = App(token=settings.slack_bot_token)
    processor = InquiryProcessor(settings, app.client)
    processor.guide.get()  # 시작 시 가이드 로드 확인

    @app.event("message")
    def on_message(event, logger):  # noqa: ANN001 - Bolt 시그니처
        inq = processor.should_handle(event)
        if not inq:
            return
        # Slack 은 3초 안에 ack 를 원하므로 무거운 작업은 스레드로
        threading.Thread(target=processor.handle, args=(inq,), daemon=True).start()

    log.info("Socket Mode 시작 — 채널 %s 감시 중 (가이드 출처: %s)", settings.slack_channel_id, processor.guide.source)
    SocketModeHandler(app, settings.slack_app_token).start()


def run_polling(settings: Settings) -> None:
    client = WebClient(token=settings.slack_bot_token)
    processor = InquiryProcessor(settings, client)
    processor.guide.get()
    log.info("Polling 시작 — %d초 간격, 채널 %s (가이드 출처: %s)", settings.poll_interval_seconds, settings.slack_channel_id, processor.guide.source)
    lookback_hours = max(2.0, settings.poll_interval_seconds * 5 / 3600)
    while True:
        try:
            n = processor.scan_pending(since_hours=lookback_hours)
            if n:
                log.info("이번 주기 처리 %d건", n)
        except Exception:  # noqa: BLE001
            log.exception("폴링 주기 오류")
        time.sleep(settings.poll_interval_seconds)
