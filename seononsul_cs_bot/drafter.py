"""Claude 로 1차 응대 초안을 생성한다 (서논술형).

- 시스템 프롬프트 = 고정 지침 + 노션 가이드 전문 (가이드 블록은 prompt caching, cache_control: ephemeral)
- 사용자 메시지 = 이번 문의 + 채널 이력에서 모은 컨텍스트
- 출력 = 가이드 §11 형식: [분류] / [응대 초안] / [확인 필요] / [에스컬레이션]
- 계정/권한/소속 변경·삭제 등 되돌리기 어려운 처리는 확답하지 않고 "담당자 확인 후 처리" 안내로 강제한다.

clipo_cs_bot 을 import 하지 않는 독립 모듈이다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import anthropic

from .config import Settings
from .context import DraftContext
from .parser import Inquiry

log = logging.getLogger(__name__)

SECTION_NAMES = ("분류", "응대 초안", "확인 필요", "에스컬레이션")
_SECTION_RE = re.compile(r"^\s*\*?\[(분류|응대 초안|확인 필요|에스컬레이션)\]\*?\s*", re.MULTILINE)

# 8개 분류 체계 (백오피스 원본 enum 과 별개로, 봇이 초안에 붙이는 정규화된 유형)
TAXONOMY = (
    "계정·접속·권한",
    "과목·성취기준",
    "수업·평가 설계",
    "AI 채점·배점",
    "AI 피드백 품질",
    "과제물·학생 관리",
    "평가 자료실·리포트",
    "시스템 오류·기타",
)

STATIC_INSTRUCTIONS = """당신은 '채움AI 서·논술형 평가지원시스템'(이하 서논술형) 고객센터의 1차 응대 도우미입니다.
서논술형은 공교육 선생님이 서술형·논술형 평가를 설계하고 AI 채점·피드백을 활용하도록 돕는 서비스이며, 문의자는 대부분 중·고등학교 선생님입니다.

역할: 회사 슬랙 서논술형 CS 채널에 새로 접수된 1:1 문의에 대해, 담당자가 검수 후 바로 발송할 수 있는 수준의 **1차 응대 초안**을 작성합니다.
당신의 초안은 선생님에게 직접 발송되지 않습니다. 반드시 사람이 검수·수정한 뒤 발송합니다.

아래 <guide> 는 회사가 관리하는 공식 응대 가이드입니다. 톤, 응대 원칙, 유형별 템플릿, 절대 규칙, 에스컬레이션 라우팅을 이 가이드에 따르세요.
가이드와 이 지침이 충돌하면 가이드를 우선하되, 다음 규칙은 무조건 지킵니다.

1. **되돌리기 어려운 처리는 확답하지 않습니다.** 계정 생성·삭제, 권한 변경, 소속(학교) 변경, 대표교사 변경, 학생/과제물/데이터 삭제, 병합·이관처럼
   한 번 수행하면 되돌리기 어려운 작업은 "바로 처리해 드리겠습니다"라고 단정하지 말고, **"담당자 확인 후 처리"** 절차만 안내합니다.
   초안에서는 필요한 확인 정보를 요청하고, [에스컬레이션] 에 담당자 처리 항목으로 넘깁니다.
2. 서논술형에는 **비용·결제·크레딧·요금·할인 개념이 없습니다.** 문의에 그런 표현이 있어도 비용 관련 안내를 하지 말고, 오해가 있으면 정중히 바로잡거나 담당자 확인으로 넘깁니다.
3. 확실하지 않은 정책·기능 동작·일정은 단정하지 말고 [확인 필요] 에 사람이 확인할 항목으로 올립니다.

컨텍스트 활용:
- <context> 에는 같은 선생님의 과거 문의·실제 답변, 같은 유형의 최근 모범 답변, (추가문의이면) 원 문의 스레드가 들어 있습니다.
- 과거 실제 답변의 인사·문단 구성·마무리 톤을 따르되, 문장을 그대로 복사하지 말고 이번 문의에 맞게 쓰세요.
- 과거 답변에 적힌 구체 정책·기능 동작이 지금도 유효하다고 단정하지 마세요. 확실하지 않으면 [확인 필요] 에 올립니다.
- 스레드 내부 메모는 담당자끼리의 논의이므로 선생님께 보내는 초안에 노출하지 않습니다.

분류 체계(이 8가지 중 하나만 [분류] 에 씁니다):
1 계정·접속·권한 / 2 과목·성취기준 / 3 수업·평가 설계 / 4 AI 채점·배점 / 5 AI 피드백 품질 / 6 과제물·학생 관리 / 7 평가 자료실·리포트 / 8 시스템 오류·기타

출력 형식 (이 네 블록만, 순서 고정, 다른 머리말·해설 금지):

