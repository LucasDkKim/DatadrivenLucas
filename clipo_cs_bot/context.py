"""초안 작성에 쓸 컨텍스트를 #clipo-cs 채널 이력에서 수집한다 (경량 RAG).

세 가지를 모은다.
1. 같은 교사의 과거 문의 + 실제 답변  -> 개인화 (연수 강사 여부, 반복 요청 등)
2. 같은 유형의 최근 답변 완료 문의    -> 톤/형식 few-shot
3. 재질문인 경우 원 문의 스레드         -> 대화 맥락

모두 Slack `conversations.history` / `conversations.replies` 만 사용한다 (별도 DB 없음).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterable

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .parser import Inquiry, ThreadRecord, parse_answer_reply, parse_inquiry, question_id_from_url

log = logging.getLogger(__name__)


@dataclass
class DraftContext:
    prior_by_teacher: list[ThreadRecord] = field(default_factory=list)
    similar_by_category: list[ThreadRecord] = field(default_factory=list)
    original_thread: ThreadRecord | None = None  # 재질문일 때

    def describe(self) -> str:
        sections = []
        if self.original_thread:
            sections.append("## 이 재질문의 원 문의와 1차 답변\n" + self.original_thread.describe())
        if self.prior_by_teacher:
            items = "\n\n---\n\n".join(r.describe() for r in self.prior_by_teacher)
            sections.append("## 같은 선생님의 과거 문의와 실제 답변 (최신순)\n" + items)
        if self.similar_by_category:
            items = "\n\n---\n\n".join(r.describe() for r in self.similar_by_category)
            sections.append("## 같은 유형의 최근 모범 답변 사례 (톤·형식 참고)\n" + items)
        return "\n\n".join(sections) if sections else "(참고할 과거 이력 없음)"


class ChannelHistory:
    """채널 이력을 한 번 읽어 메모리에 들고, 여러 조회에 재사용한다."""

    def __init__(self, client: WebClient, channel_id: str, *, days: int = 120, max_pages: int = 10):
        self.client = client
        self.channel_id = channel_id
        self.days = days
        self.max_pages = max_pages
        self._messages: list[dict] | None = None
        self._thread_cache: dict[str, list[dict]] = {}

    def messages(self) -> list[dict]:
        if self._messages is not None:
            return self._messages
        oldest = str(time.time() - self.days * 86400)
        collected: list[dict] = []
        cursor = None
        for _ in range(self.max_pages):
            try:
                resp = self.client.conversations_history(
                    channel=self.channel_id, oldest=oldest, limit=200, cursor=cursor
                )
            except SlackApiError as e:
                log.warning("conversations.history 실패: %s", e.response.get("error"))
                break
            collected.extend(resp.get("messages", []))
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not resp.get("has_more") or not cursor:
                break
        self._messages = collected
        return collected

    def replies(self, thread_ts: str) -> list[dict]:
        if thread_ts in self._thread_cache:
            return self._thread_cache[thread_ts]
        try:
            resp = self.client.conversations_replies(channel=self.channel_id, ts=thread_ts, limit=200)
            msgs = [m for m in resp.get("messages", []) if m.get("ts") != thread_ts]
        except SlackApiError as e:
            log.warning("conversations.replies(%s) 실패: %s", thread_ts, e.response.get("error"))
            msgs = []
        self._thread_cache[thread_ts] = msgs
        return msgs

    def inquiries(self) -> Iterable[Inquiry]:
        for m in self.messages():
            if m.get("thread_ts") and m.get("thread_ts") != m.get("ts"):
                continue  # 스레드 답글은 제외
            inq = parse_inquiry(m.get("text", ""), m.get("ts", ""))
            if inq:
                yield inq

    def record(self, inq: Inquiry, *, bot_user_id: str | None = None) -> ThreadRecord:
        rec = ThreadRecord(inquiry=inq)
        for r in self.replies(inq.ts):
            text = r.get("text", "")
            if ans := parse_answer_reply(text, r.get("ts", "")):
                rec.answer = ans
                continue
            if bot_user_id and r.get("user") == bot_user_id:
                continue  # 우리 봇이 올린 초안은 참고 자료로 쓰지 않는다
            if r.get("bot_id") or r.get("subtype"):
                continue
            if text.strip():
                rec.human_notes.append(text.strip())
        return rec


def build_context(
    history: ChannelHistory,
    inquiry: Inquiry,
    *,
    bot_user_id: str | None = None,
    max_prior: int = 3,
    max_similar: int = 3,
) -> DraftContext:
    ctx = DraftContext()
    all_inqs = [i for i in history.inquiries() if i.ts != inquiry.ts]
    answered = [i for i in all_inqs if i.kind == "answered"]

    # 1) 재질문이면 원 문의 스레드
    if inquiry.kind == "requestion":
        qid = inquiry.question_id
        for cand in all_inqs:
            if qid and cand.question_id == qid:
                ctx.original_thread = history.record(cand, bot_user_id=bot_user_id)
                # 원 문의의 교사 정보를 재질문에 이어붙여 개인화에 활용
                inquiry.teacher_id = inquiry.teacher_id or cand.teacher_id
                inquiry.school_name = inquiry.school_name or cand.school_name
                inquiry.school_id = inquiry.school_id or cand.school_id
                inquiry.category = inquiry.category or cand.category
                break

    # 2) 같은 교사의 과거 문의
    if inquiry.teacher_id:
        prior = [i for i in answered if i.teacher_id == inquiry.teacher_id]
        if ctx.original_thread:
            prior = [i for i in prior if i.ts != ctx.original_thread.inquiry.ts]
        for cand in prior[:max_prior]:
            ctx.prior_by_teacher.append(history.record(cand, bot_user_id=bot_user_id))

    # 3) 같은 유형의 최근 답변 사례 (같은 교사 것은 중복 제외)
    if inquiry.category:
        seen = {r.inquiry.ts for r in ctx.prior_by_teacher}
        similar = [i for i in answered if i.category == inquiry.category and i.ts not in seen]
        for cand in similar:
            rec = history.record(cand, bot_user_id=bot_user_id)
            if rec.answer and rec.answer.answer:  # 실제 답변이 있는 것만 few-shot 으로
                ctx.similar_by_category.append(rec)
            if len(ctx.similar_by_category) >= max_similar:
                break

    return ctx


def find_inquiry_by_ts(history: ChannelHistory, ts: str) -> Inquiry | None:
    for inq in history.inquiries():
        if inq.ts == ts:
            return inq
    # 이력 범위 밖이면 단건 조회
    try:
        resp = history.client.conversations_history(channel=history.channel_id, latest=ts, oldest=ts, inclusive=True, limit=1)
        msgs = resp.get("messages", [])
        if msgs:
            return parse_inquiry(msgs[0].get("text", ""), msgs[0].get("ts", ""))
    except SlackApiError as e:
        log.warning("단건 조회 실패: %s", e.response.get("error"))
    return None


__all__ = ["ChannelHistory", "DraftContext", "build_context", "find_inquiry_by_ts", "question_id_from_url"]
