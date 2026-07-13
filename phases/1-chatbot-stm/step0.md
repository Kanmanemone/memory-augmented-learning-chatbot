# Step 0: project-setup

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md`
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`

이 프로젝트(`lossy_clone`)는 저장소 루트의 원본 프로젝트(`chatbot.py`, `memory/`, `episodic_schema.py` 등)를 그대로 베끼지 않고, 개념만 참고해 `lossy_clone/` 폴더 안에 처음부터 다시 짜는 학습용 산출물이다. `lossy_clone/`는 아직 존재하지 않으며 이 step에서 최초로 생성한다. 이전 step은 없다.

이 step은 **폴더 뼈대만** 만든다. import 충돌 방지 가드, LLM 연동, STM, Chatbot은 모두 다음 step들에서 각각 따로 다룬다.

## 작업

`lossy_clone/` 폴더 뼈대를 아래와 같이 만든다 (`docs/ARCHITECTURE.md`의 디렉토리 구조를 따름):

```
lossy_clone/
├── README.md
├── requirements.txt
├── __init__.py
├── memory/
│   └── __init__.py
├── tests/
│   ├── __init__.py
│   └── test_project_setup.py
└── data/
    └── .gitkeep
```

- `lossy_clone/__init__.py`: 빈 파일. `lossy_clone`을 파이썬 패키지로 만든다.
- `lossy_clone/memory/__init__.py`: 빈 파일.
- `lossy_clone/tests/__init__.py`: 빈 파일.
- `lossy_clone/requirements.txt`: 지금 시점에 실제로 필요한 의존성만 적는다 (현재는 없으므로 헤더 주석 한 줄만 두거나 비워둔다). 이후 step에서 필요해질 때마다 줄을 추가한다.
- `lossy_clone/README.md`: 아래 섹션을 포함한 스텁 문서.
  - 프로젝트 한 줄 소개 (원본 개념만 참고한 독립 재구현이라는 점)
  - 현재 단계(1단계: Chatbot + STM)와 아직 없는 기능(LTM, Episodic — 이후 단계)
  - 실행 방법은 아직 `Chatbot` 클래스가 없으므로 "추후 채워짐"이라고만 적어둔다 (Step 5에서 실제 사용 예시로 교체될 예정).
- `lossy_clone/data/.gitkeep`: 빈 파일. SQLite DB는 실행 시 이 디렉토리 아래 생성되며, 저장소 루트 `.gitignore`의 `*.db` 패턴이 이미 커밋을 막는다. 별도 `.gitignore`를 새로 만들 필요는 없다.
- `lossy_clone/tests/test_project_setup.py`: 아래를 검증하는 테스트를 **먼저 작성**하고, 위 구조를 만들어 테스트를 통과시켜라 (TDD).
  - `import lossy_clone`이 성공한다.
  - `lossy_clone.__file__` 기준으로 `memory/`, `tests/`, `data/` 서브디렉토리가 실제로 존재한다 (`pathlib.Path(lossy_clone.__file__).parent`를 기준으로 계산 — 현재 작업 디렉토리에 의존하지 말 것).
  - `lossy_clone/requirements.txt`, `lossy_clone/README.md` 파일이 존재한다.

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ARCHITECTURE.md` 디렉토리 구조를 따르는가?
   - `ADR.md` 기술 스택(특정 LLM 벤더 SDK 비의존 등)을 벗어나지 않았는가?
   - `CLAUDE.md` CRITICAL 규칙(`lossy_clone/` 밖 파일 미참조)을 위반하지 않았는가?
3. 결과에 따라 `phases/1-chatbot-stm/index.json`의 `step 0` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 생성한 파일 목록을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 저장소 루트의 `chatbot.py`, `memory/`, `episodic_schema.py`, `data/*.json`, `README.md`(루트)를 수정하거나 import하지 마라. 이유: `lossy_clone/`은 원본과 완전히 독립적이어야 한다 (ADR-001).
- 테스트나 코드에서 `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 사용하지 마라. 이유: 저장소 루트에 이미 동일한 이름의 원본 모듈(`chatbot.py`, `memory/`)이 존재해서, 접두사 없이 import하면 의도치 않게 원본 모듈이 로드될 수 있다. 항상 `lossy_clone.` 접두사를 붙이거나(`import lossy_clone`, `from lossy_clone import ...`) 패키지 내부에서는 상대 import를 사용하라. (자동 검증 가드는 다음 step에서 추가된다 — 이 step에서는 직접 조심할 것.)
- `conftest.py`나 import 충돌 가드를 이 step에서 만들지 마라. 별도 step(`import-guard`)에서 다룬다.
- `Chatbot` 클래스, STM, LLM 연동 등 이후 단계의 기능을 앞당겨 구현하지 마라. 이 step은 폴더 뼈대와 스모크 테스트만 다룬다 (ADR-003).
- `.env` 파일을 만들거나 수정하지 마라. 이미 저장소 루트에 존재하며 이 step과 무관하다.
- 기존 테스트를 깨뜨리지 마라.
