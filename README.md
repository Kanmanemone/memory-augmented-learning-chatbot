# Learning Chatbot Memory Prototype

단일 사용자 학습 챗봇에 STM, LTM, Episodic 3계층 메모리를 붙인 핸즈온 데모 프로젝트입니다.

대화 중에는 최근 메시지를 STM에 저장하고, 매 응답마다 STM/LTM/Episodic 메모리를 검색해 LLM 컨텍스트에 주입합니다. 세션이 끝나면 STM을 요약해 LTM으로 전이하고, 주제별 학습 이력은 Episodic 메모리에 누적합니다.

## 구성

```text
chatbot.py                    # 챗봇 진입점과 메모리 주입 루프
episodic_schema.py            # Episodic SQLite/Chroma 스키마와 upsert/search
memory/stm.py                 # STM 메시지 저장소
memory/ltm.py                 # LTM 요약 저장소와 Chroma 컬렉션
memory/retrieval.py           # keyword/semantic/hybrid 검색 입력과 점수 계산
memory/session_manager.py     # 세션 시작/종료 이벤트 처리
memory/consolidation.py       # 세션 종료 시 STM -> LTM/Episodic 전이
memory/tagger.py              # LLM 기반 topic tagging
tests/                        # 동작 검증 테스트
learning_chatbot_memory.seed.yaml
```

## Gemini 설정

이 프로젝트의 LLM 호출 경로는 Gemini API를 사용합니다.

- 챗봇 응답 생성: `gemini-2.5-flash`
- topic tagging: `gemini-2.5-flash`
- 세션 종료 후 STM -> LTM 요약/구조화: `gemini-2.5-pro`
- LTM -> Episodic 변환: `gemini-2.5-pro`

실제 Gemini API를 호출하려면 공식 Google SDK가 필요합니다.

```bash
pip install google-genai
```

API key는 홈 디렉터리의 `~/.env`에서 읽습니다.

```bash
GEMINI_API_KEY=your-api-key
```

기본 테스트는 fake/mock Gemini client 또는 deterministic fallback을 사용하므로 `GEMINI_API_KEY` 없이도 실행됩니다. 실제 Gemini API를 호출하는 integration test는 `GEMINI_API_KEY`가 있을 때만 실행되고, 없으면 skip됩니다.

## 메모리 계층

### 1. STM: Short-Term Memory

STM은 현재 세션의 raw 메시지를 SQLite `stm_messages` 테이블에 저장합니다.

필드:

- `id`: UUID primary key
- `session_id`: 대화 세션 ID
- `role`: `user`, `assistant`, `system`
- `content`: 메시지 원문
- `timestamp`: ISO 8601 생성 시각
- `turn_index`: 세션 내 순서

`Chatbot.chat()`은 사용자 메시지를 먼저 STM에 저장한 뒤, `build_messages_from_stm()`으로 최근 N개 메시지를 다시 읽어 LLM messages에 넣습니다. 그래서 매 응답은 현재 세션의 최근 대화 맥락을 항상 포함합니다.

### 2. LTM: Long-Term Memory

LTM은 세션 종료 후 생성되는 세션 단위 요약입니다. SQLite `ltm` 테이블에 구조화 필드를 저장하고, embedding이 있으면 Chroma `ltm_embeddings` 컬렉션에도 저장합니다.

필드:

- `id`
- `session_id`
- `summary`
- `struggles`
- `strengths`
- `confusions`
- `topic_tags`
- `embedding`
- `created_at`

세션 종료 시 `memory.consolidation.consolidate_session()`이 STM 메시지를 읽고 `ConsolidationAnalysis` 형태로 요약합니다. 기본 구현은 오프라인 데모가 가능하도록 deterministic analyzer와 작은 deterministic embedding을 사용합니다. 실제 LLM 요약기를 붙이고 싶으면 같은 인터페이스의 `analyzer`나 `embedding_fn`을 주입하면 됩니다.

### 3. Episodic Memory

