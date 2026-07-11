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
- **Gemini 호출에 타임아웃/재시도 정책이 전혀 없음**: 응답 생성(`_generate_gemini_reply`), topic tagging(`tagger.py`), consolidation 요약/Episodic 변환(`memory/consolidation.py`) 세 지점 모두 요청 타임아웃이나 재시도/backoff 로직이 없다(`docs/ARCHITECTURE.md` "설정" 참고). 데모 노트북 환경에서는 문제가 안 됐지만, PRD의 "실사용 가능한 형태로 정리" 목표를 고려하면 최소한 응답 생성 경로(실패 시 턴 전체가 죽고 예외가 그대로 전파됨)는 재시도 정책이 필요할 수 있다. 어느 수준까지 다룰지(단순 재시도 vs 사용자에게 에러 메시지 노출 vs 그대로 둠) 결정 필요.
- **세션 자동 종료 기능(`SessionEndCriteria`) 연결 여부**: `memory/session_manager.py`에 `max_turns`/`inactivity_timeout_seconds` 기반 자동 세션 종료가 이미 구현돼 있지만 `chatbot.py`의 `SessionManager(...)` 생성 코드(`chatbot.py:1097-1100`)가 이 값을 넘기지 않아 미사용 상태다. 켤지, 켠다면 기본 임계값을 얼마로 할지 결정 필요.

---

## 발견된 규칙 위반 (CLAUDE.md CRITICAL)

코드 전수 조사 중 CLAUDE.md의 CRITICAL 규칙을 실제로 위반하고 있는 지점을 발견해 기록한다. 우선순위를 정해 별도로 고쳐야 한다.

### 위반 1: `chatbot.py`가 `memory/*.py`를 거치지 않고 직접 SQL을 짬 — **해결됨**
CLAUDE.md: "STM/LTM/Episodic 저장·조회 로직은 반드시 `memory/*.py` 또는 `episodic_schema.py`를 통해서만 접근한다." — `chatbot.py`의 `DDL_LEARNER_PERSONA`, `init_demo_learner_persona`, `load_demo_learner_persona`가 `learner_persona` 테이블에 대해 이 규칙을 어기고 직접 SQL을 실행하고 있었다. 이 테이블은 `memory/*.py`/`episodic_schema.py` 어디에도 정의돼 있지 않았다.

**조치**: `load_demo_learner_persona`를 호출하는 곳이 전체 코드베이스(챗봇 코드, 노트북, 스크립트)에 하나도 없음을 grep으로 확인했다 — `init_demo_learner_persona`가 매 `_open_db()` 호출마다 `learner_persona` 테이블에 값을 써넣기만 하고, 그 값을 읽어 쓰는 곳이 없는 완전한 죽은 코드였다. `memory/*.py`로 옮겨 정당화하는 대신, DDL/write/read 세 함수와 관련 import(`DEMO_LEARNER_PERSONA`)를 통째로 삭제해 위반 자체를 없앴다. `memory/demo_fixture.py`의 `DEMO_LEARNER_PERSONA` 상수 자체는 그 파일의 다른 데모 픽스처 설명에 계속 쓰이므로 그대로 남겨뒀다.

### 위반 2: 데모 전용 하드코딩(정해진 문자열/시나리오만 통과시키는 강제 치환)
CLAUDE.md: "데모 전용 코드(특정 문자열을 감지해 정해진 답변으로 강제 치환하거나, 특정 시나리오만 통과시키기 위한 하드코딩)를 새로 추가하지 않는다." — 아래는 이미 존재하는(새로 추가한 게 아닌) 위반 사례다. 실사용 중 실제로 발동하는지 여부로 우선순위를 나눴다 — 정리 작업을 시작한다면 Tier A부터 먼저 볼 가치가 있다.

**Tier A — 실사용 중에도 실제로 발동함 (우선순위 높음)**: 게이팅 조건이 데모 전용 문자열이 아니라 일반적인 키워드/유사도 판정이라, 실제 사용자가 흔히 할 법한 질문에서도 그대로 실행된다. 다만 두 함수 모두 **가짜 문장을 지어내지는 않고** 실제로 검색된 LTM/Episodic 데이터를 템플릿에 꽂아 넣는 방식이라, 답변 내용 자체가 틀리지는 않는다 — 문제는 LLM의 실제 응답 문구를 문자열 포함 여부로 사후 검사해서 강제로 덧붙인다는 점이다(재현성·유지보수성 문제, docstring에도 "demo loop를 verifiable하게 유지하기 위해"라고 명시돼 있어 원래 목적이 데모 검증용이었음이 드러난다):
  - `_ensure_episodic_context_reflected`(`chatbot.py:1593-1643`): 게이팅 조건은 일반 repeat-detection(`repeat_metadata.is_repeat`, 임계값 0.86)뿐 — 어떤 주제든 사용자가 이전과 비슷한 질문을 반복하면 발동해 실제 Episodic 데이터(강점/약점/질문)로 답변에 문장을 덧붙인다.
  - `_ensure_prior_struggle_context_reflected`(`chatbot.py:1773-1814`): 게이팅 조건은 `_is_prior_struggle_question`(`chatbot.py:1863-1879`)이라는 일반 키워드 분류기 — "저번에/지난/전에" + "어려웠/헷갈렸" 또는 영어 등가 표현이 있으면 어떤 주제든 발동한다("지난번에 내가 뭘 어려워했지?"는 학습 챗봇에서 매우 자연스러운 실제 질문이다).

