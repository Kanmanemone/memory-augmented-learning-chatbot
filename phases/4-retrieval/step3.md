# Step 3: docs-sync-phase4

## 읽어야 할 파일

- `CLAUDE.md`의 "문서 작성 스타일" 섹션
- `/docs/ARCHITECTURE.md`, `lossy_clone/ARCHITECTURE.md` (2/3단계 섹션 문체·형식 참고)
- `/docs/ADR.md` (ADR-009~011 전부)
- `lossy_clone/chatbot.py` — `chat()`의 최종 구현(검색 호출 순서를 코드에서 직접 확인)
- `lossy_clone/README.md`

## 작업

### 1. `docs/ARCHITECTURE.md` 갱신

- "데이터 흐름 (1단계)" 섹션(현재 `chat()` 흐름)에 검색 단계를 반영한다 — `search_ltm`/`search_episodic` 호출과 컨텍스트 주입이 어디에 끼워지는지 실제 호출 순서로 적는다. ("Episodic 단계가 추가되면..." 같은 이제는 낡은 예고 문구가 있다면 정리한다.)
- "디렉토리 구조"에 새 함수(`search_ltm`/`search_episodic`, `build_memory_context`)가 추가된 파일을 반영한다 (새 파일이 생기지 않았다면 주석만 갱신).
- "상태 관리" 섹션에 Chroma/임베딩 없이 키워드 검색만 쓴다는 점을 반영한다 (ADR-010).

### 2. `lossy_clone/README.md` 갱신

- 상태 표 4단계 항목을 ⬜ → ✅로 바꾼다. 이제 로드맵의 모든 단계가 완료되므로, "N단계는 아직 구현 전..." 같은 예정 로드맵 문구를 정리한다(더 이상 미구현 단계가 없다는 점을 반영).
- "코드에서 사용" 섹션에 `chat()`이 이제 과거 LTM/Episodic 맥락을 자동으로 참고한다는 점을 한 줄 덧붙인다.

### 3. `lossy_clone/ARCHITECTURE.md` 갱신

- `## 4단계 — 검색/통합 (예정)`을 실제 구현으로 교체한다 (제목에서 "(예정)" 제거).
- 1~3단계 섹션과 동일한 형식: mermaid `sequenceDiagram`(구체적 예시 포함) + 이유 불릿.
- 다이어그램은 `chat()`의 실제 흐름을 그린다: 사용자 메시지 저장 → `search_ltm`/`search_episodic` 호출 → (매치 있으면) `build_memory_context`로 컨텍스트 조립 → STM 이력 조회 → `generate()`(컨텍스트가 system 메시지로 맨 앞에 옴) → 응답 저장/반환. 이전 단계 다이어그램들처럼 백틱/이스케이프 따옴표를 mermaid 라벨 안에 직접 넣지 마라 — 지난 리뷰에서 문제가 됐던 부분이다(코드펜스나 JSON 예시가 필요하면 불릿 설명 쪽 텍스트로 뺀다).
- 불릿에는 ADR-010(왜 키워드 검색만 쓰는지), ADR-011(왜 system 메시지로 앞에 붙이는지, 왜 반복 질문 감지를 별도 알고리즘 없이 처리하는지)의 핵심 이유를 반영한다.
- 이 phase를 끝으로 `docs/PRD.md`의 로드맵 4단계가 전부 끝난다는 점을 파일 상단이나 4단계 섹션 근처에 한 줄 남긴다.

## 핵심 규칙 (반드시 지켜야 함)

- CLAUDE.md 문서 스타일 가이드를 따른다: 리스트 우선, 정확한 경로 명시, 실제 함수 호출 순서로 작성(짐작 금지).
- 루트 `docs/ARCHITECTURE.md`와 `lossy_clone/ARCHITECTURE.md`를 혼동하지 마라. 둘 다 갱신 대상이지만 내용 수준이 다르다.

## 테스트

코드 변경이 없으므로 새 테스트는 필요 없다. 기존 테스트 스위트가 그대로 통과하는지만 확인한다.

## Acceptance Criteria

```bash
python -m compileall -q .
python -m pytest
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 문서에 적힌 흐름이 `chatbot.py`의 실제 구현과 일치하는지 확인한다.
3. mermaid 다이어그램에 백틱/이스케이프 따옴표가 라벨 안에 직접 들어가지 않았는지 확인한다.
4. `phases/4-retrieval/index.json`의 `step 3`을 업데이트하고, 이 phase가 마지막 step이므로 모든 step이 `completed`면 `phases/index.json`의 `4-retrieval` 항목도 `completed`로 갱신한다.

## 금지사항

- `*.py` 코드를 수정하지 마라 — 이 step은 문서 동기화만 다룬다.
- mermaid 다이어그램 라벨 안에 코드펜스(` ``` `)나 이스케이프된 따옴표(`\"`)를 직접 넣지 마라.
- 기존 테스트를 깨뜨리지 마라.
