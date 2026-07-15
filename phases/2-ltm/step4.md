# Step 4: docs-sync-phase2

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `CLAUDE.md`의 "문서 작성 스타일" 섹션 — 이번 step에서 `ARCHITECTURE.md`/`README.md`를 고칠 때 그대로 적용해야 하는 규칙이다
- `/docs/ARCHITECTURE.md` — 1단계 데이터 흐름이 이미 어떻게 적혀 있는지(문체·수준 참고)
- `/docs/ADR.md` (ADR-005, ADR-006 포함)
- `lossy_clone/README.md`, `lossy_clone/ARCHITECTURE.md` — 이번 step에서 **둘 다** 갱신 대상이다. 루트 `docs/ARCHITECTURE.md`와 헷갈리지 마라 (ADR-001 참고: 루트 `docs/`는 하네스가 읽는 가드레일 문서, `lossy_clone/README.md`·`lossy_clone/ARCHITECTURE.md`는 이 폴더만 잘라내도 남는 실행 문서 — 세 파일 모두 별개로 존재하며 전부 이번 step에서 손댄다). `lossy_clone/ARCHITECTURE.md`는 이미 `## 2단계 — LTM (예정)` 자리가 비워져 있으니, `## 1단계 — Chatbot + STM`의 mermaid `sequenceDiagram` + 불릿 설명 패턴을 그대로 참고하라.
- `lossy_clone/chatbot.py` — step 2에서 추가된 `end_session()`의 실제 구현 (호출 순서를 코드에서 직접 확인할 것, 짐작으로 쓰지 않는다)
- `lossy_clone/__main__.py` — step 3에서 반영된 CLI 출력 문구

## 작업

### 1. `docs/ARCHITECTURE.md` 갱신

- "데이터 흐름" 섹션에 2단계 흐름을 추가한다. `end_session()`을 코드에서 직접 확인한 뒤, 그 함수가 실제로 호출하는 순서 그대로 적어라 ("요약한다" 같은 추상적 표현이 아니라 `get_recent_messages(limit=None)` → `LLMClient.generate(...)` → `save_summary(...)`처럼 실제 호출 순서로).
- "디렉토리 구조" 트리에 `memory/ltm.py`를 반영한다.
- "상태 관리" 섹션의 "프로세스 밖 상태(LTM, Episodic)는 각각의 단계에서 도입되기 전까지는 존재하지 않는다" 문구를 갱신한다. LTM은 이제 존재하므로(같은 SQLite 파일의 `ltm` 테이블), Episodic만 아직 없다는 내용으로 바꿔라.

### 2. `lossy_clone/README.md` 갱신

- "상태" 표에서 2단계 항목을 ⬜ → ✅로 바꾼다.
- "코드에서 사용" 섹션에 `end_session()` 사용 예시를 추가한다 (`bot.chat(...)` 몇 번 호출 후 `bot.end_session()`을 부르는 흐름).
- "빠른 시작" 섹션에 CLI 종료 시 세션 요약이 LTM에 저장된다는 점을 한 줄로 안내한다.

### 3. `lossy_clone/ARCHITECTURE.md` 갱신

- `## 2단계 — LTM (예정)` 섹션을 실제 구현 내용으로 교체한다. 제목에서 "(예정)"을 뗀다.
- `## 1단계 — Chatbot + STM` 섹션과 동일한 형식으로 작성한다: mermaid `sequenceDiagram`(구체적인 예시 메시지·값 포함) + 그 아래 "왜 이렇게 했는지"를 설명하는 불릿 목록.
- 다이어그램에는 실제 호출 순서(`get_recent_messages(limit=None)` → `LLMClient.generate(...)` → `save_summary(...)`)를 담는다. `chatbot.py`에서 `end_session()`을 직접 읽고 확인한 뒤 작성하라 — 짐작으로 쓰지 않는다.
- 불릿 설명에는 ADR-005(왜 summary만 담는지), ADR-006(왜 명시적 `end_session()` 호출로만 트리거하는지, 중복 호출 시 무슨 일이 일어나는지)의 핵심 이유를 반영한다.
- `## 3단계 — Episodic memory (예정)`, `## 4단계 — 검색/통합 (예정)` 섹션은 그대로 둔다 (아직 미구현이므로 "(예정)" 유지 — CLAUDE.md: 미구현 로드맵은 바뀔 수 있음을 명시).

## 핵심 규칙 (반드시 지켜야 함)

- CLAUDE.md 문서 스타일 가이드를 따른다: 리스트 우선(문단 지양), 파일 경로는 `lossy_clone/README.md`처럼 정확히 명시(모호하게 "README"라고만 쓰지 않는다), 다이어그램/흐름은 실제 코드의 함수 호출로 적는다.
- `lossy_clone/README.md`의 "2~4단계는 아직 구현 전이라 범위/순서가 바뀔 수 있는 예정 로드맵" 문구에서 이제 3~4단계에 대한 부분만 남기고 2단계는 완료 사실로 바꾼다.
- 이번 step은 문서만 다룬다 — `*.py` 코드를 수정하지 마라.

## 테스트

코드 변경이 없으므로 새 테스트는 필요 없다. 기존 테스트 스위트가 그대로 통과하는지만 확인한다.

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과 (기존 스위트 회귀 없음 확인용)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `docs/ARCHITECTURE.md`의 데이터 흐름 설명과 `lossy_clone/ARCHITECTURE.md`의 mermaid 다이어그램이 둘 다 `end_session()`의 실제 구현과 일치하는가?
   - `CLAUDE.md` 문서 작성 스타일 규칙(리스트 우선, 구체적 경로/호출, 문단 축소보다 정확성 우선)을 지켰는가?
   - `lossy_clone/ARCHITECTURE.md`의 2단계 섹션이 1단계 섹션과 동일한 형식(mermaid + 이유 불릿)을 따르는가?
   - `docs/ARCHITECTURE.md`의 "상태 관리" 섹션이 LTM 존재를 반영하도록 갱신되었는가?
   - 루트 `docs/ARCHITECTURE.md`와 `lossy_clone/ARCHITECTURE.md`를 혼동해 엉뚱한 파일에 내용을 넣지 않았는가 (둘 다 갱신하되 내용 수준이 다르다: 루트는 텍스트 화살표 흐름, `lossy_clone/`은 mermaid)?
3. 결과에 따라 `phases/2-ltm/index.json`의 `step 4` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 변경한 파일을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단
4. 이 step이 phase `2-ltm`의 마지막 step이므로, 모든 step이 `completed`면 `phases/index.json`에서 `2-ltm` 항목의 `status`도 `completed`로 갱신한다 (execute.py가 자동 처리하지 않는 경우 수동으로 확인).

## 금지사항

- 루트 `docs/ARCHITECTURE.md`와 `lossy_clone/ARCHITECTURE.md`를 혼동하지 마라. 이번 step은 두 파일 모두 갱신 대상이지만 내용 수준이 다르다 — 루트는 기존 문체(텍스트 화살표) 그대로 간단히, `lossy_clone/`은 1단계처럼 mermaid로 상세히.
- `lossy_clone/ARCHITECTURE.md`의 3/4단계 섹션 내용을 앞당겨 채우지 마라 — "(예정)" 상태를 유지한다 (ADR-003).
- `*.py` 코드를 수정하지 마라 — 이 step은 문서 동기화만 다룬다.
- 기존 테스트를 깨뜨리지 마라.