Episodic은 주제별 사용자 특성을 누적하는 메모리입니다. SQLite `episodic_memory` 테이블에 topic, 강점, 약점, 질문 이력, 출처 메시지 메타데이터를 저장하고, topic embedding이 있으면 Chroma `episodic_topics` 컬렉션에도 저장합니다.

핵심 필드:

- `episodic_id`
- `topic`
- `topic_tags`
- `strengths`
- `weaknesses`
- `questions`
- `source_session_ids`
- `source_message_ids`
- `source_turn_indices`
- `source_message_timestamps`
- `topic_contexts`
- `occurrence_count`
- `topic_embedding`
- `last_updated`

주제 태그는 `category:topic-slug` 형식으로 정규화됩니다. 예를 들어 `Python decorators`는 `programming:python-decorators`처럼 저장됩니다. 중복 태그, 빈 태그, 낮은 confidence 태그는 정규화 또는 fallback 처리됩니다.

## 대화 처리 흐름

`Chatbot.chat(user_message)`의 주요 흐름은 다음과 같습니다.

1. 세션 종료 명령인지 확인합니다.
2. 사용자 메시지를 STM에 저장합니다.
3. 최근 STM 메시지를 읽어 LLM 대화 history로 만듭니다.
4. 사용자 메시지로 LTM 검색 입력을 만들고 관련 LTM을 검색합니다.
5. 사용자 메시지로 Episodic 검색 입력을 만들고 관련 주제 이력을 검색합니다.
6. LTM/Episodic 결과를 system context 문자열로 포맷합니다.
7. 동일 주제 재질문인지 Episodic topic/question history와 비교합니다.
8. STM messages와 확장된 system context로 LLM 응답을 생성합니다.
9. assistant 응답을 STM에 저장합니다.
10. user/assistant 턴을 topic tagging해서 Episodic에 upsert합니다.

응답 생성 시 사용된 메모리 출처는 `last_generation_memory_trace`에 남습니다. 이 trace의 `memory_sources`에는 각 항목의 `memory_type`(`STM`, `LTM`, `Episodic`), `source_id`, 점수, 출처 메타데이터가 포함됩니다. 응답 텍스트와 출처 정보를 한 번에 확인하려면 `Chatbot.chat_with_memory_trace(user_message)`를 사용합니다. 반복 질문 감지 결과는 `last_repeat_detection_metadata`에서 볼 수 있습니다.

## 세션 종료와 LTM 전이

세션은 다음 방식으로 종료될 수 있습니다.

- `bot.end_session()` 직접 호출
- `exit`, `quit`, `/end`, `:q`, `bye`, `종료`, `끝` 같은 종료 명령 입력
- `SessionEndCriteria`로 설정한 max turns 또는 inactivity timeout

종료되면 `SessionMemoryConsolidator`가 한 번만 실행됩니다.

1. STM 최근 메시지를 읽습니다.
2. 이미 같은 세션의 LTM이 있으면 중복 전이를 막습니다.
3. 세션 transcript를 요약해 `summary`, `struggles`, `strengths`, `confusions`, `topics`, `questions`, `embedding`을 만듭니다.
4. LTM SQLite row와 Chroma embedding을 저장합니다.
5. topic별 Episodic record를 upsert합니다.
6. 전이된 STM 메시지를 삭제하거나 보존 정책에 따라 유지합니다.
7. `memory_state` 테이블에 cursor, 전이 상태, 삭제 row 수, LTM ID, timestamp를 기록합니다.

## 검색 방식

검색 입력은 LTM과 Episodic에 대해 따로 만들어집니다.

- `semantic_query`: embedding/vector search용 자연어 쿼리
- `keyword_tokens`: keyword filtering/ranking용 정규화 토큰

`memory.retrieval`은 다음 점수를 계산합니다.

- semantic score: Chroma cosine distance를 0..1 범위로 정규화
- keyword score: 요청 keyword 중 매칭된 비율
- hybrid score: semantic score와 keyword score의 weighted average

기본 가중치는 semantic `0.3`, keyword `0.7`입니다. LTM과 Episodic 결과는 source metadata를 유지한 채 병합되고, hybrid score 기준으로 정렬됩니다.

