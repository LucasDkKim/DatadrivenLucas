# 클리포 고객센터 1차 응대 도우미

`#clipo-cs` 슬랙 채널에 clipo-backoffice 봇이 올리는 **신규 문의 알림을 감지**하고, 노션의
[클리포 CS 1차 응대 RAG 참조 문서](https://app.notion.com/p/3d417e5c8cf181afbf54eb5e23de248b)와
채널의 과거 답변 이력을 참고해 **1차 응대 초안을 같은 스레드에 댓글로** 올립니다.

초안은 선생님께 직접 발송되지 않습니다. 담당자가 검수·수정한 뒤 clipo.ai 백오피스에 등록하는 것이 전제입니다.

## 동작 흐름

```
clipo.ai 문의 접수
   └─ clipo-backoffice 봇 → #clipo-cs 에 ":eyes: ... 에 접수된 문의입니다" 알림
         └─ [이 봇] Slack 이벤트 수신 (Socket Mode) 또는 폴링
               ├─ 알림 파싱: 유형 / 제목 / 내용 / 학교 / 교사ID / 누적 문의 수
               ├─ 컨텍스트 수집 (채널 이력, 최근 120일)
               │     ├─ 같은 교사의 과거 문의 + 실제 발송 답변      → 개인화
               │     ├─ 같은 유형의 최근 답변 완료 사례 (few-shot)  → 톤·형식
               │     └─ 재질문이면 원 문의 스레드                  → 대화 맥락
               ├─ 가이드 로드: Notion(1시간 캐시) → 실패 시 guide/cs_guide.md 스냅샷
               ├─ Claude 호출 (시스템 프롬프트 = 고정 지침 + 가이드 전문, 가이드 블록은 prompt caching)
               ├─ 후처리: [분류]/[응대 초안]/[크로스체크 필요]/[에스컬레이션] 4블록 검사,
               │           초안 본문에 금액·크레딧 숫자가 보이면 ⚠️ 경고 배지
               └─ 스레드 댓글 게시 (초안 본문은 복사하기 쉽게 코드 블록)
```

감지 대상은 두 가지입니다.

| 알림 | 예 | 처리 |
|---|---|---|
| 신규 문의 | `:eyes: 2026-09-07 19:29:05 에 접수된 문의입니다. - 문의 유형 : ...` | 초안 생성 |
| 재질문 | `:speech_balloon: 3일 만에 재질문이 접수되었습니다. <링크|바로가기>` + 본문 | 원 문의 스레드를 찾아 맥락 포함 후 초안 생성 |
| 답변 완료 | `:white_check_mark: [답변완료] ...` | 건너뜀 (컨텍스트 자료로만 사용) |

중복 댓글은 두 겹으로 막습니다. 처리한 메시지 ts 를 `.state/processed.json` 에 기록하고, 게시 전에 스레드에 이미 봇 댓글이 있는지 확인합니다.

## 가이드가 지키는 규칙

노션 문서의 규칙을 프롬프트와 코드 양쪽에서 강제합니다.

- **금액·크레딧·잔여·소진일 등 숫자는 초안에 넣지 않고** `{담당자 확인 후 기입}` 으로 비운 뒤 `[크로스체크 필요]` 에 나열합니다. 본문에 그런 숫자가 보이면 댓글에 경고가 붙습니다.
- 비밀번호·이메일 변경·대표교사 변경·환불·크레딧 재지급은 **"확인 후 처리"** 절차만 안내합니다.
- 가이드 §11 출력 형식(`[분류] / [응대 초안] / [크로스체크 필요] / [에스컬레이션]`)을 그대로 따릅니다.
- 노션 문서를 고치면 다음 갱신 주기(기본 60분)부터 초안에 반영됩니다. 코드 수정이 필요 없습니다.

## 설치

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 값 채우기
```

### 1. Slack 앱 만들기 (5분)

1. https://api.slack.com/apps → **Create New App** → **From a manifest** → 워크스페이스 선택 → `slack_manifest.yaml` 내용 붙여넣기 → Create.
2. **Install to Workspace** 후 *OAuth & Permissions* 의 **Bot User OAuth Token** (`xoxb-…`) 을 `SLACK_BOT_TOKEN` 에.
3. *Basic Information → App-Level Tokens* 에서 `connections:write` 스코프로 토큰 생성 → `xapp-…` 을 `SLACK_APP_TOKEN` 에. (폴링 모드만 쓸 거면 생략 가능)
4. `#clipo-cs` 채널에서 `/invite @클리포 CS 도우미`.

매니페스트에 들어 있는 스코프는 `channels:history`, `channels:read`, `groups:history`, `groups:read`, `chat:write` 이고 이벤트는 `message.channels`, `message.groups` 입니다.

### 2. Notion 통합 (선택, 권장)

1. https://www.notion.so/profile/integrations → 내부 통합 생성 → **Internal Integration Secret** 을 `NOTION_TOKEN` 에.
2. 노션에서 가이드 페이지 우상단 `…` → **Connections** → 방금 만든 통합 추가.
3. `python -m clipo_cs_bot guide` 로 `# 출처: notion` 이 찍히는지 확인.

토큰이 없거나 조회에 실패하면 저장소의 `guide/cs_guide.md` 스냅샷을 씁니다. 노션을 크게 고친 뒤에는 스냅샷도 갱신해 두는 것이 안전합니다.

```bash
python -m clipo_cs_bot guide > guide/cs_guide.md
```

### 3. Claude

`ANTHROPIC_API_KEY` 를 넣습니다. 기본 모델은 `claude-opus-5`, effort 는 `high` 입니다. 비용을 낮추려면 `CLAUDE_EFFORT=medium`, 품질을 더 올리려면 `xhigh`.

## 실행

```bash
# 실시간 감시 (Socket Mode, 권장)
python -m clipo_cs_bot run

# 폴링 (앱 토큰 없이 봇 토큰만으로)
python -m clipo_cs_bot poll

# 특정 문의로 초안만 뽑아 터미널에서 확인 (슬랙에 안 올림) — 시범 운영 전 품질 점검용
python -m clipo_cs_bot draft --ts 1788776946.730899
python -m clipo_cs_bot draft --ts 1788776946.730899 --post   # 확인 후 스레드에 게시

# 봇이 꺼져 있던 동안 쌓인 미응대 문의 일괄 처리
python -m clipo_cs_bot backfill --hours 24
```

메시지 ts 는 슬랙에서 메시지 링크 복사 시 URL 끝의 `p1788776946730899` 를 `1788776946.730899` 로 읽으면 됩니다.

`DRY_RUN=true` 로 두면 어떤 모드에서도 슬랙에 올리지 않고 터미널에만 출력합니다. 시범 운영 첫날은 이 상태로 하루 돌려 보고 켜는 것을 권합니다.

### Docker

```bash
docker build -t clipo-cs-bot .
docker run -d --name clipo-cs-bot --env-file .env -v clipo_state:/app/.state --restart unless-stopped clipo-cs-bot
```

### 테스트

```bash
pip install pytest && python -m pytest -q tests
```

## 운영 메모

- **응답 속도**: 문의 1건당 Claude 호출 1회. 채널 이력 조회(최대 10페이지)를 포함해 보통 30초 안팎입니다. Slack 이벤트는 즉시 ack 하고 초안 생성은 별도 스레드에서 돌리므로 재전송(3초 룰) 문제는 없습니다.
- **비용**: 가이드(약 7천 자)는 prompt caching 으로 캐시되어 두 번째 호출부터 입력 비용이 크게 줄어듭니다. 로그의 `cache_read` 값으로 확인할 수 있습니다.
- **개인정보**: 문의 본문(교사 성명·메일·연락처)이 Claude API 로 전송됩니다. 백오피스 데이터가 이미 슬랙에 공유되는 범위와 같지만, 회사 데이터 처리 방침에 맞는지 한 번 확인해 두세요.
- **피드백 루프**: 초안이 어긋난 케이스를 스레드에 남기면, 다음 문의부터 "스레드 내부 메모" 로 컨텍스트에 포함됩니다. 반복되는 교정은 노션 가이드에 반영하는 것이 가장 효과적입니다.
- **레거시 Claude in Slack 봇과의 관계**: 이 봇은 태그 없이 자동으로 초안을 올립니다. 기존처럼 `@Claude` 를 태그해 다시 다듬는 흐름은 그대로 병행할 수 있습니다.

## 구조

```
clipo_cs_bot/
  config.py     환경 변수 → Settings
  parser.py     백오피스 알림 파싱 (신규/답변완료/재질문), 답변 본문 추출
  context.py    채널 이력에서 개인화·few-shot·원 스레드 수집
  guide.py      Notion → 마크다운 변환, 캐시, 스냅샷 폴백
  drafter.py    Claude 호출, 4블록 파싱, 숫자 규칙 후처리
  slack_app.py  Socket Mode / 폴링, 중복 방지, 댓글 포맷
  __main__.py   CLI (run / poll / draft / backfill / guide)
guide/cs_guide.md   노션 가이드 스냅샷 (폴백)
slack_manifest.yaml Slack 앱 매니페스트
tests/              파서·후처리 단위 테스트
```
