# PRD: memory-augmented-learning-chatbot

## 목표
개인 학습자의 대화를 기억하는 챗봇. 방금 나눈 대화(STM)뿐 아니라 지난 세션에서 뭘 배우고 뭘 어려워했는지(LTM), 특정 주제에 대해 시간이 지나며 실력이 어떻게 변했는지(Episodic)까지 기억해서, 같은 질문을 다시 해도 매번 처음 설명하듯 답하지 않는 학습 도우미를 만든다.

## 사용자
학습 기록을 스스로 관리해줄 개인용 AI 튜터가 필요한 1인 사용자. (지금은 단일 사용자 전용 — 여러 사용자를 구분해서 다루는 기능은 없음. `SessionManager`는 프로세스당 활성 세션을 하나만 허용하며, 이미 활성 세션이 있는 상태에서 `start_session()`을 다시 호출하면 `RuntimeError`)

## 핵심 기능

### 1. STM (단기 기억)
- 현재 세션의 대화 원문을 SQLite `stm_messages` 테이블에 메시지 단위로 저장하고, 매 응답마다 최근 **20턴**(`DEFAULT_STM_WINDOW`, `chatbot.py:173`)을 LLM 컨텍스트에 주입한다. 20턴을 넘는 대화는 저장은 계속되지만 컨텍스트 주입 시 잘려나간다 — 별도의 토큰 예산 기반 truncation은 없다.
- 메시지는 저장 시점에 즉시 커밋되므로, 대화 도중 프로세스가 죽어도(Ctrl+C, 크래시) 이미 나눈 턴은 유실되지 않는다. 단, 세션이 명시적으로 종료되지 않으면 LTM/Episodic 승격은 일어나지 않고, 다음 실행에서 이어받지도 않는다 — 승격은 세션 종료 이벤트에서만 트리거된다.
- 세션이 정상 종료(consolidation 완료)되면 해당 세션의 STM 원문은 삭제된다(경량화). Consolidation이 실패하거나 세션이 비정상 종료되면 STM 원문은 그대로 남는다.

### 2. LTM (장기 기억)
- 세션이 끝나면 그 세션에서 배운 내용, 강점, 약점, 헷갈려한 부분을 Gemini(`gemini-2.5-pro`)로 요약해 `ltm` 테이블에 저장하고, 다음 세션에서 관련 있을 때 하이브리드 검색(키워드 70% + 시맨틱 30%, 아래 "검색 방식" 참고)으로 다시 불러온다.
- **요약이 항상 만들어짐을 보장**: Gemini 호출이 실패해도 무조건 예외를 던지지 않고 키워드 기반 결정론적 요약으로 대체한다(`memory/consolidation.py`의 `default_analyzer`) — 단, 이 fallback은 JSON 파싱 실패 등 일부 경로에서는 예외를 잡지 않아 세션 통째로 요약 실패할 수 있다(아래 "에러/엣지 케이스" 참고).
- 빈 세션(메시지가 0개인 채로 종료)은 LTM을 만들지 않고 `"no_messages"` 상태로 기록될 뿐이다.

### 3. Episodic (주제별 학습 이력)
- LTM 요약을 다시 Gemini(`gemini-2.5-pro`)로 주제별(`category:topic-slug`, 예: `programming:python-decorators`)로 쪼개 변환하고, 같은 주제에 대한 강점/약점/질문 이력을 누적한다.
- 같은 주제가 다시 등장하면 정확히 같은 토픽명이거나 유사도(제목/태그/질문의 문자열·토큰 유사도)가 **0.86 이상**이면 기존 레코드에 병합하고 `occurrence_count`를 1 증가시킨다 — 완전히 새 레코드를 만들지 않는다.
- 다음 세션에서 같은 주제를 다시 물어보면 이전 강점/약점/질문 이력을 컨텍스트에 반영해 답한다.

### 검색 방식 (LTM/Episodic 공통)
- 구조화 필드(키워드 토큰 매칭) 70% + Chroma 벡터 유사도(cosine) 30%로 가중합한 hybrid score를 쓴다(`memory/retrieval.py`의 `HybridScoreConfig` 기본값: `semantic_weight=0.3`, `keyword_weight=0.7`).
- 검색어가 빈 문자열이거나 키워드 토큰이 하나도 안 남으면(불용어 제거 후) `RetrievalInputValidationError`를 던진다 — 빈 질의로 검색을 시도하지 않는다.
- 결과가 없으면 예외 없이 빈 리스트를 반환한다.

## 에러/엣지 케이스 (제품 관점)