## 동일 주제 재질문 처리

현재 메시지의 topic/query text를 정규화한 뒤, 검색된 Episodic record의 `topic`과 `questions`에 대해 similarity를 계산합니다.

반복 질문으로 판단되면 system context에 다음 정보가 추가됩니다.

- matched prior topic
- matched prior question
- confidence
- 해당 topic의 strengths, weaknesses, question history

LLM이 일반적인 답변을 하더라도 데모 검증이 가능하도록, 반복 질문인 경우 `_ensure_episodic_context_reflected()`가 Episodic context 사용 흔적을 응답에 반영합니다.

## 간단한 사용 예시

```python
from chatbot import Chatbot

bot = Chatbot()

print(bot.chat("Can you explain Python decorators?"))
print(bot.chat("I am confused about functools.wraps."))

# 세션 종료 시 STM -> LTM/Episodic 전이가 실행됩니다.
bot.end_session()
```

Gemini API를 실제로 호출하는 경로는 위의 `GEMINI_API_KEY` 설정을 사용합니다. 테스트와 일부 메모리 전이 로직은 mock 또는 deterministic fallback을 사용해 오프라인에서도 검증할 수 있습니다.

## 데이터 저장 위치

기본 데이터 파일:

- SQLite: `data/chatbot.db`
- Chroma: `data/chroma/`
- 실습용 원본 STM 입력 JSON: `data/demo_memory_stm.json`
- 완성 fixture/source data JSON: `data/demo_memory_fixture.json`

## 파이썬 학습 데모 fixture

데모 fixture는 파이썬 입문 학습자가 2026-05-01부터 2026-05-04까지 반복문, 조건문, 함수, 예외처리, 모듈, 클래스 순서로 학습하는 대화 흐름을 담습니다. 뒤 세션에는 이전 학습 질문을 다시 묻는 장면이 포함되어 STM, LTM, Episodic 검색과 승격 결과를 함께 확인할 수 있습니다.

두 JSON의 역할은 분리되어 있습니다.

- `data/demo_memory_stm.json`: Colab 핸즈온에서 처음 불러오는 실습용 입력 데이터입니다. 원본 STM 대화만 담고, 최상위에는 `stm_conversations`만 있으며, 각 conversation은 `session_id`, `recent_topic`, `messages`만 가집니다. LTM, Episodic, 기대 질문, DB 경로 같은 완성 산출물 정보는 넣지 않습니다.
- `data/demo_memory_fixture.json`: 완성 fixture/source data입니다. `data/demo_memory_stm.json`의 `stm_conversations`를 그대로 포함하고, 변환 후 비교용 `ltm_summaries`, `episodic_memories`, `demo_questions`, `expected_memory_sources`, `demo_database_paths`를 함께 제공합니다.
- `data/chatbot.db`와 `data/chroma/`: JSON fixture에서 생성하거나 검증하는 실행 산출물입니다. 재생성할 수 있는 런타임 데이터이므로 fixture의 원본 역할은 두 JSON이 담당합니다.

STM 메시지는 raw 대화 턴입니다. 필수 필드는 `session_id`, `role`, `content`, `timestamp`, `turn_index`이고, 데모 데이터는 추적을 쉽게 하려고 `id`와 `memory_type`도 포함합니다.

LTM 항목은 세션 단위 요약입니다. `id`, `session_id`, `summary`, `struggles`, `strengths`, `confusions`, `topic_tags`, `embedding`, `created_at`을 유지합니다. STM -> LTM 승격은 마지막 STM 메시지 이후 3시간 동안 대화가 없을 때 처리합니다.

Episodic 항목은 주제별 누적 학습 기억입니다. `episodic_id`, `topic`, `topic_tags`, `strengths`, `weaknesses`, `questions`, `source_session_ids`, `source_message_ids`, `source_turn_indices`, `source_message_timestamps`, `topic_embedding`, `last_updated`, `memory_item_type`을 유지합니다. LTM -> Episodic 승격은 매일 03시에 처리합니다.

### Colab 수동 변환 API

