# Architecture Decision Records

## 철학
데모보다 실제 동작 우선. 큰 재작성보다 검증 가능한 작은 무위험 변경을 하나씩 쌓아간다. 판단이 애매한 결정은 미루되, "미룬다"는 사실 자체를 기록해서 나중에 놓치지 않는다.

---

### ADR-001: 3계층 메모리 구조 (STM/LTM/Episodic)

**결정**: 대화를 세 계층으로 나눠 기억한다 — STM(세션 원문), LTM(세션 단위 요약), Episodic(주제별 누적 이력).

**이유**: 매 응답에 전체 대화 기록을 다 넣을 수 없고, "방금 한 말"과 "예전에 배운 것"과 "이 주제를 다루는 실력의 변화"는 서로 다른 시간 축을 가진 정보라 계층을 나눠야 검색과 요약이 각각 단순해진다.

**트레이드오프**: 계층이 늘어난 만큼 승격(STM→LTM→Episodic) 로직과 저장소가 늘어나고, 지금처럼 그 승격 로직이 여러 버전으로 중복될 위험도 커진다.

### ADR-002: SQLite + Chroma 이중 저장

**결정**: 구조화된 필드(요약, 태그, 이력 리스트)는 SQLite에, 검색용 벡터는 Chroma에 저장한다.

**이유**: 정확한 필드 조회와 근사 의미 검색은 요구사항이 달라 하나의 저장소로 둘 다 잘하기 어렵다.

**트레이드오프**: 두 저장소 간 정합성(같은 레코드의 SQLite row와 Chroma 벡터가 항상 짝을 이뤄야 함)을 직접 관리해야 하고, 임베딩 스킴이 흔들리면(현재처럼) 이 정합성이 조용히 깨질 수 있다.

### ADR-003: 하네스 Stop 훅/AC를 `python -m compileall`로 임시 대체

**결정**: 원래 npm 기반이던 Harness의 Stop 훅과 AC 예시를 Python 프로젝트에 맞게 최소한의 구문 검증(`python -m compileall -q .`)으로 교체했다.

**이유**: 기존 npm 커맨드는 이 저장소에서 무조건 실패했다. 실제 lint/test 툴체인을 아직 정하지 않은 상태라, 결정을 미리 하기보다 "당장 깨지지 않는 최소 장치"만 먼저 넣었다.

**트레이드오프**: 구문 오류만 잡고 실제 동작 회귀는 못 잡는다. pytest 등 테스트 도구가 정해지면 이 자리를 교체해야 한다.

### ADR-004: 하네스 자체 테스트(`scripts/test_execute.py`)는 pytest에 고정

**결정**: `requirements.txt`에 `pytest==9.1.1`을 추가했다. `scripts/test_execute.py`(execute.py 리팩터링 안전망)가 이미 `import pytest`를 전제로 작성돼 있었는데, pytest가 어디에도 선언돼 있지 않아 실제로는 실행 불가능한 상태였다(`ModuleNotFoundError`).

**이유**: 하네스(`scripts/execute.py`)는 CLAUDE.md에 명시된 대로 챗봇 로직과 무관한 별도 메타 도구다. 이 도구의 자체 회귀 테스트를 돌릴 수 있어야 하네스 코드를 안전하게 고칠 수 있는데, 그 최소 전제조차 깨져 있었다.

**범위**: 이 결정은 하네스 자체 테스트에만 적용된다. 챗봇 본체(`tests/`)의 lint/test 툴체인 선택은 여전히 아래 "결정 대기 중"의 별도 사안이다.

### ADR-005: LTM/Episodic 검색은 키워드 70% + 시맨틱 30% 가중합 hybrid score

**결정**: `memory/retrieval.py`의 `DEFAULT_HYBRID_SCORE_CONFIG`가 `semantic_weight=0.3`, `keyword_weight=0.7`로 고정돼 있다 — 구조화 키워드 매칭이 벡터 유사도보다 약 2.3배 크게 반영된다. `score = (semantic * 0.3 + keyword * 0.7) / weight_sum`.

**이유**: 코드에 남겨진 근거 문서가 없어 역추적한 것이다 — 현재 임베딩이 전부 fallback(3차원 또는 384차원 deterministic 벡터, 실 임베딩 모델이 아님)이라 시맨틱 점수의 신뢰도가 낮으므로, 상대적으로 신뢰 가능한 키워드 매칭 쪽에 가중치를 더 준 것으로 추정된다.

