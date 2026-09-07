"""파서 단위 테스트. 실제 #clipo-cs 알림 형식을 그대로 사용한다."""

from clipo_cs_bot.drafter import split_sections, _sanity_check
from clipo_cs_bot.parser import parse_answer_reply, parse_inquiry

NEW_MSG = """:eyes: 2026-09-07 19:29:05 에 접수된 문의입니다.
            - 문의 유형 : `연수 지원 신청`
            - 문의 제목 : <https://clipo.ai/staffs/questions/9sJUjq-P-E6iOeiCwHT1Fw|연수 지원 신청>
            - 문의 내용 : 1. 연수 진행하시는 선생님 성함 및 소속 : 안봉균(청원여자고등학교)
2. 연수 일자 및 시간 : 2026.9.18. 14:30~16:30
3. 연수 대상 : 인천해송고등학교 과학교사
4. 지원 받을 메일 : <mailto:bkan1004@naver.com|bkan1004@naver.com>
5. 연락처 : 010-8026-7213
6. 비고 : 연수 준비로 미리 크레딧이 필요합니다. 충전해 주시면 좋겠습니다.~^^
            - 문의 유저: 청원여자고등학교(2465)의 교사(25610) (누적 문의 수 : 8)
            - <https://clipo.ai/staffs/questions/HS65OkeKGkaft_cER7SFpA|이전 문의보기>"""

ANSWERED_MSG = """:white_check_mark: [답변완료] 2026-09-07 14:53:05 에 접수된 문의입니다.
            - 문의 유형 : `학교 설정(학생/선생님)`
            - 문의 제목 : <https://clipo.ai/staffs/questions/4rFk0HtXuEyfu9hRM6jamA|대표교사 설정>
            - 문의 내용 : 현재 일반교사로 설정되어 있습니다.
대표교사이신 본교(성남서중) 선생님 두 분과, 저를 제외한 나머지 일반교사 선생님 1분 모두 현재 본교에서 근무하지 않습니다. 대표교사로 설정 부탁드립니다.
            - 문의 유저: 성남서중학교(2039)의 교사(46372) (누적 문의 수 : 1)"""

REQUESTION_MSG = """:speech_balloon: 3일 만에 재질문이 접수되었습니다. <https://clipo.ai/staffs/questions/MY5JhpGMUEmzXZPsT-svHg|바로가기>

```명확한 답변 감사드립니다.

과제 및 평가 글쓰기는 다양한 기호(동영상, 사진, 그림 등)를 활용할 수 있습니다.
클리포 평가 시스템과 피드백이 훌륭하여 반드시 이 부분이 개선되었으면 합니다!```"""

ANSWER_REPLY = """:white_check_mark: 3 hours 만에 답변 완료했습니다. <https://clipo.ai/staffs/questions/ayJlmvN3zESjNfoX-rsTUg|바로가기>

```안녕하세요, 선생님.
클리포입니다.

결론부터 말씀드리면, 과제물 업로드 방법 설정을 이용해 공개했다가 비공개로 전환하실 수 있습니다.

감사합니다.```"""


def test_parse_new_inquiry():
    inq = parse_inquiry(NEW_MSG, "1788776946.730899")
    assert inq is not None
    assert inq.kind == "new"
    assert inq.is_pending
    assert inq.category == "연수 지원 신청"
    assert inq.title == "연수 지원 신청"
    assert inq.question_id == "9sJUjq-P-E6iOeiCwHT1Fw"
    assert inq.school_name == "청원여자고등학교"
    assert inq.school_id == "2465"
    assert inq.teacher_id == "25610"
    assert inq.inquiry_count == 8
    assert inq.received_at == "2026-09-07 19:29:05"
    assert inq.prev_inquiries_url.endswith("HS65OkeKGkaft_cER7SFpA")
    # mailto 링크가 평문 주소로 정리되고, 내용이 '문의 유저' 줄 앞에서 끊긴다
    assert "bkan1004@naver.com" in inq.content
    assert "<mailto:" not in inq.content
    assert "문의 유저" not in inq.content
    assert inq.content.startswith("1. 연수 진행하시는")
    assert inq.content.endswith("~^^")


def test_parse_answered_inquiry_is_not_pending():
    inq = parse_inquiry(ANSWERED_MSG, "1")
    assert inq is not None
    assert inq.kind == "answered"
    assert not inq.is_pending
    assert inq.category == "학교 설정(학생/선생님)"
    assert inq.prev_inquiries_url == ""
    assert inq.content.endswith("대표교사로 설정 부탁드립니다.")


def test_parse_requestion():
    inq = parse_inquiry(REQUESTION_MSG, "2")
    assert inq is not None
    assert inq.kind == "requestion"
    assert inq.is_pending
    assert inq.elapsed == "3일"
    assert inq.question_id == "MY5JhpGMUEmzXZPsT-svHg"
    assert inq.content.startswith("명확한 답변 감사드립니다.")
    assert inq.content.endswith("개선되었으면 합니다!")


def test_non_inquiry_messages_are_ignored():
    assert parse_inquiry("차주 월요일에 클리포 고객센터 응대 좀 부탁 드립니다!", "3") is None
    assert parse_inquiry("", "4") is None
    assert parse_inquiry(ANSWER_REPLY, "5") is None  # 답변 알림은 문의가 아님


def test_parse_answer_reply():
    ans = parse_answer_reply(ANSWER_REPLY, "6")
    assert ans is not None
    assert ans.url.endswith("ayJlmvN3zESjNfoX-rsTUg")
    assert ans.answer.startswith("안녕하세요, 선생님.")
    assert ans.answer.endswith("감사합니다.")


def test_split_sections_and_sanity():
    text = """[분류] 연수 지원 신청
[응대 초안]
안녕하세요, 선생님. 감사합니다.
[크로스체크 필요]
- 크레딧 쿠폰 수량
[에스컬레이션]
CS 담당 태그"""
    sec = split_sections(text)
    assert set(sec) == {"분류", "응대 초안", "크로스체크 필요", "에스컬레이션"}
    assert sec["분류"] == "연수 지원 신청"
    assert sec["응대 초안"] == "안녕하세요, 선생님. 감사합니다."
    assert _sanity_check(sec) == []


def test_sanity_flags_money_in_body_and_missing_sections():
    sec = split_sections("[분류] 요금제\n[응대 초안]\n총 금액은 1,200,000원입니다.")
    warnings = _sanity_check(sec)
    assert any("금액" in w for w in warnings)
    assert any("빠졌습니다" in w for w in warnings)
