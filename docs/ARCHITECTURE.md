# 아키텍처

## 독립성 원칙
`lossy_clone/`은 상위 프로젝트(`memory-augmented-learning-chatbot`)의 어떤 파일도 import하지 않는다. 이 폴더 하나만 잘라서 다른 위치에 옮겨도 그대로 실행되어야 하므로, 모든 코드/데이터/의존성 목록은 이 폴더 안에서 자기 완결적으로 유지한다. (문서(`docs/PRD.md`, `ARCHITECTURE.md`, `ADR.md`)는 하네스 프레임워크가 루트 `docs/`를 가드레일로 읽는 구조상 예외적으로 리포지토리 루트에 둔다.)

## 디렉토리 구조 (단계적으로 채워짐)
```
lossy_clone/
├── README.md              # 실행 방법
├── requirements.txt        # 최소 의존성
├── chatbot.py              # 1단계: Chatbot 클래스, chat() / 2단계: end_session() / 3단계: end_session() 내부 episodic 추출
├── memory/
│   ├── stm.py               # 1단계: 세션 내 최근 메시지 저장/조회
│   ├── ltm.py                # 2단계: 세션 요약 저장/조회
│   └── episodic.py           # 3단계: 주제별 강점/약점/질문 저장/조회
└── data/                    # 로컬 저장소 (SQLite 등), 실행 시 생성
```

파일은 필요해지는 단계에서만 추가한다. 4단계 이후 필요한 파일은 그때 결정한다.

## 패턴
원본의 "3계층 메모리(STM/LTM/Episodic)"라는 개념 구분은 그대로 따라가지만, 각 계층은 독립된 최소 구현으로 단계마다 새로 짠다. 계층 간 결합은 원본처럼 촘촘한 상호 참조(트레이스, 통합 컨텍스트 병합 등)를 그대로 옮기지 않고, 각 단계에서 필요한 최소한의 연결만 만든다.

## 데이터 흐름 (1단계)
```
사용자 입력
  → Chatbot.chat(message)
  → STM에 사용자 메시지 저장
  → STM에서 최근 대화 이력 읽기
  → 응답 생성 (LLM 호출)
  → STM에 응답 저장
  → 응답 반환
```
4단계가 추가되면 "응답 생성 시 LTM/Episodic 조회" 단계가 순서대로 끼워진다.

## 데이터 흐름 (2단계)
```
세션 종료
  → Chatbot.end_session()
  → STM에서 세션 전체 이력 읽기 (get_recent_messages(limit=None))
  → 이력이 비어 있으면 종료 (LLM 호출/저장 없이 None 반환)
  → STM 이력 뒤에 요약 지시 메시지(role="user")를 덧붙여 LLM에 전달 (LLMClient.generate(...))
  → 응답을 LTM에 저장 (memory.ltm.save_summary(...))
  → 요약 텍스트 반환
```
`end_session()`은 `chat()`과 달리 매 턴이 아니라 세션이 끝날 때 한 번(또는 호출자가 원할 때마다) 실행된다. 요약 지시 메시지는 LLM 호출에만 쓰이고 STM에는 저장되지 않는다. 지시 메시지를 이력 **뒤에** `role="user"`로 붙이는 이유는 `docs/ADR.md` ADR-009 참고 — 이력 앞에 `role="system"`으로만 붙이면 Gemini에 보내는 마지막 turn이 `model`로 끝나 빈 응답이 돌아올 수 있다.

## 데이터 흐름 (3단계)
```
(2단계의 LTM 저장까지 끝난 뒤, 같은 end_session() 호출 안에서 이어짐)
  → STM 이력 뒤에 별도의 episodic 지시 메시지(role="user")를 덧붙여 LLM에 전달 (LLMClient.generate(...))
  → 응답에서 코드펜스 제거 후 JSON 파싱 ({"topics": [...]} 형태 기대)
  → topic이 빈 문자열인 항목 제외, 유효한 항목만 Episodic에 저장 (memory.episodic.save_episodes(...))
  → (실패 시) 예외/파싱 실패는 조용히 무시 — 이미 저장된 LTM 요약에는 영향 없음
  → 요약 텍스트 반환 (2단계와 동일, Episodic 저장 결과는 반환값에 포함되지 않음)
```
LTM 요약 호출이 실패하면 이 흐름 자체가 시도되지 않는다(순서 고정). 반대로 이 흐름이 실패해도 `end_session()`은 요약을 정상 반환한다.

## 상태 관리
- 세션 내 대화 상태(STM)는 로컬 SQLite 파일(`lossy_clone/data/` 아래)에 저장한다. 원본과 동일하게 파일 기반으로 가고, 필드 이름과 구성도 기본적으로 원본을 따르되 이름이 좋지 않은 필드만 바꾼다.
- 세션 요약(LTM)은 2단계부터, 주제별 강점/약점/질문(Episodic)은 3단계부터 같은 SQLite 파일의 `ltm`/`episodic` 테이블에 각각 저장된다.
- Chroma 같은 벡터 DB는 아직 없다. `docs/PRD.md` MVP 제외 사항에 따라 4단계에서 필요해지면 그때 도입 여부를 결정한다.
