# Step 2: docs-sync-phase3

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `CLAUDE.md`의 "문서 작성 스타일" 섹션 — 이번 step에서 문서를 고칠 때 그대로 적용해야 하는 규칙이다
- `/docs/ARCHITECTURE.md` — 2단계 데이터 흐름/상태 관리 섹션이 이미 어떻게 적혀 있는지(문체·수준 참고)
- `/docs/ADR.md` (ADR-005~008 전부)
- `lossy_clone/README.md`, `lossy_clone/ARCHITECTURE.md` — 이번 step에서 **셋 다**(루트 `docs/ARCHITECTURE.md` 포함) 갱신 대상이다. `lossy_clone/ARCHITECTURE.md`의 `## 2단계 — LTM` 섹션(mermaid + 이유 불릿 패턴)을 그대로 참고해서 `## 3단계 — Episodic memory (예정)`를 채운다.
- `lossy_clone/chatbot.py` — step 1에서 확장된 `end_session()`의 실제 구현 (두 번째 `generate()` 호출과 파싱/저장 로직을 코드에서 직접 확인할 것, 짐작으로 쓰지 않는다)
- `lossy_clone/memory/episodic.py` — step 0에서 만든 스키마/함수

## 작업

### 1. `docs/ARCHITECTURE.md` 갱신

- "데이터 흐름 (2단계)" 다음에 "데이터 흐름 (3단계)" 섹션을 추가한다. `end_session()`의 실제 호출 순서 그대로 적는다 — LTM 요약 저장까지 끝난 뒤 별도의 `generate()` 호출로 topic/strengths/weaknesses/questions를 추출하고, 유효한 항목만 `memory.episodic.save_episodes(...)`로 저장하는 흐름을 구체적으로 적는다.
- "디렉토리 구조" 트리에 `memory/episodic.py`를 반영하고, "episodic.py 등은 3단계에서 추가" 같은 예정 표시 주석을 지운다.
- "상태 관리" 섹션의 "프로세스 밖 상태 중 Episodic은 3단계에서 도입되기 전까지는 존재하지 않는다" 문장을 갱신한다 — Episodic도 이제 같은 SQLite 파일의 `episodic` 테이블에 존재한다는 내용으로 바꾼다 (이제 이 섹션에 "아직 없는 프로세스 밖 상태"가 하나도 안 남으므로, 문장을 통째로 재구성해도 된다).

### 2. `lossy_clone/README.md` 갱신

- "상태" 표에서 3단계 항목을 ⬜ → ✅로 바꾼다.
- 로드맵 문구("3~4단계는 아직 구현 전...")를 "4단계는 아직 구현 전..."으로 축소한다.
- "코드에서 사용" 섹션의 `end_session()` 예시 옆에, 이 호출로 세션에서 다룬 주제별 학습 이력(topic/strengths/weaknesses/questions)도 함께 `episodic` 테이블에 저장된다는 점을 한 줄 주석 또는 문장으로 덧붙인다. (episodic 저장은 side effect라 새 API 호출 예시를 추가하지 않는다 — 반환값은 여전히 요약 텍스트뿐이다.)

### 3. `lossy_clone/ARCHITECTURE.md` 갱신

- `## 3단계 — Episodic memory (예정)` 섹션을 실제 구현 내용으로 교체한다. 제목에서 "(예정)"을 뗀다.
- `## 2단계 — LTM` 섹션과 동일한 형식으로 작성한다: mermaid `sequenceDiagram`(구체적인 예시 topic/strengths/weaknesses/questions 값 포함) + 그 아래 "왜 이렇게 했는지"를 설명하는 불릿 목록.
- 다이어그램은 `end_session()` 하나 안에서 LTM 요약 호출과 Episodic 추출 호출이 순서대로 두 번 일어나는 것을 표현해야 한다 — 2단계 다이어그램의 마지막(`INSERT INTO ltm`) 다음에 이어지는 두 번째 `Bot->>Gemini: generate(...)` 호출부터 시작하는 식으로 그려라 (전체를 새로 그리지 말고 2단계 다이어그램에 자연스럽게 이어지도록).
- 불릿 설명에는 ADR-007(왜 append-only인지, 왜 taxonomy/embedding이 없는지), ADR-008(왜 별도 LLM 호출인지, 왜 실패가 격리되는지, 왜 순서가 고정인지)의 핵심 이유를 반영한다.
- `## 4단계 — 검색/통합 (예정)` 섹션은 그대로 둔다 (아직 미구현이므로 "(예정)" 유지 — CLAUDE.md: 미구현 로드맵은 바뀔 수 있음을 명시).

## 핵심 규칙 (반드시 지켜야 함)

- CLAUDE.md 문서 스타일 가이드를 따른다: 리스트 우선(문단 지양), 파일 경로는 정확히 명시(모호하게 "README"라고만 쓰지 않는다), 다이어그램/흐름은 실제 코드의 함수 호출로 적는다.
- 루트 `docs/ARCHITECTURE.md`와 `lossy_clone/ARCHITECTURE.md`를 혼동하지 마라. 둘 다 갱신 대상이지만 내용 수준이 다르다 — 루트는 기존 문체(텍스트 화살표) 그대로 간단히, `lossy_clone/`은 2단계처럼 mermaid로 상세히.
- `lossy_clone/ARCHITECTURE.md`의 4단계 섹션 내용을 앞당겨 채우지 마라 — "(예정)" 상태를 유지한다 (ADR-003).

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
   - `docs/ARCHITECTURE.md`의 데이터 흐름 설명과 `lossy_clone/ARCHITECTURE.md`의 mermaid 다이어그램이 둘 다 `end_session()`의 실제 구현(LTM 요약 → Episodic 추출 순서)과 일치하는가?
   - `lossy_clone/ARCHITECTURE.md`의 3단계 섹션이 2단계 섹션과 동일한 형식(mermaid + 이유 불릿)을 따르는가?
   - `docs/ARCHITECTURE.md`의 "상태 관리" 섹션이 Episodic 존재를 반영하도록 갱신되었는가?
   - `CLAUDE.md` 문서 작성 스타일 규칙을 지켰는가?
3. 결과에 따라 `phases/3-episodic/index.json`의 `step 2` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 변경한 파일을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단
4. 이 step이 phase `3-episodic`의 마지막 step이므로, 모든 step이 `completed`면 `phases/index.json`에서 `3-episodic` 항목의 `status`도 `completed`로 갱신한다.

## 금지사항

- 루트 `docs/ARCHITECTURE.md`와 `lossy_clone/ARCHITECTURE.md`를 혼동하지 마라.
- `lossy_clone/ARCHITECTURE.md`의 4단계 섹션 내용을 앞당겨 채우지 마라 — "(예정)" 상태를 유지한다 (ADR-003).
- `*.py` 코드를 수정하지 마라 — 이 step은 문서 동기화만 다룬다.
- 기존 테스트를 깨뜨리지 마라.
