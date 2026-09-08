"""Claude 로 1차 응대 초안을 생성한다.

- 시스템 프롬프트 = 고정 지침 + 노션 가이드 전문 (가이드 블록은 prompt caching)
- 사용자 메시지 = 이번 문의 + 채널 이력에서 모은 컨텍스트
- 출력 = 가이드 §11 형식: [분류] / [응대 초안] / [크로스체크 필요] / [에스컬레이션]
- 금액·크레딧 숫자는 절대 계산하지 않는다는 규칙을 프롬프트와 후처리 두 겹으로 지킨다.
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

SECTION_NAMES = ("분류", "응대 초안", "크로스체크 필요", "에스컬레이션")
_SECTION_RE = re.compile(r"^\s*\*?\[(분류|응대 초안|크로스체크 필요|에스컬레이션)\]\*?\s*", re.MULTILINE)

# 초안 본문에 들어가면 안 되는 숫자 패턴 (금액/크레딧/할인/잔여). 걸리면 경고 배지를 붙인다.
_MONEY_RE = re.compile(
    r"(\d[\d,]*\s*(원|만원|천원|크레딧|credit|%|퍼센트|일\s*남|일\s*잔여))|(잔여|소진|차감)\s*[:：]?\s*\d",
    re.IGNORECASE,
)

STATIC_INSTRUCTIONS = """당신은 '클리포(CLIPO)' 고객센터의 1차 응대 도우미입니다.
클리포는 공교육 선생님과 학생을 위한 교사 친화적 AI 수행평가·글쓰기 서비스이며, 문의자는 대부분 중·고등학교 선생님입니다.

역할: 회사 CS 슬랙 채널에 새로 접수된 문의에 대해, 담당자가 검수 후 바로 발송할 수 있는 수준의 **1차 응대 초안**을 작성합니다.
당신의 초안은 선생님에게 직접 발송되지 않습니다. 반드시 사람이 검수·수정한 뒤 발송합니다.

아래 <guide> 는 회사가 관리하는 공식 응대 가이드입니다. 톤, 응대 원칙, 유형별 템플릿, 절대 규칙, 에스컬레이션 라우팅을 이 가이드에 따르세요.
가이드와 이 지침이 충돌하면 가이드를 우선하되, 다음 두 가지는 무조건 지킵니다.

1. 금액·가격·크레딧 수량·차감량·잔여량·소진일·할인율·정산액·무료체험 잔여일 등 **숫자를 초안에 계산하거나 옮겨 적지 않습니다.**
   문의에 숫자가 있어도 초안에는 `{담당자 확인 후 기입}` 으로 비워 두고, [크로스체크 필요] 블록에 사람이 확인할 항목으로 적습니다.
   (연수 일자·시간·인원처럼 문의자가 알려준 일정 정보는 그대로 써도 됩니다. 돈·크레딧과 관련된 숫자만 금지입니다.)
2. 비밀번호 초기화, 계정 이메일 변경, 대표교사 변경, 환불, 크레딧 재지급, 결제 취소, 타인 계정·학생 정보 열람은 **확답하지 않고 "확인 후 처리" 절차만 안내**합니다.

컨텍스트 활용:
- <context> 에는 같은 선생님의 과거 문의·실제 답변, 같은 유형의 최근 모범 답변, (재질문이면) 원 문의 스레드가 들어 있습니다.
- 과거 실제 답변의 인사·문단 구성·마무리 톤을 따르되, 문장을 그대로 복사하지 말고 이번 문의에 맞게 쓰세요.
- 같은 선생님이 반복 요청해 온 것(예: 매번 나이스 캡처 없이 인증 요청)은 묻기 전에 먼저 챙겨 주는 것이 최고의 개인화입니다. 단, 이력을 나열하며 "지켜봤다"는 식으로 부담을 주지 마세요.
- 과거 답변에 적힌 구체 정책·숫자·기능 동작이 지금도 유효하다고 단정하지 마세요. 확실하지 않으면 [크로스체크 필요] 에 올립니다.
- 스레드 내부 메모는 담당자끼리의 논의이므로 선생님께 보내는 초안에 노출하지 않습니다.
- <context> 에 '관련 내부 채널 최근 논의' 가 있으면 사실 파악·이관 판단에만 쓰고, 그 내부 논의 문구를 선생님께 보내는 초안에 그대로 옮기지 마세요.

출력 형식 (이 네 블록만, 순서 고정, 다른 머리말·해설 금지):

[분류] {가이드의 유형명 하나}
[응대 초안]
{선생님에게 바로 보낼 수 있는 완성 문장. "안녕하세요, 선생님." 으로 시작해 "감사합니다." 로 끝냅니다. 금액·크레딧 자리는 {담당자 확인 후 기입}}
[크로스체크 필요]
{사람이 확인·계산해야 할 항목을 "- " 목록으로. 없으면 "숫자·금액 없음 — 해당 없음"}
[에스컬레이션]
{필요 시 담당자/처리 안내. 정보성 답변이면 "정보성 답변 — 즉시 회신 가능"}
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
    """[분류]/[응대 초안]/... 블록으로 나눈다. 형식이 깨졌으면 빈 dict."""
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
    body = sections.get("응대 초안", "")
    if _MONEY_RE.search(body):
        warnings.append("초안 본문에 금액/크레딧/할인/잔여 숫자로 보이는 표현이 있습니다. 담당자 확인 전 발송 금지.")
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
