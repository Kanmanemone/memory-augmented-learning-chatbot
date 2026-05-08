"""Explicit demo memory fixture for the hands-on learning chatbot.

The fixture describes the supplied demo database content in one stable Python
contract so tests, scripts, and demo documentation can refer to the same
persona, questions, and expected memory sources.
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_raw_stm_conversations() -> list[dict[str, Any]]:
    fixture_path = _PROJECT_ROOT / "data" / "demo_memory_stm.json"
    with fixture_path.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)["stm_conversations"]


DEMO_LEARNER_PERSONA = (
    "파이썬을 처음 배우는 초급 학습자. 데코레이터와 재귀를 배운 적이 있지만 "
    "짧거나 모호한 질문으로 직전 맥락을 이어 묻는 경우가 많다."
)

DEMO_STM_CONTEXT: dict[str, Any] = {
    "session_id": "demo-current-session",
    "recent_topic": "programming:python-decorators",
    "previous_user_message": "데코레이터에서 wrapper 함수가 정확히 뭐야?",
    "previous_assistant_focus": (
        "decorator는 함수를 감싸는 wrapper 함수를 반환하고, 호출 시 wrapper가 "
        "먼저 실행된 뒤 원래 함수가 실행된다는 맥락을 설명했다."
    ),
}

DEMO_LTM_SUMMARY: dict[str, Any] = {
    "session_id": "demo-prior-python-session",
    "summary": (
        "지난 학습에서 Python decorators와 recursion을 다뤘고, 학습자는 "
        "wrapper 구조, 실행 순서, base case, 호출 스택에서 어려움을 보였다."
    ),
    "struggles": [
        "decorator wrapper 구조를 원래 함수와 분리해서 이해하기",
        "decorator 실행 순서와 @wraps가 필요한 이유 구분하기",
        "recursion base case를 고르고 호출 스택 흐름 추적하기",
    ],
    "strengths": [
        "작은 코드 예제를 보면 질문을 구체화한다",
        "이전 설명과 새 개념을 연결하려고 시도한다",
    ],
    "confusions": [
        "wrapper 함수가 원래 함수를 감싼다는 표현",
        "재귀가 언제 멈추는지와 호출 스택이 되돌아오는 순서",
    ],
    "topic_tags": [
        "programming:python-decorators",
        "programming:recursion",
    ],
}

DEMO_SQL_JOIN_STM_CONTEXT: dict[str, Any] = {
    "session_id": "demo-sql-join-session",
    "recent_topic": "web_app:sql-join",
    "previous_user_message": "LEFT JOIN이랑 INNER JOIN은 뭐가 달라?",
    "previous_assistant_focus": (
        "SQLite에서 JOIN은 여러 테이블의 관련 행을 연결해 읽는 방법이며, "
        "INNER JOIN은 양쪽 테이블에 매칭되는 행만 남기고 LEFT JOIN은 왼쪽 "
        "테이블의 모든 행을 유지한다는 차이를 설명했다."
    ),
}

DEMO_STM_CONVERSATIONS: list[dict[str, Any]] = _load_raw_stm_conversations()

DEMO_SQL_JOIN_LTM_SUMMARY: dict[str, Any] = {
    "id": "demo-ltm-sql-join-history",
    "session_id": "demo-sql-join-session",
    "summary": (
        "SQLite와 SQL JOIN 학습에서 학습자는 users 테이블과 orders 테이블을 "
        "연결해 읽는 예제를 연습했고, INNER JOIN과 LEFT JOIN의 결과 행 차이를 "
        "헷갈려했다."
    ),
    "struggles": [
        "INNER JOIN은 양쪽 테이블에 매칭되는 행만 보여준다는 점 이해하기",
        "LEFT JOIN은 왼쪽 테이블의 행을 유지하고 매칭이 없으면 NULL이 나온다는 점 이해하기",
        "ON 조건으로 두 테이블의 키를 연결하는 흐름 추적하기",
    ],
    "strengths": [
        "users와 orders처럼 구체적인 테이블 예시가 있으면 행 결합을 추론한다",
    ],
    "confusions": [
        "JOIN을 왜 쓰는지와 WHERE로 필터링하는 것의 차이",
        "LEFT JOIN 결과에서 주문이 없는 사용자 행이 남는 이유",
    ],
    "topic_tags": ["web_app:sql-join", "web_app:sqlite-schema"],
}

DEMO_EPISODIC_MEMORIES: list[dict[str, Any]] = [
    {
        "episodic_id": "demo-episode-python-decorators",
        "memory_type": "episodic",
        "memory_item_type": "learning_event",
        "topic": "programming:python-decorators",
        "topic_tags": ["programming:python-decorators"],
        "strengths": [
            "간단한 함수 예제를 기반으로 설명하면 잘 따라온다",
        ],
        "weaknesses": [
            "wrapper 함수가 원래 함수를 감싼다는 구조를 헷갈려한다",
            "decorator 적용 시점과 함수 호출 시 실행 순서를 혼동한다",
            "@wraps가 원래 함수 메타데이터를 보존한다는 점을 자주 놓친다",
        ],
        "questions": [
            "데코레이터에서 wrapper 함수가 정확히 뭐야?",
            "왜 @wraps를 붙여야 해?",
            "방금 얘기하던걸 조금 더 자세하게 다시 설명해줘.",
        ],
    },
    {
        "episodic_id": "demo-episode-recursion",
        "memory_type": "episodic",
        "memory_item_type": "learning_event",
        "topic": "programming:recursion",
        "topic_tags": ["programming:recursion"],
        "strengths": [
            "팩토리얼처럼 작은 예제에서는 반복 흐름을 설명할 수 있다",
        ],
        "weaknesses": [
            "base case를 빠뜨리거나 너무 늦게 배치한다",
            "호출 스택이 쌓였다가 되돌아오는 순서를 어려워한다",
        ],
        "questions": [
            "재귀는 언제 멈춰?",
            "base case가 없으면 왜 문제가 돼?",
        ],
    },
    {
        "episodic_id": "demo-episode-sql-join",
        "memory_type": "episodic",
        "memory_item_type": "learning_event",
        "topic": "web_app:sql-join",
        "topic_tags": ["web_app:sql-join", "web_app:sqlite-schema"],
        "strengths": [
            "테이블 예시를 보면 행이 어떻게 합쳐지는지 추론한다",
        ],
        "weaknesses": [
            "INNER JOIN과 LEFT JOIN의 결과 행 차이를 헷갈려한다",
            "LEFT JOIN에서 매칭되지 않은 오른쪽 테이블 값이 NULL로 남는 이유를 어려워한다",
            "ON 조건이 두 테이블의 키를 연결한다는 점을 놓친다",
        ],
        "questions": [
            "SQLite에서 JOIN은 왜 쓰는 거야?",
            "LEFT JOIN이랑 INNER JOIN은 뭐가 달라?",
        ],
    },
    {
        "episodic_id": "demo-conversation-python-decorators",
        "memory_type": "episodic",
        "memory_item_type": "conversation_episode",
        "topic": "general:python-decorators-conversation",
        "topic_tags": ["programming:python-decorators", "general:conversation"],
        "strengths": [
            "직전 설명을 이어서 다시 묻는 방식으로 모르는 지점을 좁힌다",
        ],
        "weaknesses": [
            "짧은 후속 질문만으로는 wrapper와 원래 함수의 관계가 다시 흐려진다",
        ],
        "questions": [
            "방금 얘기하던걸 조금 더 자세하게 다시 설명해줘.",
        ],
    },
    {
        "episodic_id": "demo-conversation-python-modules-prior-reference",
        "memory_type": "episodic",
        "memory_item_type": "conversation_episode",
        "topic": "general:python-modules-prior-memory-reference",
        "topic_tags": [
            "programming:python-modules",
            "programming:python-functions",
            "programming:python-exceptions",
            "general:memory-reference",
        ],
        "strengths": [
            "전날 배운 반복문, 조건문, 함수, 예외처리를 새 모듈 학습과 연결해 질문한다",
        ],
        "weaknesses": [
            "return과 print의 역할 차이를 모듈 파일로 나눴을 때 다시 확인해야 한다",
            "ValueError처럼 구체적인 예외를 잡는 이유를 이전 계산기 예제와 연결해 복습한다",
        ],
        "questions": [
            (
                "어제 반복문/조건문이랑 함수/예외처리를 배웠는데, 오늘 모듈은 "
                "그 함수들을 .py 파일로 나눠 쓰는 거야? 어제 저녁에 return과 "
                "print를 헷갈리고 ValueError를 따로 잡는 이유를 물어봤던 "
                "계산기 코드도 calculator.py 모듈로 만들 수 있어?"
            ),
        ],
        "source_session_ids": ["demo-stm-20260502-modules"],
        "source_message_ids": ["demo-stm-20260502-modules-000"],
        "source_turn_indices": [0],
        "source_message_timestamps": ["2026-05-02T13:05:00+09:00"],
        "topic_embedding": [0.0, 1.0, 1.0],
        "last_updated": "2026-05-03T03:00:00+09:00",
    },
]

DEMO_QUESTIONS = [
    "방금 얘기하던걸 조금 더 자세하게 다시 설명해줘.",
    "저번에 내가 뭘 어려워했었지?",
    "이게 뭐야?",
]

EXPECTED_MEMORY_SOURCES = {
    DEMO_QUESTIONS[0]: ["STM", "Episodic"],
    DEMO_QUESTIONS[1]: ["LTM", "Episodic"],
    DEMO_QUESTIONS[2]: ["STM", "LTM", "Episodic"],
}

DEMO_DATABASE_PATHS = {
    "sqlite_candidates": ["data/demo.sqlite3", "data/chatbot.db"],
    "chroma": "data/chroma",
}

DEMO_MEMORY_FIXTURE: dict[str, Any] = {
    "learner_persona": DEMO_LEARNER_PERSONA,
    "stm_conversations": DEMO_STM_CONVERSATIONS,
    "stm_context": DEMO_STM_CONTEXT,
    "sql_join_stm_context": DEMO_SQL_JOIN_STM_CONTEXT,
    "ltm_summary": DEMO_LTM_SUMMARY,
    "sql_join_ltm_summary": DEMO_SQL_JOIN_LTM_SUMMARY,
    "episodic_memories": DEMO_EPISODIC_MEMORIES,
    "demo_questions": DEMO_QUESTIONS,
    "expected_memory_sources": EXPECTED_MEMORY_SOURCES,
    "demo_database_paths": DEMO_DATABASE_PATHS,
    "conversion_trigger": False,
}

__all__ = [
    "DEMO_DATABASE_PATHS",
    "DEMO_EPISODIC_MEMORIES",
    "DEMO_LEARNER_PERSONA",
    "DEMO_LTM_SUMMARY",
    "DEMO_MEMORY_FIXTURE",
    "DEMO_QUESTIONS",
    "DEMO_SQL_JOIN_LTM_SUMMARY",
    "DEMO_SQL_JOIN_STM_CONTEXT",
    "DEMO_STM_CONVERSATIONS",
    "DEMO_STM_CONTEXT",
    "EXPECTED_MEMORY_SOURCES",
]
