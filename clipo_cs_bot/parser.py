"""clipo-backoffice 봇이 #clipo-cs 에 올리는 알림 메시지를 구조화한다.

실제 메시지 형태 (신규 문의):

    :eyes: 2026-09-07 19:29:05 에 접수된 문의입니다.
                - 문의 유형 : `연수 지원 신청`
                - 문의 제목 : <https://clipo.ai/staffs/questions/XXXX|연수 지원 신청>
                - 문의 내용 : ... (여러 줄)
                - 문의 유저: 청원여자고등학교(2465)의 교사(25610) (누적 문의 수 : 8)
                - <https://clipo.ai/staffs/questions/YYYY|이전 문의보기>

답변이 완료되면 같은 메시지가 `:white_check_mark: [답변완료] ...` 로 수정되고,
스레드에 `:white_check_mark: N 만에 답변 완료했습니다. <url|바로가기>` + 답변 본문(코드 블록)이 달린다.

재질문은 별도 메시지로 온다:

    :speech_balloon: 3일 만에 재질문이 접수되었습니다. <https://clipo.ai/staffs/questions/XXXX|바로가기>
    ```재질문 본문```
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

InquiryKind = Literal["new", "answered", "requestion"]

_MAILTO_RE = re.compile(r"<mailto:([^|>]+)\|[^>]*>")
_LABELED_LINK_RE = re.compile(r"<(https?://[^|>]+)\|([^>]*)>")
_BARE_LINK_RE = re.compile(r"<(https?://[^>]+)>")
_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")

_CATEGORY_RE = re.compile(r"문의 유형\s*:\s*`([^`]+)`")
_TITLE_RE = re.compile(r"문의 제목\s*:\s*<(https?://[^|>]+)\|([^>]*)>")
_CONTENT_RE = re.compile(r"문의 내용\s*:\s*(.*?)\n\s*-\s*문의 유저", re.DOTALL)
_CONTENT_TAIL_RE = re.compile(r"문의 내용\s*:\s*(.*)$", re.DOTALL)
_USER_RE = re.compile(r"문의 유저\s*:\s*(.+?)\((\d+)\)의\s*교사\((\d+)\)\s*\(누적 문의 수\s*:\s*(\d+)\)")
_PREV_URL_RE = re.compile(r"<(https?://[^|>]+)\|이전 문의보기>")
_RECEIVED_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*에 접수된 문의입니다")
_REQUESTION_RE = re.compile(r"(?::\w+:\s*)?(.+?)\s*만에 재질문이 접수되었습니다\.\s*<(https?://[^|>]+)\|바로가기>")
_ANSWERED_REPLY_RE = re.compile(r"만에 답변 완료했습니다\.\s*<(https?://[^|>]+)\|바로가기>")
_CODE_BLOCK_RE = re.compile(r"```(.*?)```", re.DOTALL)
_QUESTION_ID_RE = re.compile(r"/questions/([A-Za-z0-9_-]+)")


def clean_slack_text(text: str) -> str:
    """Slack mrkdwn 링크/이스케이프를 사람이 읽는 평문으로 바꾼다."""
    text = _MAILTO_RE.sub(r"\1", text)
    text = _LABELED_LINK_RE.sub(lambda m: f"{m.group(2)} ({m.group(1)})" if m.group(2) else m.group(1), text)
    text = _BARE_LINK_RE.sub(r"\1", text)
    text = _USER_MENTION_RE.sub(r"@\1", text)
    return html.unescape(text).strip()


def question_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _QUESTION_ID_RE.search(url)
    return m.group(1) if m else None


@dataclass
class Inquiry:
    kind: InquiryKind
    ts: str
    raw_text: str
    category: str = ""
    title: str = ""
    url: str = ""
    content: str = ""
    school_name: str = ""
    school_id: str = ""
    teacher_id: str = ""
    inquiry_count: int = 0
    received_at: str = ""
    prev_inquiries_url: str = ""
    elapsed: str = ""  # 재질문: "3일", "2 hours" 등

    @property
    def question_id(self) -> str | None:
        return question_id_from_url(self.url)

    @property
    def is_pending(self) -> bool:
        return self.kind in ("new", "requestion")

    def describe(self) -> str:
        """Claude 프롬프트/로그에 넣기 위한 요약."""
        lines = []
        if self.kind == "requestion":
            lines.append(f"[재질문] 최초 답변 후 {self.elapsed or '?'} 만에 접수")
        else:
            lines.append(f"[{'답변 완료' if self.kind == 'answered' else '신규 문의'}] 접수 시각: {self.received_at or '?'}")
        if self.category:
            lines.append(f"문의 유형: {self.category}")
        if self.title:
            lines.append(f"문의 제목: {self.title}")
        if self.school_name or self.teacher_id:
            lines.append(
                f"문의 유저: {self.school_name}(학교ID {self.school_id}) 교사ID {self.teacher_id}"
                f" / 누적 문의 {self.inquiry_count}건"
            )
        if self.url:
            lines.append(f"백오피스 링크: {self.url}")
        lines.append("문의 내용:")
        lines.append(self.content or "(내용 없음)")
        return "\n".join(lines)


def detect_kind(text: str) -> Optional[InquiryKind]:
    if "재질문이 접수되었습니다" in text:
        return "requestion"
    if "접수된 문의입니다" in text:
        return "answered" if "[답변완료]" in text else "new"
    return None


def parse_inquiry(text: str, ts: str = "") -> Optional[Inquiry]:
    """알림 메시지를 Inquiry 로 변환한다. 알림 형식이 아니면 None."""
    if not text:
        return None
    kind = detect_kind(text)
    if kind is None:
        return None

    if kind == "requestion":
        m = _REQUESTION_RE.search(text)
        code = _CODE_BLOCK_RE.search(text)
        return Inquiry(
            kind=kind,
            ts=ts,
            raw_text=text,
            elapsed=clean_slack_text(m.group(1)) if m else "",
            url=m.group(2) if m else "",
            title="재질문",
            content=html.unescape(code.group(1)).strip() if code else clean_slack_text(text),
        )

    inq = Inquiry(kind=kind, ts=ts, raw_text=text)

    if m := _RECEIVED_RE.search(text):
        inq.received_at = m.group(1)
    if m := _CATEGORY_RE.search(text):
        inq.category = m.group(1).strip()
    if m := _TITLE_RE.search(text):
        inq.url = m.group(1)
        inq.title = html.unescape(m.group(2)).strip()
    if m := _CONTENT_RE.search(text) or _CONTENT_TAIL_RE.search(text):
        inq.content = clean_slack_text(m.group(1))
    if m := _USER_RE.search(text):
        inq.school_name = m.group(1).strip()
        inq.school_id = m.group(2)
        inq.teacher_id = m.group(3)
        inq.inquiry_count = int(m.group(4))
    if m := _PREV_URL_RE.search(text):
        inq.prev_inquiries_url = m.group(1)
    return inq


@dataclass
class AnsweredReply:
    """스레드에 달린 '답변 완료' 알림 (실제 발송된 답변 본문 포함)."""

    ts: str
    url: str
    answer: str


def parse_answer_reply(text: str, ts: str = "") -> Optional[AnsweredReply]:
    if not text or "답변 완료했습니다" not in text:
        return None
    m = _ANSWERED_REPLY_RE.search(text)
    code = _CODE_BLOCK_RE.search(text)
    answer = html.unescape(code.group(1)).strip() if code else ""
    return AnsweredReply(ts=ts, url=m.group(1) if m else "", answer=answer)


@dataclass
class ThreadRecord:
    """문의 알림 + 스레드에 달린 답변/내부 논의."""

    inquiry: Inquiry
    answer: Optional[AnsweredReply] = None
    human_notes: list[str] = field(default_factory=list)

    def describe(self, *, max_note_chars: int = 600) -> str:
        parts = [self.inquiry.describe()]
        if self.answer and self.answer.answer:
            parts.append("실제 발송된 답변:\n" + self.answer.answer)
        if self.human_notes:
            notes = "\n".join(f"- {n[:max_note_chars]}" for n in self.human_notes[:5])
            parts.append("스레드 내부 메모:\n" + notes)
        return "\n\n".join(parts)
