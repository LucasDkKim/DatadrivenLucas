"""서논술형 파서 단위 테스트. 실제 backoffice 알림 형식을 익명화해 그대로 사용한다.

parser 모듈만 import 하므로 (표준 라이브러리만 사용) 무거운 의존성 없이 실행된다.
"""

from seononsul_cs_bot.parser import parse_answer_reply, parse_inquiry

# 1) 계정·소속 변경 (미답변, 👀). 되돌리기 어려운 처리 유형.
ACCOUNT_MSG = """👀 2026-09-05 10:12:33 에 인천정각중학교(1021) 교사(30514)로부터 접수된 1:1 문의입니다.
- 유형 : `계정·접속·권한`
- 제목 : <https://seononsul.chaeum.ai/admin/inquiries/Ab12Cd34Ef|소속 학교 변경 요청>
```학기 중 전근으로 소속 학교가 바뀌었습니다.
기존 계정의 소속을 새 학교로 변경할 수 있을까요? 학생 명단과 과제물은 그대로 유지되면 좋겠습니다.```"""

# 2) AI 채점·배점 (답변완료, ✅ [답변완료]).
GRADING_MSG = """✅ [답변완료] 2026-09-04 16:40:01 에 세종과학예술영재학교(2210) 교사(11876)로부터 접수된 1:1 문의입니다.
- 유형 : `AI 채점·배점`
- 제목 : <https://seononsul.chaeum.ai/admin/inquiries/Zx98Yw76Uv|서술형 문항 배점 반영 문의>
```학생 답안에 대해 AI 채점 결과가 나왔는데, 제가 설정한 배점표와 다르게 점수가 매겨진 것 같습니다.
부분 점수 기준은 어떻게 반영되나요?```"""

# 3) 과목 미개설 (미답변, 👀).
SUBJECT_MSG = """👀 2026-09-06 09:03:20 에 대전도안고등학교(3305) 교사(44120)로부터 접수된 1:1 문의입니다.
- 유형 : `과목·성취기준`
- 제목 : <https://seononsul.chaeum.ai/admin/inquiries/Qr55Tt66Ww|통합사회 과목이 목록에 없어요>
```2학년 통합사회 서논술형 평가를 준비 중인데, 과목 선택 목록에 통합사회가 보이지 않습니다.
아직 개설되지 않은 과목인가요? 개설 예정이라면 일정이 궁금합니다.```"""

# 스레드: 담당자 공식 답변 (답변완료 등록됨)
ANSWER_REPLY = """✅ 답변완료 등록됨 <https://seononsul.chaeum.ai/admin/inquiries/Zx98Yw76Uv|바로가기>
```안녕하세요, 선생님. AI 서·논술형 평가지원시스템 고객센터입니다.
배점 기준은 문항 편집 화면의 '배점 기준'에서 확인하실 수 있습니다.
감사합니다.```"""

# 스레드: 선생님의 추가문의 (추가문의 등록됨)
FOLLOWUP_MSG = """💬 추가문의 등록됨 <https://seononsul.chaeum.ai/admin/inquiries/Ab12Cd34Ef|바로가기>
```안내 감사합니다. 그런데 소속 변경 후에도 이전 학교에서 만든 리포트를 계속 볼 수 있나요?```"""


def test_parse_account_inquiry_open():
    inq = parse_inquiry(ACCOUNT_MSG, "1788776946.730899")
    assert inq is not None
    assert inq.kind == "new"
    assert inq.status == "open"
    assert inq.is_pending
    assert inq.category == "계정·접속·권한"
    assert inq.title == "소속 학교 변경 요청"
    assert inq.inquiry_id == "Ab12Cd34Ef"
    assert inq.school_name == "인천정각중학교"
    assert inq.school_id == "1021"
    assert inq.teacher_id == "30514"
    assert inq.received_at == "2026-09-05 10:12:33"
    assert inq.thread_ts == "1788776946.730899"
    assert inq.content.startswith("학기 중 전근으로")
    assert inq.content.endswith("유지되면 좋겠습니다.")


def test_parse_grading_inquiry_answered_not_pending():
    inq = parse_inquiry(GRADING_MSG, "1788770000.100000")
    assert inq is not None
    assert inq.kind == "answered"
    assert inq.status == "answered"
    assert not inq.is_pending
    assert inq.category == "AI 채점·배점"
    assert inq.school_name == "세종과학예술영재학교"
    assert inq.school_id == "2210"
    assert inq.teacher_id == "11876"
    assert inq.inquiry_id == "Zx98Yw76Uv"
    assert inq.content.endswith("반영되나요?")


def test_parse_subject_inquiry_open():
    inq = parse_inquiry(SUBJECT_MSG, "1788780000.200000")
    assert inq is not None
    assert inq.kind == "new"
    assert inq.is_pending
    assert inq.category == "과목·성취기준"
    assert inq.school_name == "대전도안고등학교"
    assert inq.teacher_id == "44120"
    assert inq.title == "통합사회 과목이 목록에 없어요"
    assert inq.content.startswith("2학년 통합사회")


def test_parse_followup_inquiry():
    inq = parse_inquiry(FOLLOWUP_MSG, "1788790000.300000", thread_ts="1788776946.730899")
    assert inq is not None
    assert inq.kind == "followup"
    assert inq.is_pending
    assert inq.status == "open"
    assert inq.inquiry_id == "Ab12Cd34Ef"
    assert inq.thread_ts == "1788776946.730899"
    assert inq.content.startswith("안내 감사합니다.")
    assert inq.content.endswith("계속 볼 수 있나요?")


def test_non_inquiry_messages_are_ignored():
    assert parse_inquiry("오늘 서논술형 CS 당번은 누구인가요?", "3") is None
    assert parse_inquiry("", "4") is None
    # 답변완료 등록됨(공식 답변)은 문의가 아님
    assert parse_inquiry(ANSWER_REPLY, "5") is None


def test_parse_answer_reply():
    ans = parse_answer_reply(ANSWER_REPLY, "6")
    assert ans is not None
    assert ans.url.endswith("Zx98Yw76Uv")
    assert ans.answer.startswith("안녕하세요, 선생님. AI 서·논술형 평가지원시스템 고객센터입니다.")
    assert ans.answer.endswith("감사합니다.")
    # 추가문의 알림은 공식 답변이 아니므로 None
    assert parse_answer_reply(FOLLOWUP_MSG, "7") is None