Colab에서는 별도 CLI 없이 함수 import만으로 수동 변환을 실행할 수 있습니다. 가장 단순한 전체 변환은 원본 STM JSON을 SQLite와 Chroma 산출물로 재생성합니다.

```python
from memory import (
    load_demo_stm_json,
    promote_ltm_to_episodic_daily,
    promote_stm_to_ltm_if_idle,
    run_demo_memory_conversion,
)

fixture = load_demo_stm_json("data/demo_memory_stm.json")

result = run_demo_memory_conversion(
    input_path="data/demo_memory_stm.json",
    db_path="data/chatbot.db",
    chroma_dir="data/chroma",
    mode="all",
)

print(result["logs"])
```

`run_demo_memory_conversion()`은 stdout에 `load_stm_fixture`, `promote_stm_to_ltm`, `promote_ltm_to_episodic`, `sync_ltm_chroma`, `sync_episodic_chroma` 단계를 출력하고, 같은 판단을 `result["logs"]` list/dict structured log로 반환합니다. 각 log entry에는 `action`, 처리된 `session_id` 또는 `ltm_id`, 승격/skip 사유, 생성된 `ltm_id` 또는 `episodic_id`, 처리 건수 같은 값이 들어갑니다.

### 변환 로그 확인 기준

Colab/manual 변환 흐름은 사람이 바로 읽는 stdout 로그와 노트북에서 필터링하기 쉬운 structured log를 함께 남깁니다. stdout 로그는 단계별 prefix로 처리 흐름을 확인하는 용도이고, structured log는 `result["logs"]`, `stm_to_ltm["logs"]`, `episodic_result["logs"]`에서 dict list로 확인하는 용도입니다.

전체 변환에서 기대되는 대표 stdout 로그는 다음과 같습니다.

```text
[DemoConversion] load_stm_fixture input_file=data/demo_memory_stm.json sessions=... messages=... processed_session_ids=[...]
[STM->LTM] promote_stm_to_ltm session_id=... ltm_id=... messages=... promotion_condition=idle_3h idle_3h_condition_met=True idle_hours=...
[STM->LTM] skip_active_session session_id=... promotion_condition=idle_3h idle_3h_condition_met=False idle_hours=...
[STM->LTM] skip_existing_ltm session_id=... ltm_id=... promotion_condition=idle_3h idle_3h_condition_met=True idle_hours=...
[DemoConversion] sync_ltm_chroma row_count=...
[LTM->Episodic] promote_ltm_to_episodic ltm_id=... episodic_id=... topic=... target_date=... audit_status=promoted
[LTM->Episodic] skip_ltm_date_mismatch ltm_id=... target_date=... created_date=... audit_status=skipped
[LTM->Episodic] skip_existing_episodic ltm_id=... session_id=... audit_status=already_processed
[LTM->Episodic] skip_outside_run_at_window run_at=03:00 actual_time=04:00 window=03:00-03:59
[DemoConversion] sync_episodic_chroma row_count=...
[DemoConversion] completed mode=all
```

structured log에서는 `action`으로 각 판단을 구분합니다. `load_stm_fixture`에는 `input_file`, `session_count`, `message_count`, `processed_session_ids`, `db_path`가 들어갑니다. `promote_stm_to_ltm`, `skip_active_session`, `skip_existing_ltm`에는 `session_id`, `message_count`, `promotion_condition`, `idle_3h_condition_met`, `idle_hours_elapsed`, `required_idle_hours`, `last_message_timestamp`, `evaluated_at`이 들어가고, 승격된 항목에는 `ltm_id`, `deleted_stm_rows`, `topic_tags`도 포함됩니다.

LTM -> Episodic structured log는 `promote_ltm_to_episodic`, `skip_ltm_date_mismatch`, `skip_existing_episodic`, `skip_outside_run_at_window`, `no_ltm_rows_for_episodic` 같은 `action`을 사용합니다. 승격 log에는 `ltm_id`, `episodic_id`, `session_id`, `topic`, `topic_tags`, `strengths`, `weaknesses`, `questions`, `run_at`, `date`, `target_date`, `audit_status`가 들어갑니다. skip log에는 `skip_reason`, `created_at`, `created_date`, `promotion_metadata_found`, `actual_time`, `window_start`, `window_end`처럼 왜 처리하지 않았는지 확인할 수 있는 필드가 들어갑니다. Chroma 동기화 log는 `sync_ltm_chroma`, `sync_episodic_chroma` `action`과 `row_count`, `chroma_dir`로 산출물 적재 건수를 확인합니다.