**트레이드오프**: 실 임베딩 API로 전환하면(위 "임베딩 전략" 참고) 이 가중치 비율이 더 이상 적절하지 않을 수 있다 — 임베딩 전략이 바뀌면 이 가중치도 재검토 대상이다. 현재는 "왜 0.3/0.7인지"를 설명하는 근거가 코드/문서 어디에도 없었다는 점 자체가 문제였다(이번에 문서화로 해소).

---

## 결정 대기 중

아래는 검토 중 발견했지만 아직 결론을 내리지 않은 사안들이다. 임의로 정하지 말고, 실제로 손댈 시점에 다시 논의한다.

- **유휴시간(3시간)/매일 03시 배치 승격을 제품에 남길지**: `memory/demo_conversion.py`의 `promote_stm_to_ltm_if_idle`, `promote_ltm_to_episodic_daily`는 현재 실제 챗봇 흐름(`consolidate_session`)과 별개로 존재하고 스케줄러도 없다. 유지하려면 실제 스케줄러 연결이 필요하고, 안 쓸 거면 통째로 삭제 대상.
- **임베딩 전략**: 지금은 서로 다른 3곳에 호환되지 않는 deterministic fallback이 흩어져 있다 — `memory/consolidation.py::_default_embedding`(3차원, `recursion/sql/embedding` 3개 키워드그룹 카운트), `memory/demo_conversion.py::_deterministic_learning_embedding`(3차원이지만 `loops/functions/modules` 축이라 consolidation.py와 값 자체가 호환 안 됨), `chatbot.py::build_demo_query_embedding`(384차원, 고정 키워드→축 one-hot). 특히 **저장은 consolidation.py의 3차원 벡터로, 검색 질의는 chatbot.py의 384차원 벡터로 이뤄지는 경로가 실제로 존재해**(`episodic_schema.py:831-835`의 `search_episodic_by_embedding` 등) Chroma 컬렉션 내 차원이 아예 안 맞는 쿼리가 발생할 수 있다. 실제 Gemini 임베딩 API를 붙일지, 일단 하나의 fallback으로 통일만 할지 결정 필요.
- **Python lint/test 툴체인 (챗봇 본체)**: `tests/`가 비어있는 채로 pytest만 쓸지, ruff 등 lint를 더할지 아직 미정. 하네스 Stop 훅(ADR-003)이 이 결정을 기다리고 있다. (하네스 자체 테스트의 pytest 채택은 ADR-004로 별도 결정됨.)
- **하네스가 아직 실전 투입된 적 없음**: `phases/` 디렉터리 자체가 존재하지 않는다 — `scripts/execute.py`, `.claude/commands/harness.md`, `scripts/test_execute.py`는 갖춰졌지만 실제 task/step으로 end-to-end 실행된 적이 없다. `python3 scripts/execute.py {task-name}` 커맨드(CLAUDE.md)는 `phases/{task-name}/index.json`이 있어야 동작하므로, 첫 실사용 시 `phases/` 스캐폴딩부터 만들어야 한다.
- **Episodic 승격의 비원자적 실패 처리**: `memory/consolidation.py`의 `consolidate()`는 LTM 저장(`ltm.save_ltm_summary`) 성공 후 Episodic upsert 도중 예외가 나면, `memory_state`에 완료 기록을 남기지 못한 채 함수가 종료된다. 그런데 재실행 시 중복 방지 체크(`_has_ltm_for_session`)는 LTM 존재 여부만 보기 때문에 "이미 처리됨"으로 오판해 재시도조차 안 한다 — Episodic 승격 누락이 감지·복구 수단 없이 영구화될 수 있다. 트랜잭션으로 묶을지, `memory_state`에 단계별 상태를 더 세분화해 재시도 가능하게 할지 결정 필요.
- **`memory/__init__.py`의 공개 API 불일치**: 패키지 최상위(`from memory import ...`)로 export되는 함수가 `memory/demo_conversion.py`([정리 대상 일부])의 것뿐이다(`load_demo_stm_json`, `promote_ltm_to_episodic_daily`, `promote_stm_to_ltm_if_idle`, `run_demo_memory_conversion`). 실제 사용 경로인 `stm`/`ltm`/`consolidation`/`tagger`/`session_manager`는 하나도 최상위로 노출되지 않는다. `demo_conversion.py`를 정리할 때 `__init__.py`를 그대로 두면 패키지 공개 API가 통째로 사라지므로, 이 참에 무엇을 공개 API로 삼을지 결정 필요.

