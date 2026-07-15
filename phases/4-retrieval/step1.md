# Step 1: chat-context-assembly

## 읽어야 할 파일

- `/docs/PRD.md` (4단계 항목), `/docs/ADR.md` (ADR-010 포함)
- `lossy_clone/chatbot.py` — 현재 `_SUMMARY_INSTRUCTION`/`_EPISODIC_INSTRUCTION`/`_strip_code_fence`/`_parse_episodes` 같은 모듈 레벨 헬퍼 패턴을 그대로 따라간다
- `lossy_clone/memory/ltm.py`, `lossy_clone/memory/episodic.py` — step 0에서 만든 `search_ltm`/`search_episodic`의 실제 반환 shape을 코드에서 직접 확인

## 작업

`lossy_clone/chatbot.py`에 순수 함수(DB/LLM 호출 없음) `build_memory_context`를 추가한다.

```python
def build_memory_context(ltm_hits: list[dict], episodic_hits: list[dict]) -> Optional[str]:
    """검색된 LTM/Episodic 결과를 하나의 컨텍스트 텍스트로 조립한다.
    ltm_hits, episodic_hits가 둘 다 비어있으면 None을 반환한다."""
    ...
```

## 핵심 규칙 (반드시 지켜야 함)

- `ltm_hits`/`episodic_hits`가 둘 다 빈 리스트면 `None`을 반환한다 (호출부가 "컨텍스트 없음"을 판단할 수 있어야 한다).
- 반환 텍스트에는 다음이 반드시 포함되어야 한다:
  - 이 내용이 "사용자의 과거 학습 이력"이라는 점을 알리는 안내 문장.
  - `ltm_hits`가 있으면 각 항목의 `summary` 텍스트.
  - `episodic_hits`가 있으면 각 항목의 `topic`과 `questions`(최소한 이 둘은 반드시 — `strengths`/`weaknesses`도 포함하면 좋음).
  - "관련 있으면 참고해서 답하라" 류의 지시 — 특히 지금 질문이 과거 `questions`와 비슷하면 언급하고 이어가라는 취지를 포함한다 (별도 반복 질문 감지 알고리즘 없이, 이 문장 하나로 LLM에게 판단을 위임한다 — 다음 step에서 참조할 ADR-011).
- `ltm_hits`만 있고 `episodic_hits`는 없는 경우(또는 반대)에도 있는 쪽만 자연스럽게 포함하고 빈 섹션 헤더를 넣지 마라.
- 정확한 문구는 자유롭게 정한다 (ADR-002 — 디테일은 뭉갠다). 다만 위에 나열한 정보(요약 텍스트, topic, questions, 참고하라는 지시)가 실제로 텍스트 안에 들어있어야 한다 — 테스트가 이 내용의 존재 여부를 문자열 포함 검사로 확인한다.
- 이 함수는 `Chatbot` 인스턴스나 DB 커넥션을 전혀 몰라야 한다 (순수 함수, `list[dict]` 입력 → `Optional[str]` 출력).

## 테스트 (TDD — 먼저 작성하고 통과하는 구현을 만들 것)

`lossy_clone/tests/test_chatbot.py`에 추가 (DB/LLM 없이 plain dict만 넣고 검증):

- `ltm_hits=[]`, `episodic_hits=[]` → `None` 반환.
- `ltm_hits`에 `summary` 텍스트가 있는 dict 하나 → 반환 문자열에 그 `summary` 텍스트가 포함되는지.
- `episodic_hits`에 `topic`/`questions`가 있는 dict 하나 → 반환 문자열에 `topic`과 `questions`의 실제 값이 포함되는지.
- `ltm_hits`만 있고 `episodic_hits=[]`인 경우에도 정상적으로 문자열이 만들어지는지 (반대 경우도).
- 반환 문자열에 "참고해서" 류의 지시성 문구가 포함되는지 (정확한 단어가 아니라, 예를 들어 "과거"/"이전" 같은 이력을 가리키는 단어와 "참고"/"활용" 같은 지시 단어가 함께 있는지 정도로 느슨하게 검증 — 정확한 워딩에 테스트를 결합시키지 마라).

## Acceptance Criteria

```bash
python -m compileall -q .
python -m pytest
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. `build_memory_context`가 DB나 `self`(Chatbot 인스턴스) 없이 순수하게 동작하는지 확인한다.
3. `phases/4-retrieval/index.json`의 `step 1`을 업데이트한다.

## 금지사항

- 이번 step에서 `Chatbot.chat()`을 수정하지 마라 — 실제 연결은 다음 step(`chat-integration`)의 몫이다.
- `search_ltm`/`search_episodic`를 이 함수 안에서 호출하지 마라 — 이 함수는 이미 검색된 결과(`list[dict]`)만 받는다.
- 반복 질문 여부를 판정하는 임계값/스코어링 로직을 새로 만들지 마라 — 검색 결과를 텍스트로 노출하고 판단은 LLM에 맡긴다.
- 기존 테스트를 깨뜨리지 마라.