### 승격 출력과 상태 표시 해석

변환 결과는 `promoted_count`, `skipped_count`, `already_processed_count`, `eligible_count`, `audit_counts` 같은 집계값과 `logs`의 개별 판단을 함께 읽습니다. 집계값은 전체 실행 상태를 빠르게 보는 지표이고, `logs`는 특정 세션이나 LTM 항목이 왜 승격되었거나 건너뛰어졌는지 확인하는 근거입니다.

STM -> LTM 단계에서는 `action`이 상태 표시 역할을 합니다.

| `action` | 의미 | 확인할 필드 |
| --- | --- | --- |
| `promote_stm_to_ltm` | STM 세션이 LTM 요약으로 승격됨 | `session_id`, `ltm_id`, `message_count`, `topic_tags`, `deleted_stm_rows` |
| `skip_active_session` | 마지막 메시지 이후 3시간이 지나지 않아 아직 STM에 머무름 | `idle_3h_condition_met=False`, `idle_hours_elapsed`, `required_idle_hours`, `last_message_timestamp`, `evaluated_at` |
| `skip_existing_ltm` | 같은 `session_id`의 LTM이 이미 있어 중복 승격을 막음 | `session_id`, `ltm_id`, `idle_3h_condition_met=True` |
| `skip_empty_session` | 대상 세션의 STM 메시지가 없음 | `session_id`, `message_count=0` |
| `skip_target_date_mismatch` | 날짜별 실습에서 해당 날짜의 STM 메시지가 없음 | `target_date`, `source_dates`, `available_message_count` |

STM -> LTM에서 가장 중요한 상태 지표는 `promotion_condition="idle_3h"`와 `idle_3h_condition_met`입니다. `idle_3h_condition_met=True`인데 `promote_stm_to_ltm`이면 실제 승격이고, `skip_existing_ltm`이면 이미 처리된 세션입니다. `idle_3h_condition_met=False`는 학습 대화가 아직 진행 중이라는 뜻이라 `promoted_count`에 포함되지 않습니다.

LTM -> Episodic 단계에서는 `audit_status`가 최종 상태 표시입니다.

| `audit_status` | 대표 `action` | 의미 | 확인할 필드 |
| --- | --- | --- | --- |
| `promoted` | `promote_ltm_to_episodic` | 해당 날짜의 LTM이 주제별 Episodic 기억으로 승격됨 | `ltm_id`, `episodic_id`, `topic`, `topic_tags`, `target_date`, `run_at` |
| `skipped` | `skip_ltm_date_mismatch` | LTM의 `created_at` 날짜가 실행 대상 날짜와 달라 제외됨 | `created_date`, `target_date`, `skip_reason=target_date_mismatch` |
| `already_processed` | `skip_existing_episodic` | 같은 LTM 또는 세션 출처가 이미 Episodic에 반영되어 중복 처리하지 않음 | `ltm_id`, `session_id`, `promotion_metadata_found`, `skip_reason=already_processed` |
| 없음 | `skip_outside_run_at_window` | 요청 시간이 daily `run_at` 실행 창 밖이라 전체 Episodic 승격을 실행하지 않음 | `run_at`, `actual_time`, `window_start`, `window_end` |
| `skipped` | `no_ltm_rows_for_episodic` | 대상 날짜에 평가할 LTM row가 없음 | `date`, `target_date`, `skip_reason=no_ltm_rows` |

