# lossy_clone 데이터 흐름

단계(phase)별 데이터 흐름을 mermaid로 정리합니다. 각 단계는 이전 단계의 흐름 위에 새 단계가 순서대로 끼워지는 방식으로 쌓입니다 (`docs/ADR.md` ADR-003 — 단계적으로 쌓는다). 아직 만들지 않은 단계는 다이어그램 없이 예정으로만 남겨둡니다.

## 1단계 — Chatbot + STM

```mermaid
sequenceDiagram
    actor User
    participant Bot as Chatbot.chat()
    participant DB as SQLite (stm_messages)
    participant Gemini as Gemini API

    User->>Bot: "Can you explain decorators?"
    Bot->>DB: INSERT (role='user', content=..., turn_index=N)
    Bot->>DB: SELECT ... WHERE session_id=? ORDER BY turn_index DESC LIMIT 20
    DB-->>Bot: 최근 대화 이력 (turn_index 오름차순으로 재정렬)
    Bot->>Gemini: generate(messages=[...최근 이력...])
    Gemini-->>Bot: "Decorators let you wrap a function..."
    Bot->>DB: INSERT (role='assistant', content=..., turn_index=N+1)
    Bot-->>User: "Decorators let you wrap a function..."
```

- **`turn_index`**: `session_id` 내 메시지 저장 순서, 0부터 시작하는 정수. 정렬 기준은 `timestamp`가 아니라 `turn_index` — 정수 비교가 확정적이고, `UNIQUE(session_id, turn_index)`로 DB 레벨에서 중복 순번 방지.
- **왜 매번 최근 이력을 다시 가져오는가**: Gemini API는 stateless, 이전 턴을 기억 안 함. 맥락 유지하려면 매 요청마다 이전 대화를 통째로 다시 전송해야 함. STM이 그 역할, `chat()` 호출마다 최근 N개 재조회 후 LLM에 전달.
- **`get_recent_messages()`에서 정렬을 두 번 하는 이유**: "어떤 메시지를 고를지"와 "고른 걸 어떤 순서로 넘길지"는 별개 문제. SQL은 `turn_index DESC LIMIT N`으로 조회 — 여기서 DESC가 맞음(ASC로 하면 최신 N개가 아니라 가장 오래된 N개가 잡힘). 이렇게 고른 N개를 그대로 LLM에 넘기면 최신 메시지가 맨 앞에 오는, 시간이 거꾸로 가는 대화록이 되어버리므로 파이썬 `reversed()`로 다시 오름차순(대화가 실제 흘러간 순서)으로 뒤집은 뒤 반환. 고르는 기준은 내림차순, LLM에 넘기는 순서는 오름차순 — 둘 다 맞는 동작이고 목적이 다름.
- **Gemini는 메시지 하나가 아니라 대화 전체를 받음**: `chat()`은 현재 사용자 메시지를 STM에 먼저 저장한 뒤 `get_recent_messages()`를 호출 → 과거 턴 + 방금 저장한 현재 메시지까지 전부(최대 `history_limit`, 기본 20) 포함. 이 리스트가 그대로 `generate(messages=...)`에 전달되고, `GeminiLLMClient`가 이를 Gemini `contents` 배열로 변환(메시지당 entry 1개, `assistant`→`model`). 매 요청마다 대화 전체를 통째로 받는 구조.
- **`generate()`는 fetch 함수가 아님**: fetch는 앞 단계 `SELECT`에서 이미 끝났다. `generate(messages=...)`는 앞서 설명한 대화 전체(이력)를 입력으로 받아 다음 응답 텍스트만 생성한다. 순서: `SELECT` → 이력 반환 → `generate(...)`. Gemini REST 엔드포인트 이름(`generateContent`)에 맞춘 네이밍이다.
- **Chroma(벡터 DB)는 1단계에서 안 씀**: `lossy_clone/` 전체에 Chroma 관련 코드가 없고, `lossy_clone/requirements.txt`에도 `requests`만 있다 (저장소 루트의 `requirements.txt`에는 `chromadb`가 있지만, 그건 원본 프로젝트 것이고 `lossy_clone`은 참조하지 않는다). `docs/PRD.md`의 "MVP 제외 사항 (1단계 기준)"에 "Chroma 등 벡터 DB, 임베딩 검색"이 명시적으로 빠져 있고, 1단계는 SQLite 기반 STM만 쓴다. Chroma는 원본 프로젝트(루트의 `episodic_schema.py`, `memory/ltm.py` 등)에서 LTM/Episodic 임베딩 검색용으로 쓰이는 것으로, `lossy_clone`에서는 아직 없는 2~4단계(LTM/Episodic/검색·통합)에서나 등장할 가능성이 있다.

## 2단계 — LTM

