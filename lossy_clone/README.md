# lossy_clone

`memory-augmented-learning-chatbot`(원본)의 "3계층 메모리(STM/LTM/Episodic) 학습 챗봇" 개념만 참고해서, 코드를 베끼지 않고 처음부터 다시 짠 독립 실행형 챗봇입니다. 이 폴더 하나만 잘라내도 그대로 동작합니다 (원본의 다른 파일을 import하지 않습니다).

## 현재 단계

**1단계: Chatbot 인스턴스 + STM** — 세션 내 최근 대화를 SQLite 기반 STM에 저장/조회하며, 실제로 호출되는 LLM 연동(Gemini REST, 벤더 SDK 비의존)으로 응답을 생성합니다.

아직 없는 기능 (이후 단계에서 추가 예정):

- LTM (장기 기억, 세션 요약 전이) — 2단계
- Episodic memory (주제별 학습 이력 누적) — 3단계
- LTM/Episodic 검색, 반복 질문 감지 등 세부 동작 — 4단계

## 실행 방법

추후 채워짐 (`Chatbot` 클래스가 아직 없습니다).
