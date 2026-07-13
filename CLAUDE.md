# 프로젝트: lossy_clone

## 기술 스택
- Python 3
- 로컬 SQLite 기반 저장소 (`lossy_clone/data/`)
- LLM 연동은 벤더에 고정하지 않고 교체 가능한 인터페이스로 분리 (특정 SDK에 직접 의존 금지)

## 아키텍처 규칙
- CRITICAL: `lossy_clone/` 폴더 밖의 어떤 파일도 import/참조하지 않는다. 이 폴더만 잘라내도 독립적으로 동작해야 한다 (docs/ADR.md ADR-001).
- CRITICAL: 특정 LLM 벤더 SDK(google-genai 등)에 직접 의존하지 않고, 교체 가능한 연동 지점으로 분리한다 (docs/ADR.md ADR-004).
- 기능은 `docs/PRD.md`의 단계별 로드맵 순서를 따른다. 이후 단계의 기능(LTM, Episodic 등)을 앞당겨 구현하지 않는다.

## 개발 프로세스
- CRITICAL: 새 기능 구현 시 반드시 테스트를 먼저 작성하고, 테스트가 통과하는 구현을 작성할 것 (TDD)
- 커밋 메시지는 conventional commits 형식을 따를 것 (feat:, fix:, docs:, refactor:)

## 명령어
pip install -r requirements.txt   # 의존성 설치
python -m compileall -q .         # 구문 오류 검사
python -m pytest                  # 테스트
