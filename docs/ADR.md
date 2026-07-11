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

---

## 결정 대기 중

아래는 검토 중 발견했지만 아직 결론을 내리지 않은 사안들이다. 임의로 정하지 말고, 실제로 손댈 시점에 다시 논의한다.

- **유휴시간(3시간)/매일 03시 배치 승격을 제품에 남길지**: `memory/demo_conversion.py`의 `promote_stm_to_ltm_if_idle`, `promote_ltm_to_episodic_daily`는 현재 실제 챗봇 흐름(`consolidate_session`)과 별개로 존재하고 스케줄러도 없다. 유지하려면 실제 스케줄러 연결이 필요하고, 안 쓸 거면 통째로 삭제 대상.
- **임베딩 전략**: 지금은 서로 다른 3곳(consolidation.py 3차원, demo_conversion.py 자체 방식, chatbot.py 384차원)에 호환되지 않는 deterministic fallback이 흩어져 있다. 실제 Gemini 임베딩 API를 붙일지, 일단 하나의 fallback으로 통일만 할지 결정 필요.
- **Python lint/test 툴체인 (챗봇 본체)**: `tests/`가 비어있는 채로 pytest만 쓸지, ruff 등 lint를 더할지 아직 미정. 하네스 Stop 훅(ADR-003)이 이 결정을 기다리고 있다. (하네스 자체 테스트의 pytest 채택은 ADR-004로 별도 결정됨.)
- **하네스가 아직 실전 투입된 적 없음**: `phases/` 디렉터리 자체가 존재하지 않는다 — `scripts/execute.py`, `.claude/commands/harness.md`, `scripts/test_execute.py`는 갖춰졌지만 실제 task/step으로 end-to-end 실행된 적이 없다. `python3 scripts/execute.py {task-name}` 커맨드(CLAUDE.md)는 `phases/{task-name}/index.json`이 있어야 동작하므로, 첫 실사용 시 `phases/` 스캐폴딩부터 만들어야 한다.