```mermaid
sequenceDiagram
    actor User
    participant Bot as Chatbot.end_session()
    participant DB as SQLite (stm_messages / ltm)
    participant Gemini as Gemini API

    User->>Bot: end_session()
    Bot->>DB: SELECT ... WHERE session_id=? ORDER BY turn_index ASC (limit=None)
    DB-->>Bot: 세션 전체 이력 [(user, "decorators?"), (assistant, "Decorators let you wrap..."), ...]
    Bot->>Gemini: generate(messages=[...전체 이력, {role:"user", content:"위 대화의 핵심 내용을 한국어로 간단히 요약하라"}])
    Gemini-->>Bot: "사용자는 Python 데코레이터와 functools.wraps에 대해 질문함"
    Bot->>DB: INSERT INTO ltm (id, session_id, summary, created_at)
    Note over Bot: 3단계가 없다면 여기서 바로 User에게 반환한다.<br/>3단계가 추가된 뒤로는 아래 3단계 흐름이 끝난 뒤에 반환된다.
```

- **`DB as SQLite (stm_messages / ltm)`의 `/`는 "또는"(둘 다 해당) 표시다**: `stm_messages`와 `ltm`은 같은 SQLite 파일(`lossy_clone/data/chatbot.db`) 안의 서로 다른 테이블이지만, 물리적으로 하나의 DB이므로 다이어그램에 테이블마다 따로 그리지 않고 `DB` 하나로 합쳐서 표시했다. 위 다이어그램에서 첫 번째 `SELECT`는 `stm_messages`를 조회하고, `INSERT INTO ltm`은 `ltm`에 쓴다 — 같은 `DB`가 두 테이블을 번갈아 오갈 뿐이다.
- **`end_session()`은 `chat()`과 트리거 시점이 다르다**: `chat()`은 매 턴 호출되지만 `end_session()`은 호출자가 세션을 끝내고 싶을 때 명시적으로 부른다. 원본의 exit-keyword 감지·`max_turns`·`inactivity_timeout` 같은 자동 판단은 없다 — `docs/ADR.md` ADR-006.
- **이 다이어그램은 `end_session()`이 User에게 반환하는 지점을 보여주지 않는다**: `end_session()`은 함수 전체가 끝난 뒤 딱 한 번만 반환한다. 3단계가 추가되기 전에는 `INSERT INTO ltm` 직후가 곧 반환 지점이었지만, 3단계가 그 뒤에 이어지므로 실제 반환은 아래 3단계 다이어그램의 마지막 화살표에서 일어난다 — 두 다이어그램은 같은 한 번의 함수 호출을 앞/뒤로 나눠 보여주는 것이지, 두 번 반환하는 게 아니다.
- **왜 `limit=None`으로 전체 이력을 다시 읽는가**: `chat()`의 대화 컨텍스트(`history_limit`, 기본 20개)와 요약 대상은 서로 다른 개념이다. 세션이 20턴을 넘으면 `chat()`은 최근 20개만 LLM에 보내지만, `end_session()`은 세션 전체를 요약해야 하므로 별도로 전체 조회가 필요하다.
- **STM 이력이 비어 있으면 아무 것도 하지 않는다**: `history`가 빈 리스트면 LLM을 호출하지 않고 `None`을 반환한다. 빈 대화를 요약시키는 것은 의미가 없고, 불필요한 LLM 호출 비용도 아낀다.
- **요약 지시 메시지는 STM 이력 뒤에 `role="user"`로 붙는다**: 이력 앞에 `role="system"`으로 붙이지 않는 이유는 `docs/ADR.md` ADR-009 참고 — STM 이력은 항상 `(user, assistant)` 쌍으로 끝나므로, 지시를 `systemInstruction`으로만 보내면 Gemini에 전달되는 `contents`의 마지막 turn이 `model`로 끝나버려 빈 응답이 돌아올 수 있다. 이 지시 메시지는 `chat()`이 저장하는 실제 대화 기록이 아니라 이번 `generate()` 호출에만 쓰이는 일회성 지시이므로 `add_message()`로 `stm_messages`에 남기지 않는다.
- **LTM엔 `summary` 하나만 담는다**: 원본의 `struggles`/`strengths`/`confusions`/`topic_tags`/`embedding`은 넣지 않는다. 강점/약점/혼란 누적은 3단계(Episodic memory)의 몫이다 — `docs/ADR.md` ADR-005.
- **`end_session()`을 여러 번 호출하면 LTM에 요약이 중복으로 쌓인다**: STM은 지우지 않으므로 다시 호출하면 같은 이력을 또 요약해 새 row를 만든다. 이를 막는 상태 추적은 의도적으로 만들지 않았다 — `docs/ADR.md` ADR-006.

## 3단계 — Episodic memory

Episodic memory는 세션에서 다룬 학습 주제별로 사용자의 강점(strengths)/약점(weaknesses)/질문(questions)을 기록하는 계층이다. `end_session()`이 LLM에게 "이 대화에서 어떤 주제들을 다뤘는지, 주제별로 무엇을 잘하고 무엇을 어려워했는지 뽑아달라"고 요청하면, LLM은 `{"topics": [{"topic": "decorators", "strengths": [...], "weaknesses": [...], "questions": [...]}, ...]}` 형태로 답한다. 이 `topics` 배열의 원소 하나가 대화에서 다룬 주제 하나이며, `episodic` 테이블의 row 하나가 된다 — 한 세션에서 여러 주제를 다뤘으면(예: decorators, recursion) row도 그만큼 여러 개 생긴다.

