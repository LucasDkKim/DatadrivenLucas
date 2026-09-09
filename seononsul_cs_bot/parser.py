"""clipo-backoffice 봇(bot id `B066N9SC7L2`)이 서논술형 CS 채널에 올리는 알림을 구조화한다.

서논술형(채움AI 서·논술형 평가지원시스템)의 1:1 문의 알림 형태 (신규/미답변):

    👀 2026-09-05 10:12:33 에 인천정각중학교(1021) 교사(30514)로부터 접수된 1:1 문의입니다.
    - 유형 : `계정·접속·권한`
    - 제목 : <https://seononsul.chaeum.ai/admin/inquiries/Ab12Cd|소속 학교 변경 요청>
    ```학기 중 전근으로 소속 학교가 바뀌었습니다. ...```

답변이 완료되면 상태 이모지가 ✅ 로 바뀌고 머리말에 `[답변완료]` 가 붙는다:

    ✅ [답변완료] 2026-09-04 16:40:01 에 세종과학예술영재학교(2210) 교사(11876)로부터 접수된 1:1 문의입니다.
    ...

스레드에는 두 종류의 후속 알림이 달린다.
- `답변완료 등록됨` : 담당자가 등록한 공식 답변 (실제 발송된 답변 본문 포함)  → parse_answer_reply
- `추가문의 등록됨` : 선생님이 남긴 추가 문의(재질문)                       → parse_inquiry(kind="followup")

상태 이모지는 유니코드(✅/👀/💬)로 오거나 Slack 숏코드(:white_check_mark:/:eyes:/:speech_balloon:)로
올 수 있으므로, 판별은 이모지가 아니라 문장(마커 텍스트)으로 한다.

이 모듈은 표준 라이브러리만 쓰며 clipo_cs_bot 을 포함해 어떤 외부 패키지도 import 하지 않는다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

InquiryKind = Literal["new", "answered", "followup"]

_MAILTO_RE = re.compile(r"<mailto:([^|>]+)\|[^>]*>")
_LABELED_LINK_RE = re.compile(r"<(https?://[^|>]+)\|([^>]*)>")
_BARE_LINK_RE = re.compile(r"<(https?://[^>]+)>")
_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")

# 헤더 한 줄: {날짜} 에 {학교명}({학교ID}) 교사({교사ID})로부터 접수된 1:1 문의입니다.
_HEADER_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*에\s*(.+?)\((\d+)\)\s*교사\((\d+)\)\s*로부터\s*접수된\s*1:1\s*문의입니다"
)
_CATEGORY_RE = re.compile(r"유형\s*:\s*`([^`]+)`")
_TITLE_RE = re.compile(r"제목\s*:\s*<(https?://[^|>]+)\|([^>]*)>")
_CODE_BLOCK_RE = re.compile(r"```(.*?)```", re.DOTALL)
_INQUIRY_ID_RE = re.compile(r"/inquiries/([A-Za-z0-9_-]+)")
_LINK_URL_RE = re.compile(r"<(https?://[^|>]+)(?:\|[^>]*)?>")

# 후속(스레드) 알림 마커
_ANSWER_MARKER = "답변완료 등록됨"
_FOLLOWUP_MARKER = "추가문의 등록됨"
_INQUIRY_MARKER = "접수된 1:1 문의입니다"


def clean_slack_text(text: str) -> str:
    """Slack mrkdwn 링크/이스케이프를 사람이 읽는 평문으로 바꾼다."""
    text = _MAILTO_RE.sub(r"\1", text)
    text = _LABELED_LINK_RE.sub(lambda m: f"{m.group(2)} ({m.group(1)})" if m.group(2) else m.group(1), text)
    text = _BARE_LINK_RE.sub(r"\1", text)
    text = _USER_MENTION_RE.sub(r"@\1", text)
    return html.unescape(text).strip()


def inquiry_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _INQUIRY_ID_RE.search(url)
    return m.group(1) if m else None


@dataclass
class Inquiry:
    kind: InquiryKind
    ts: str
    raw_text: str
    category: str = ""  # 원본 enum (백오피스 유형명 그대로)
    title: str = ""
    url: str = ""
    content: str = ""  # 본문 (코드 블록)
    school_name: str = ""
    school_id: str = ""
    teacher_id: str = ""
    received_at: str = ""  # date
    thread_ts: str = ""

    @property
    def status(self) -> str:
        """답변 완료 여부. answered = ✅/[답변완료], 그 외(신규·추가문의) = open."""
        return "answered" if self.kind == "answered" else "open"

    @property
    def inquiry_id(self) -> str | None:
        return inquiry_id_from_url(self.url)

    @property
    def is_pending(self) -> bool:
        """아직 담당자 답변이 필요한 상태(신규 문의 또는 추가문의)."""
        return self.kind in ("new", "followup")

    def describe(self) -> str:
        """Claude 프롬프트/로그에 넣기 위한 요약."""
        lines = []
        if self.kind == "followup":
            lines.append("[추가문의] 기존 문의에 대한 선생님의 추가 질문")
        else:
            label = "답변 완료" if self.kind == "answered" else "신규 문의"
            lines.append(f"[{label}] 접수 시각: {self.received_at or '?'}")
        if self.category:
            lines.append(f"문의 유형(원본): {self.category}")
        if self.title:
            lines.append(f"문의 제목: {self.title}")
        if self.school_name or self.teacher_id:
            lines.append(
                f"문의 유저: {self.school_name}(학교ID {self.school_id}) 교사ID {self.teacher_id}"
            )
        if self.url:
            lines.append(f"백오피스 링크: {self.url}")
        lines.append("문의 내용:")
        lines.append(self.content or "(내용 없음)")
        return "\n".join(lines)


def detect_kind(text: str) -> Optional[InquiryKind]:
    if _FOLLOWUP_MARKER in text:
        return "followup"
    if _INQUIRY_MARKER in text:
        return "answered" if "[답변완료]" in text else "new"
    return None


def parse_inquiry(text: str, ts: str = "", thread_ts: str = "") -> Optional[Inquiry]:
    """알림 메시지를 Inquiry 로 변환한다. 서논술형 문의 알림 형식이 아니면 None."""
    if not text:
        return None
    kind = detect_kind(text)
    if kind is None:
        return None

    inq = Inquiry(kind=kind, ts=ts, raw_text=text, thread_ts=thread_ts or ts)

    if kind == "followup":
        # 추가문의: 링크(원 문의)와 본문(코드 블록)만 있는 짧은 알림
        if m := _LINK_URL_RE.search(text):
            inq.url = m.group(1)
        code = _CODE_BLOCK_RE.search(text)
        inq.content = html.unescape(code.group(1)).strip() if code else clean_slack_text(text)
        inq.title = "추가문의"
        return inq

    if m := _HEADER_RE.search(text):
        inq.received_at = m.group(1)
        inq.school_name = m.group(2).strip()
        inq.school_id = m.group(3)
        inq.teacher_id = m.group(4)
    if m := _CATEGORY_RE.search(text):
        inq.category = m.group(1).strip()
    if m := _TITLE_RE.search(text):
        inq.url = m.group(1)
        inq.title = html.unescape(m.group(2)).strip()
    code = _CODE_BLOCK_RE.search(text)
    if code:
        inq.content = html.unescape(code.group(1)).strip()
    return inq


@dataclass
class AnsweredReply:
    """스레드에 달린 '답변완료 등록됨' 알림 (실제 발송된 답변 본문 포함)."""

    ts: str
    url: str
    answer: str


def parse_answer_reply(text: str, ts: str = "") -> Optional[AnsweredReply]:
    """스레드의 '답변완료 등록됨' 알림에서 실제 발송된 답변 본문을 추출한다."""
    if not text or _ANSWER_MARKER not in text:
        return None
    url = ""
    if m := _LINK_URL_RE.search(text):
        url = m.group(1)
    code = _CODE_BLOCK_RE.search(text)
    answer = html.unescape(code.group(1)).strip() if code else ""
    return AnsweredReply(ts=ts, url=url, answer=answer)


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


__all__ = [
    "Inquiry",
    "InquiryKind",
    "AnsweredReply",
    "ThreadRecord",
    "parse_inquiry",
    "parse_answer_reply",
    "detect_kind",
    "clean_slack_text",
    "inquiry_id_from_url",
]
