# 서논술형 CS 1차 응대 도우미

**채움AI 서·논술형 평가지원시스템**(이하 서논술형) 고객센터용 슬랙 봇입니다.
서논술형 CS 슬랙 채널에 backoffice 봇(`clipo-backoffice`, bot id `B066N9SC7L2`)이 올리는 **1:1 문의 알림을 감지**하고,
Notion RAG 가이드와 채널의 과거 답변 이력을 참고해 **1차 응대 초안을 같은 스레드에 댓글로** 올립니다.

초안은 선생님께 **직접 발송되지 않습니다.** 담당자가 검수·수정한 뒤 백오피스에 등록하는 것이 전제입니다.
봇은 문의가 접수된 내부 스레드 안에만 댓글을 달며, 고객(선생님)에게 나가는 어떤 발송도 하지 않습니다.

> 이 패키지는 CLIPO CS 봇(`clipo_cs_bot`)의 검증된 구조를 본떠 만들었지만 **완전히 독립적**입니다.
> `clipo_cs_bot` 을 포함해 어떤 것도 import 하지 않으며, 자체 README/.env.example/requirements 를 가져
> 언제든 별도 저장소로 그대로 옮길 수 있습니다(저장소 이식성).

## 동작 흐름

```
서논술형 1:1 문의 접수
   └─ backoffice 봇 → CS 채널에 "👀 ... 로부터 접수된 1:1 문의입니다" 알림
         └─ [이 봇] Slack 이벤트 수신 (Socket Mode) 또는 폴링
               ├─ 알림 파싱: 유형 / 제목 / 본문 / 학교(ID) / 교사ID / 상태
               ├─ 컨텍스트 수집 (채널 이력, 최근 120일)  ← 경량 RAG (서논술형)
               │     ├─ 같은 교사의 과거 문의 + 실제 발송 답변      → 개인화
               │     ├─ 같은 유형의 최근 답변 완료 사례 (few-shot)  → 톤·형식
               │     └─ 추가문의면 원 문의 스레드                  → 대화 맥락
               ├─ 가이드 로드: Notion(1시간 캐시) → 캐시 → guide/seononsul_guide.md 스냅샷
               ├─ Claude 호출 (시스템 프롬프트 = 고정 지침 + 가이드 전문, 가이드 블록은 prompt caching)
               ├─ 후처리: [분류]/[응대 초안]/[확인 필요]/[에스컬레이션] 4블록 검사
               └─ 스레드 댓글 게시 (초안 본문은 복사하기 쉽게 코드 블록)
```

감지 대상:

| 알림 | 예 | 처리 |
|---|---|---|
| 신규 문의 | `👀 2026-09-05 ... 로부터 접수된 1:1 문의입니다.` | 초안 생성 |
| 추가문의 | `💬 추가문의 등록됨 <링크\|바로가기>` + 본문 | 원 문의 스레드 맥락 포함 후 초안 생성 |
| 답변 완료 | `✅ [답변완료] ...` | 건너뜀 (컨텍스트 자료로만 사용) |
| 답변완료 등록됨 | `✅ 답변완료 등록됨 <링크\|바로가기>` + 답변 본문 | 실제 발송 답변으로 few-shot 컨텍스트에 사용 |

상태 이모지는 유니코드(👀/✅/💬)로 오거나 Slack 숏코드로 올 수 있어, 판별은 이모지가 아니라 문장(마커 텍스트)으로 합니다.

중복 댓글은 두 겹으로 막습니다. 처리한 메시지 ts 를 `.state/seononsul_processed.json` 에 기록하고, 게시 전에 스레드에 이미 봇 댓글이 있는지 확인합니다.

## 분류 체계 (8종)

1 계정·접속·권한 / 2 과목·성취기준 / 3 수업·평가 설계 / 4 AI 채점·배점 / 5 AI 피드백 품질 / 6 과제물·학생 관리 / 7 평가 자료실·리포트 / 8 시스템 오류·기타

## 초안이 지키는 규칙

가이드(§0·§10·§11)의 규칙을 프롬프트와 코드 양쪽에서 강제합니다.