| 상황 | 현재 동작 |
|---|---|
| Gemini API 키가 없음 | 실행 시점에는 에러가 나지 않는다. 첫 Gemini 호출(응답 생성, topic tagging, 요약)이 일어나야 실패가 드러난다 — 사전 검증 없음 |
| `google-genai` 패키지 미설치 | Gemini 클라이언트 생성 시점에 `ImportError` |
| Gemini 응답 생성 자체가 실패(네트워크/rate limit/빈 응답) | `chat()` 호출부에 try/except가 없어 `RuntimeError`가 그대로 사용자(또는 CLI)에게 전파된다 — 이번 턴은 실패하지만 이전 턴들은 STM에 이미 저장돼 있어 유실되지 않는다 |
| Topic tagging 실패 | 예외를 삼키고 빈 태그 리스트로 처리 — 턴 자체는 정상 진행 |
| 세션 종료를 두 번 호출 | 안전하게 무시됨(idempotent) — `Chatbot._session_consolidated` 플래그와 `SessionManager`의 활성 세션 가드 이중 보호 |
| 데모/로컬 SQLite DB 파일 또는 Chroma 저장소가 없거나 손상됨 | `DemoDatabaseError`(경로 포함한 설명 메시지)를 던지고 시작 자체를 중단 |
| 빈 사용자 입력(빈 문자열) | CLI 레벨에서는 건너뛰지만, `Chatbot.chat()` 자체에는 빈 입력 가드가 없다 |
| STM 저장은 됐는데 세션 종료 전 크래시 | 다음 실행에서 자동 복구/재개되지 않는다. 해당 세션은 사실상 고아 상태로 남는다(수동 정리 필요) |
| LTM은 저장됐는데 Episodic 승격 도중 실패 | `memory_state`에 완료 기록이 남지 않지만, LTM 존재 자체로 "이미 처리됨"으로 간주돼 재시도해도 다시 승격 시도가 안 된다 — Episodic 승격 누락이 영구화될 수 있음(알려진 정합성 갭, `docs/ADR.md` 참고) |

## CLI 종료 명령어
`exit`, `quit`, `/end`, `:q`, `bye`, `goodbye`, `종료`, `끝` (대소문자 무관, 첫 토큰 매칭도 인식 — 예: "exit now"도 종료로 처리). `Ctrl+D`/`Ctrl+C`도 세션을 정상 종료 처리한다. "리셋" 명령어는 없다.

세션은 위 명시적 종료 명령으로만 끝난다 — `memory/session_manager.py`에는 `max_turns`/`inactivity_timeout_seconds` 기반 자동 종료 기능(`SessionEndCriteria`)이 이미 구현돼 있지만, `chatbot.py`가 `SessionManager` 생성 시 이 값을 넘기지 않아(둘 다 `None`) 실제로는 비활성 상태다. 켤지 여부는 미결정.

## 비기능 요구사항
- 완전 로컬 저장(SQLite + Chroma 파일 기반) — 원격 DB 없음.
- Gemini API 네트워크 연결이 필요하다(응답 생성 1회 + topic tagging 1회 + 세션 종료 시 요약/Episodic 변환 최대 2회, 턴당 최대 2회·세션 종료 시 추가 2회 호출).
- 무인 배치/스케줄러는 없다 — 모든 승격(LTM/Episodic)은 세션 종료 이벤트에서 동기적으로 일어난다. `memory/demo_conversion.py`에 유휴시간/일 단위 배치 승격 함수가 존재하지만 살아있는 `Chatbot` 흐름과는 분리돼 있다(아래 "MVP 제외 사항" 참고).

## MVP 제외 사항
- 멀티유저 지원 (계정 구분, 사용자별 데이터 분리)
- 웹/GUI 인터페이스 (지금은 CLI 또는 Python에서 직접 import해서 쓰는 라이브러리 형태)
- 실제 임베딩 API 연동 (지금은 결정적(deterministic) fallback 벡터를 쓰고 있고, 실 임베딩 도입은 아직 미결정 — [[project-memory-augmented-chatbot-refactor]] 참고. 서로 다른 3곳에서 차원조차 다른 fallback이 쓰이고 있어(`memory/consolidation.py` 3차원, `memory/demo_conversion.py` 3차원이지만 다른 축, `chatbot.py` 384차원) 검색 결과가 애초에 신뢰할 수 없는 상태다 — 상세는 `docs/ARCHITECTURE.md`, `docs/ADR.md` 참고)
- 유휴시간/일 단위 배치 승격 스케줄러 (`promote_stm_to_ltm_if_idle` 등 — 제품에 남길지 여부 미결정)
- 세션 자동 종료(최대 턴 수/비활성 타임아웃) — 기능은 구현돼 있으나 미연결
- GDG 워크숍 데모 재현을 위한 하드코딩 로직 (진행 중인 정리 대상 — 특정 문자열을 감지해 정해진 답변으로 강제 치환/삽입하는 5개 가드 함수와, 검색 정확도를 좌우하는 고정 키워드→축 매핑 fake 임베딩이 `chatbot.py`에 아직 남아있다. 상세는 `docs/ADR.md` "발견된 규칙 위반" 참고)

## 인터페이스
- 지금은 GUI/웹 화면이 없는 프로젝트라 "디자인" 대신 인터페이스 형태를 적는다.
- CLI: `python chatbot.py` 실행 시 터미널에서 대화하는 REPL.
- 라이브러리: `from chatbot import Chatbot`으로 다른 Python 코드에서 직접 사용. `Chatbot`은 컨텍스트 매니저(`with Chatbot(...) as bot:`)를 지원하며, `__exit__`에서 `end_session()` → `close()` 순으로 자동 정리된다.