[분류] {위 8가지 유형명 하나}
[응대 초안]
{선생님에게 바로 보낼 수 있는 완성 문장. 반드시 "안녕하세요, 선생님. AI 서·논술형 평가지원시스템 고객센터입니다." 로 시작하고 정중히 마무리합니다. 되돌리기 어려운 처리는 확답 대신 확인 절차를 안내합니다.}
[확인 필요]
{담당자가 발송 전 확인해야 할 항목을 "- " 목록으로. 없으면 "확인 필요 항목 없음"}
[에스컬레이션]
{담당자 처리/전달이 필요하면 어느 버킷(개발팀 / AI / PRD·기획팀)인지와 이유를, 플레이스홀더 멘션(@개발팀-담당자 / @AI-담당자 / @기획팀-담당자)으로 적습니다. 정보성 답변이면 "정보성 답변 — 즉시 회신 가능"}
"""


@dataclass
class Draft:
    raw: str
    sections: dict[str, str]
    model: str
    warnings: list[str]

    @property
    def category(self) -> str:
        return self.sections.get("분류", "").strip()

    @property
    def body(self) -> str:
        return self.sections.get("응대 초안", "").strip()


def split_sections(text: str) -> dict[str, str]:
    """[분류]/[응대 초안]/[확인 필요]/[에스컬레이션] 블록으로 나눈다. 형식이 깨졌으면 빈 dict."""
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return {}
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[m.group(1)] = text[m.end():end].strip()
    return out


def _sanity_check(sections: dict[str, str]) -> list[str]:
    warnings = []
    missing = [name for name in SECTION_NAMES if name not in sections]
    if missing:
        warnings.append(f"출력 형식에 {', '.join(missing)} 블록이 빠졌습니다.")
    category = sections.get("분류", "").strip()
    if category and not any(t in category for t in TAXONOMY):
        warnings.append(f"[분류] 값 '{category}' 이 8개 표준 유형에 없습니다. 담당자 확인 필요.")
    body = sections.get("응대 초안", "")
    if body and "안녕하세요, 선생님. AI 서·논술형 평가지원시스템 고객센터입니다." not in body:
        warnings.append("응대 초안이 표준 인사말로 시작하지 않습니다.")
    return warnings


class Drafter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def _system(self, guide: str) -> list[dict]:
        return [
            {"type": "text", "text": STATIC_INSTRUCTIONS},
            {
                "type": "text",
                "text": f"<guide>\n{guide}\n</guide>",
                "cache_control": {"type": "ephemeral"},
            },
        ]

    @staticmethod
    def _user_message(inquiry: Inquiry, context: DraftContext) -> str:
        return (
            "<inquiry>\n" + inquiry.describe() + "\n</inquiry>\n\n"
            "<context>\n" + context.describe() + "\n</context>\n\n"
            "위 문의에 대한 1차 응대 초안을 지정된 출력 형식으로 작성하세요."
        )

    def draft(self, inquiry: Inquiry, context: DraftContext, guide: str) -> Draft:
        s = self.settings
        kwargs: dict = dict(
            model=s.claude_model,
            max_tokens=s.claude_max_tokens,
            system=self._system(guide),
            messages=[{"role": "user", "content": self._user_message(inquiry, context)}],
            thinking={"type": "adaptive"},
            output_config={"effort": s.claude_effort},
        )
        if s.claude_fallbacks:
            # 안전 분류기가 요청을 거절하면 서버가 대체 모델로 같은 요청을 다시 실행한다.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except anthropic.BadRequestError as e:
            if s.claude_fallbacks and "fallback" in str(e).lower():
                # 플랫폼이 fallbacks 를 지원하지 않는 경우: 한 번만 빼고 재시도
                log.warning("fallbacks 미지원 응답, fallbacks 없이 재시도: %s", e)
                kwargs.pop("betas", None)
                kwargs.pop("fallbacks", None)
                with self.client.beta.messages.stream(**kwargs) as stream:
                    message = stream.get_final_message()
            else:
                raise

        if message.stop_reason == "refusal":
            raise RuntimeError("모델이 요청을 거절했습니다(stop_reason=refusal). 담당자가 직접 작성해 주세요.")

        text = "".join(block.text for block in message.content if block.type == "text").strip()
        sections = split_sections(text)
        warnings = _sanity_check(sections)
        if message.stop_reason == "max_tokens":
            warnings.append("출력이 max_tokens 에서 잘렸습니다. 초안이 불완전할 수 있습니다.")
        usage = message.usage
        log.info(
            "초안 생성 완료 model=%s in=%s cache_read=%s out=%s stop=%s",
            message.model,
            usage.input_tokens,
            getattr(usage, "cache_read_input_tokens", None),
            usage.output_tokens,
            message.stop_reason,
        )
        return Draft(raw=text, sections=sections, model=message.model, warnings=warnings)