**Tier B — 이중 게이팅으로 데모 밖에서는 사실상 비활성 (우선순위 낮음, 하지만 규칙 위반 자체는 여전히 유효)**: 트리거가 정확히 두 개의 한국어 문자열(`CONTEXT_RECOVERY_UTTERANCES`, `chatbot.py:250-255`)이거나 부자연스러운 리터럴 문자열이라 실제 사용자가 우연히 발동시키기 어렵다. 다만 코드로서는 여전히 CLAUDE.md를 위반한다:
  - `is_context_recovery_utterance`(`chatbot.py:704-708`) 자체가 위 두 문자열에만 반응 — "모호한 발화 탐지"라는 이름과 달리 일반화돼 있지 않다.
  - `_ensure_decorator_wrapper_context_reflected`(`chatbot.py:1645-1681`), `_ensure_resolved_context_recovery_answers_directly`(`chatbot.py:1683-1722`, `_looks_like_clarification_request`로 판정된 응답을 고정된 한국어 단락으로 **완전히 대체**): 둘 다 `is_context_recovery_utterance` + decorator 관련 메모리 존재를 동시에 요구해 사실상 그 두 문자열 밖에서는 안 켜진다.
  - `_ensure_explicit_ltm_reference_reflected`(`chatbot.py:1816-1860`): 게이팅이 사용자 메시지에 리터럴 `"ltm"`/`"demo-ltm"` 문자열 포함 여부라, 실제 사용자가 시스템 내부 용어를 그대로 타이핑할 가능성은 낮다.
  - `build_stm_insufficiency_trigger/context`(`chatbot.py:735-783`)도 `is_context_recovery_utterance`에 종속돼 같은 카테고리.
  - `extract_referent_candidates`의 우선순위 로직(`_candidate_topic_matches_context`, `_referent_candidate_specificity`, `chatbot.py:546-564`)이 `programming:python-decorators`/`programming:python` 토픽 태그를 코드에 직접 명시 — 다른 토픽에서는 이 우선순위 로직이 적용 안 될 뿐 기능 자체가 깨지진 않는다(품질 저하 수준).
  - `INFERRED_TOPIC_KEYWORDS`/`DEMO_TOPIC_EMBEDDING_AXES`(`chatbot.py:190-235`) + `build_demo_query_embedding`(`chatbot.py:622-635`): 검색 정확도 전체가 고정 키워드→축 매핑에 의존하는 fake one-hot 임베딩. 이건 "실사용 중 발동 빈도"의 문제가 아니라 애초에 항상 이 임베딩으로 검색하므로 위 임베딩 전략 결정과 함께 다뤄야 한다(위 "임베딩 전략" 항목 참고, 별도로 더 높은 우선순위).

**요지**: `memory/demo_fixture.py`의 `DEMO_LEARNER_PERSONA`/`DEMO_STM_CONTEXT`/`DEMO_LTM_SUMMARY`가 이 파이프라인 전체가 맞아떨어지도록 설계된 고정 시나리오(데코레이터/wrapper/재귀를 배우는 초급 학습자)를 코드로 명시하고 있다. 하지만 Tier A 두 항목은 그 시나리오 밖에서도 실제로 켜지므로, "정리 대상"이라고 전부 같은 시급성으로 묶으면 안 된다 — Tier A는 지금 실사용 답변 품질/재현성에 영향을 주고, Tier B는 코드에서 지워야 할 데모 잔재이긴 하지만 당장 실사용자에게 보이는 문제는 아니다. 상세 표는 `docs/ARCHITECTURE.md`의 "컨텍스트 구성 파이프라인" 참고.

**적용 지침**: 위 항목들은 이미 존재하는 코드이므로 이 문서를 쓰는 시점에는 고치지 않았다(위반 1과 달리, 죽은 코드가 아니라 실제로 응답 생성 경로에 관여하므로 "위험 없이 삭제"가 아니라 재작성/일반화가 필요한 작업이다) — 진행 중인 GDG 데모 정리 작업(`[[project-memory-augmented-chatbot-refactor]]`)의 범위에서, Tier A부터 우선순위를 두고 다뤄야 한다. 다만 앞으로 이 CRITICAL 규칙을 위반하는 코드를 **새로** 추가하는 것은 금지된다(CLAUDE.md 원문 그대로).