LTM -> Episodic에서 `eligible_count`는 실제로 승격 후보가 된 LTM 수이고, `promoted_count`는 Episodic row로 반영된 수입니다. `audit_counts["already_processed"]`가 증가하면 실패가 아니라 idempotent 재실행에서 이미 처리된 항목을 확인했다는 뜻입니다. `skip_outside_run_at_window`는 03:00-03:59 실행 창 검증 결과이므로, daily 승격을 보려면 `date="YYYY-MM-DDT03:00:00+09:00"`처럼 03시대 시간을 넣어 다시 실행합니다.

Chroma 동기화 상태는 `sync_ltm_chroma`와 `sync_episodic_chroma` 로그의 `row_count`로 확인합니다. `row_count`가 SQLite의 LTM/Episodic row 수와 맞으면 벡터 검색 산출물이 현재 fixture 변환 결과를 반영한 상태입니다.

단계별 실습에서는 `mode`를 나누어 실행할 수 있습니다.

```python
# 1. STM JSON을 읽고 형태를 확인합니다.
fixture = load_demo_stm_json("data/demo_memory_stm.json")
sessions = [
    {
        "session_id": conversation["session_id"],
        "recent_topic": conversation["recent_topic"],
    }
    for conversation in fixture["stm_conversations"]
]

# 2. 원본 STM만 SQLite에 적재합니다.
load_result = run_demo_memory_conversion(
    input_path="data/demo_memory_stm.json",
    db_path="data/chatbot.db",
    chroma_dir="data/chroma",
    mode="load_stm",
)

print(load_result["loaded_stm_count"])
print(load_result["logs"][0])
```

STM -> LTM 승격 규칙은 마지막 STM 메시지 이후 3시간 동안 대화가 없을 때입니다. Colab에서 직접 시간을 지정해 승격 판단을 확인할 수 있습니다.

```python
import sqlite3

conn = sqlite3.connect("data/chatbot.db")
conn.row_factory = sqlite3.Row

stm_to_ltm = promote_stm_to_ltm_if_idle(
    conn,
    sessions,
    idle_hours=3,
    now="2026-05-04T17:30:00+09:00",
)

print(stm_to_ltm["promoted_count"])
print(stm_to_ltm["logs"])
conn.close()
```

LTM -> Episodic 승격 규칙은 매일 03시 실행입니다. `date`에 03시가 아닌 시간을 넣으면 `skip_outside_run_at_window` log로 판단 근거가 반환됩니다.

```python
conn = sqlite3.connect("data/chatbot.db")
conn.row_factory = sqlite3.Row

episodic_result = promote_ltm_to_episodic_daily(
    conn,
    run_at="03:00",
    date="2026-05-04T03:00:00+09:00",
)

print(episodic_result["promoted_count"])
print(episodic_result["logs"])
conn.close()
```

CLI처럼 한 줄로 재실행하고 싶을 때도 같은 함수 API를 사용합니다.

```python
result = run_demo_memory_conversion(mode="all")
for entry in result["logs"]:
    print(entry["action"], entry)
```

일부 Episodic helper의 기본 경로는 `episodic_schema.py` 내부에 `memory.db`, `chroma_db`로 정의되어 있지만, 챗봇 실행 경로에서는 `Chatbot.db_path`를 통해 `data/chatbot.db`를 넘겨 같은 DB에 저장하도록 연결합니다.

## 테스트

전체 테스트:

```bash
pytest -q
```

현재 검증된 결과:

```text
203 passed, 1 skipped
```

테스트는 다음을 포함합니다.

- STM 스키마와 최근 N개 메시지 조회
- LTM 스키마, 저장, embedding 경로
- Episodic 스키마, topic tag 정규화, upsert
- 세션 종료 시 STM -> LTM 전이
- 중복 전이 방지와 memory_state 갱신
- LTM/Episodic 검색 입력과 hybrid ranking
- 챗봇 응답 생성 시 STM/LTM/Episodic context 주입
- 동일 주제 재질문 시 Episodic context 반영

## Seed 실행 결과

이 구현은 `learning_chatbot_memory.seed.yaml`을 기준으로 Ouroboros `ooo run`을 실행해 생성·검증되었습니다.

완료 상태:

- Acceptance Criteria: `8/8`
- Sub-AC: `62/62`
- Local test: `203 passed, 1 skipped`
