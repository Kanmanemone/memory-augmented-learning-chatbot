# 프로젝트: memory-augmented-learning-chatbot

## 기술 스택
- Python (표준 라이브러리 + `sqlite3`)
- Gemini API (`google-genai`) — 대화 응답, topic tagging, 세션 요약
- ChromaDB — 벡터 검색 저장소
- 프로젝트는 GDG 워크숍 데모에서 출발했고, 현재 데모 전용 코드를 걷어내고 실사용 가능한 형태로 정리하는 중이다. 자세한 배경은 `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/ADR.md` 참고.

## 아키텍처 규칙
- CRITICAL: STM/LTM/Episodic 저장·조회 로직은 반드시 `memory/*.py` 또는 `episodic_schema.py`를 통해서만 접근한다. `chatbot.py`나 다른 곳에서 SQL을 직접 새로 짜지 않는다.
- CRITICAL: 데모 전용 코드(특정 문자열을 감지해 정해진 답변으로 강제 치환하거나, 특정 시나리오만 통과시키기 위한 하드코딩)를 새로 추가하지 않는다 — 지금 진행 중인 정리 작업의 정반대 방향이다.
- 새 fallback 로직(임베딩, topic tagging 등)을 추가할 때는 기존에 이미 있는 fallback과 방식이 겹치거나 어긋나지 않는지 먼저 확인한다 (`docs/ADR.md`의 "결정 대기 중" 항목 참고 — 임베딩 스킴이 이미 3곳에서 서로 호환되지 않는 상태다).
- Harness(`scripts/execute.py`, `.claude/commands/harness.md`)는 챗봇 로직과 무관한 별도 메타 도구다. 챗봇 기능을 만들 때 이 파일들을 함께 고칠 필요는 없다.

## 개발 프로세스
- 현재 `tests/`가 비어있어 테스트 안전망이 없는 상태다. 테스트 인프라(pytest 등, `docs/ADR.md` 결정 대기 중 참고)가 정해지면 이후 새 기능은 TDD(테스트 먼저, 통과하는 구현)로 개발한다. 그 전까지는 변경 후 반드시 직접 실행해서 눈으로 확인한다.
- 커밋 컨벤션:
  - 제목: `{type}: {구체적 요약}` — 마침표 없음. `type`은 `feat`/`fix`/`docs`/`refactor`/`chore`/`test` 중 하나. 관련 함수·메서드가 있으면 이름을 그대로 언급한다 (예: `AnalystAgent.run()`).
  - 제목 다음에 빈 줄을 하나 두고, 본문은 불릿 없이 하나의 서술형 문단으로 쓴다.
  - 본문 문단 순서: ① 실제 코드/데이터가 어떻게 동작했는지 구체적으로 서술한다 (변수명, 키 이름, 몇 번째 줄인지 등을 그대로 인용) → ② 무엇을 기대했는데 무엇이 어긋났는지 → ③ 그 결과 어떤 증상이 나타났는지 → ④ 마지막 문장에서 "그래서 ~을 ~하게 고침" 형태로 실제 수정 내용을 요약한다.
  - 문체는 "~음", "~였음", "~함" 같은 개조식 종결어미를 쓴다 (평서문/구어체가 아님).
  - 예시:
    ```
    fix: _select_files_with_llm()이 validated_files가 빈 경우에도 규칙 기반 선택으로 폴백하게 함

    _select_files_with_llm()은 except Exception에서만 select_candidate_files()로
    폴백해서, API 에러나 JSON 파싱 실패 때만 폴백이 걸렸음. 그런데 LLM이 정상
    응답을 반환해도 selected_files가 빈 리스트거나 tree_paths에 없는 경로만
    골라서 validated_files가 []가 되는 경우는 예외가 아니라서 그대로 빈 리스트가
    반환됐음. validated_files가 비어있으면 select_candidate_files()로 폴백하는
    분기를 try 블록 안에 추가함.
    ```

## 명령어
```bash
python chatbot.py                       # CLI로 챗봇 실행
python -m compileall -q .               # 최소 구문 검증 (하네스 Stop 훅과 동일)
python3 scripts/execute.py {task-name}  # 하네스 실행
```