---

## 발견된 규칙 위반 (CLAUDE.md CRITICAL)

코드 전수 조사 중 CLAUDE.md의 CRITICAL 규칙을 실제로 위반하고 있는 지점을 발견해 기록한다. 우선순위를 정해 별도로 고쳐야 한다.

### 위반 1: `chatbot.py`가 `memory/*.py`를 거치지 않고 직접 SQL을 짬
CLAUDE.md: "STM/LTM/Episodic 저장·조회 로직은 반드시 `memory/*.py` 또는 `episodic_schema.py`를 통해서만 접근한다." — `chatbot.py`의 `DDL_LEARNER_PERSONA`(`chatbot.py:270-275`), `init_demo_learner_persona`(`chatbot.py:278-289`, `conn.executescript` + `conn.execute` INSERT...ON CONFLICT + `conn.commit()`), `load_demo_learner_persona`(`chatbot.py:292-304`, `conn.execute` SELECT)가 `learner_persona` 테이블에 대해 이 규칙을 어기고 직접 SQL을 실행한다. 이 테이블은 `memory/*.py`/`episodic_schema.py` 어디에도 정의돼 있지 않다.

### 위반 2: 데모 전용 하드코딩(정해진 문자열/시나리오만 통과시키는 강제 치환)
CLAUDE.md: "데모 전용 코드(특정 문자열을 감지해 정해진 답변으로 강제 치환하거나, 특정 시나리오만 통과시키기 위한 하드코딩)를 새로 추가하지 않는다." — 아래는 이미 존재하는(새로 추가한 게 아닌) 위반 사례로, 진행 중인 정리 작업의 대상이다:
- `CONTEXT_RECOVERY_UTTERANCES`(`chatbot.py:250-255`): 정확히 두 개의 한국어 문자열과 일치할 때만 동작하는 exact-string 트리거.
- `_ensure_decorator_wrapper_context_reflected`(`chatbot.py:1645-1681`): 위 트리거 + "decorator" 언급 시, 조건이 맞으면 LLM 응답에 고정된 한국어 문장을 강제로 덧붙인다.
- `_ensure_resolved_context_recovery_answers_directly`(`chatbot.py:1683-1722`): 응답이 "명확화 요청처럼 보이면"(키워드 리스트 기반 판정, `_looks_like_clarification_request`) LLM 응답 전체를 고정된 한국어 단락으로 **완전히 대체**한다.
- `_ensure_prior_struggle_context_reflected`(`chatbot.py:1773-1814`), `_ensure_explicit_ltm_reference_reflected`(`chatbot.py:1816-1860`), `_ensure_episodic_context_reflected`(`chatbot.py:1593-1643`): 특정 키워드/토픽 조건에서 고정 템플릿 문장을 덧붙인다.
- 이 5개 가드는 `chat()` 호출마다 무조건 실행되므로(`chatbot.py:1211-1239`), 실사용 중에도 특정 조건에서 LLM 응답이 조용히 덮어써지거나 조작될 수 있다.
- `INFERRED_TOPIC_KEYWORDS`/`DEMO_TOPIC_EMBEDDING_AXES`(`chatbot.py:190-235`) + `build_demo_query_embedding`(`chatbot.py:622-635`): 검색 정확도 전체가 고정 키워드→축 매핑에 의존하는 fake one-hot 임베딩 — 위 "임베딩 전략" 항목과도 직결된다.

**적용 지침**: 이 두 위반은 이미 존재하는 코드이므로 이 문서를 쓰는 시점에 고치지는 않았다 — 진행 중인 GDG 데모 정리 작업(`[[project-memory-augmented-chatbot-refactor]]`)의 범위에서 다뤄야 한다. 다만 앞으로 이 CRITICAL 규칙을 위반하는 코드를 **새로** 추가하는 것은 금지된다(CLAUDE.md 원문 그대로).