- **되돌리기 어려운 처리는 확답 금지.** 계정 생성·삭제, 권한·소속(학교) 변경, 대표교사 변경, 학생/과제물/데이터 삭제·병합·이관은 "담당자 확인 후 처리" 절차만 안내하고 [에스컬레이션] 으로 넘깁니다.
- **비용·결제·크레딧 안내 금지.** 서논술형에는 비용 개념이 없습니다.
- 출력은 항상 `[분류] / [응대 초안] / [확인 필요] / [에스컬레이션]` 4블록. 블록 누락·본문 잘림은 경고 배지가 붙습니다.
- 에스컬레이션은 3버킷: **개발팀 / AI / PRD·기획팀** (플레이스홀더 멘션 사용). 기획팀 1차 담당자가 대응이 어려우면 팀원을 허들로 소환해 함께 검토합니다.
- Notion 문서를 고치면 다음 갱신 주기(기본 60분)부터 초안에 반영됩니다.

## 설치

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 값 채우기
```

### 1. Slack 앱

`slack_manifest.yaml` 로 앱을 만들고(From a manifest), 봇 토큰(`xoxb-`)을 `SLACK_BOT_TOKEN`, 앱 레벨 토큰(`xapp-`, scope `connections:write`)을 `SLACK_APP_TOKEN` 에 넣습니다. CS 채널에 봇을 초대합니다.
매니페스트 스코프: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `chat:write`. 이벤트: `message.channels`, `message.groups`.

### 2. Notion 가이드 (선택)

RAG 권위본은 Notion 페이지 **[🎯 서·논술형(채움AI) CS 1차 응대 RAG 참조 문서](https://app.notion.com/p/3d617e5c8cf1818cbe2bd59363071121)** 입니다. 이 페이지 ID(`3d617e5c8cf1818cbe2bd59363071121`)가 `config.py` 의 `NOTION_PAGE_ID` 기본값으로 설정되어 있습니다(환경 변수로 override 가능).

`NOTION_TOKEN` 을 넣고 위 페이지를 통합(Integration)에 공유하면 런타임이 Notion 최신본을 우선 사용하고, 조회 실패 시 저장소 스냅샷 `guide/seononsul_guide.md` 로 폴백합니다. **`NOTION_TOKEN` 이 없으면** 페이지 ID 가 설정돼 있어도 Notion 을 건너뛰고 곧장 스냅샷을 사용합니다. 스냅샷은 이 Notion 권위본과 동기화된 폴백본입니다.

```bash
python -m seononsul_cs_bot guide   # 가이드 출처/내용 확인
```

### 3. Claude

`ANTHROPIC_API_KEY` 를 넣습니다. 기본 모델 `claude-opus-5`, effort `high`.

## 실행

```bash
python -m seononsul_cs_bot run                      # 실시간 감시 (Socket Mode, 권장)
python -m seononsul_cs_bot poll                     # 폴링 (봇 토큰만)
python -m seononsul_cs_bot draft --ts 1788776946.730899          # 초안만 터미널 출력 (슬랙에 안 올림)
python -m seononsul_cs_bot draft --ts 1788776946.730899 --post   # 확인 후 스레드에 게시
python -m seononsul_cs_bot backfill --hours 24      # 최근 N시간 미응대 문의 일괄 처리
python -m seononsul_cs_bot guide                    # 가이드 로드 확인
```

`DRY_RUN=true` 로 두면 어떤 모드에서도 슬랙에 올리지 않고 터미널에만 출력합니다. 시범 운영 첫날 권장 설정입니다.

### 테스트

```bash
pip install pytest && python -m pytest -q tests/test_seononsul_parser.py
```

## 운영 원칙 — draft-only, human review before send

이 봇은 **초안만** 만듭니다. 초안은 선생님에게 자동 발송되지 않으며, 담당자가 검수·수정한 뒤에만 발송됩니다.
되돌리기 어려운 작업(계정·권한·소속·삭제)은 확답하지 않고 담당자 확인 절차로 넘기는 것이 핵심 안전장치입니다.

## 구조

```
seononsul_cs_bot/
  config.py     환경 변수 → Settings
  parser.py     backoffice 알림 파싱 (신규/답변완료/추가문의), 공식 답변 본문 추출
  context.py    채널 이력에서 개인화·few-shot·원 스레드 수집 (경량 RAG)
  guide.py      Notion → 마크다운 변환, 캐시, 스냅샷 폴백
  drafter.py    Claude 호출, 4블록 파싱, 안전 규칙 후처리
  slack_app.py  Socket Mode / 폴링, 중복 방지, 스레드 댓글 포맷
  __main__.py   CLI (run / poll / draft / backfill / guide)
  guide/seononsul_guide.md   RAG 가이드 스냅샷 (폴백)
  slack_manifest.yaml        Slack 앱 매니페스트
  .env.example / requirements.txt
tests/test_seononsul_parser.py   파서 단위 테스트
```