```mermaid
sequenceDiagram
    actor User
    participant Bot as Chatbot.end_session()
    participant DB as SQLite (stm_messages / ltm / episodic)
    participant Gemini as Gemini API

    Note over Bot,DB: 2단계: LTM 요약 저장까지 끝난 직후, 같은 end_session() 호출 안에서 바로 이어짐 (User에게는 아직 반환 전)
    Bot->>Gemini: generate(messages=[...전체 이력, {role:"user", content:"...topics 배열로 답하라..."}])
    Gemini-->>Bot: 응답 텍스트(마크다운 코드펜스로 감싸인 topics 배열 JSON)
    Bot->>Bot: 코드펜스 제거 후 json.loads (실패하면 여기서 조용히 중단, LTM 결과엔 영향 없음)
    Bot->>DB: INSERT INTO episodic (id, session_id, topic, strengths, weaknesses, questions, created_at)
    Bot-->>User: "사용자는 Python 데코레이터와 functools.wraps에 대해 질문함" (end_session() 전체를 통틀어 딱 한 번뿐인 반환)
```

- **이 다이어그램은 2단계 다이어그램의 연속이다, 별도 호출이 아니다**: 위 두 다이어그램을 합쳐야 `end_session()` 한 번 호출의 전체 그림이 된다. `Bot-->>User` 반환 화살표가 이 다이어그램에만 있는 이유는, 2단계 코드만 있을 때는 `INSERT INTO ltm` 직후가 반환 지점이었지만 3단계가 그 사이에 끼어들면서 반환 지점이 여기로 밀렸기 때문이다 — 2단계 다이어그램의 `Note`를 참고.
- **`DB` 레인에 `episodic`이 추가됐다**: 2단계 섹션에서 설명한 것과 같은 이유로, `stm_messages`/`ltm`/`episodic` 모두 같은 SQLite 파일 안의 서로 다른 테이블이라 다이어그램에서 `DB` 하나로 합쳐 표시한다.
- **LTM 요약과 별도의 `generate()` 호출이다**: 한 응답에 "요약 텍스트"와 "주제별 topics 배열"을 함께 요청하지 않는다. 요약은 자유 형식 텍스트인 반면 topics 배열은 위에서 설명한 구조화된 JSON이라 프롬프트 성격이 달라서 분리했다 — `docs/ADR.md` ADR-008.
- **이 지시 메시지도 STM 이력 뒤에 `role="user"`로 붙는다**: 2단계와 같은 이유다 — `docs/ADR.md` ADR-009.
- **순서가 고정되어 있다**: LTM 요약 호출(2단계)이 먼저이고, 그 호출이 실패하면 이 흐름(Episodic 추출) 자체가 시도되지 않는다. 반대로 이 흐름이 실패해도(예외, JSON 파싱 실패) 이미 저장된 LTM 요약에는 영향을 주지 않고 조용히 건너뛴다 — ADR-008.
- **코드펜스 제거가 필요한 이유**: 실제 Gemini는 "JSON만 출력하라"고 지시해도 응답을 마크다운 코드펜스(json 코드블록)로 감싸서 반환하는 경우가 흔하다. `GeminiLLMClient`는 JSON 강제 옵션(`response_mime_type` 등)을 쓰지 않으므로, `json.loads()` 이전에 코드펜스를 벗겨내는 전처리가 없으면 실제 API에서는 매번 파싱에 실패해 아무것도 저장되지 않는다.
- **`topic`이 빈 문자열인 항목은 저장하지 않는다**: LLM이 형식은 맞췄지만 내용이 빈 항목을 만들 수 있어서, 저장 직전에 걸러낸다. `strengths`/`weaknesses`/`questions`가 없거나 리스트가 아니면 빈 리스트로 취급한다.
- **Episodic엔 `topic`/`strengths`/`weaknesses`/`questions`만 담는다**: 원본의 taxonomy(9개 카테고리), confidence score, 임베딩+Chroma, 반복 감지(`SequenceMatcher`), source tracking, `occurrence_count`는 넣지 않는다 — `docs/ADR.md` ADR-007.
- **같은 topic이 반복돼도 병합(upsert)하지 않는다**: LTM과 동일하게(ADR-006) 매번 새 row로 쌓인다. "반복 질문 감지" 같은 병합/중복 판단은 `docs/PRD.md` 4단계 스코프다 — ADR-007.

## 4단계 — 검색/통합 (예정)

LTM/Episodic 검색, 반복 질문 감지, 통합 컨텍스트 구성이 추가됩니다. 아직 구현되지 않았습니다.
