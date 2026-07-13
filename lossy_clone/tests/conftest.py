"""Import-collision guard.

저장소 루트에는 lossy_clone과 이름이 겹치는 원본 모듈(chatbot.py, memory/)이
이미 존재한다. lossy_clone 안의 코드가 실수로 접두사 없는 `import chatbot`이나
`import memory`를 쓰면, sys.path 구성상 lossy_clone의 것이 아니라 저장소 루트의
원본 모듈이 조용히 import될 수 있다 (ADR-001 독립성 위반). 이 훅은 그 상황을
테스트 수집 직후 감지해 즉시 실패시킨다.
"""

from pathlib import Path

_LOSSY_CLONE_DIR = Path(__file__).resolve().parent.parent
_CONFLICTING_MODULE_NAMES = ("chatbot", "memory")


def pytest_collection_modifyitems(session, config, items):
    import sys

    for name in _CONFLICTING_MODULE_NAMES:
        module = sys.modules.get(name)
        if module is None:
            continue

        module_file = getattr(module, "__file__", None)
        if module_file is None and hasattr(module, "__path__"):
            paths = list(module.__path__)
            module_file = paths[0] if paths else None
        if module_file is None:
            continue

        module_path = Path(module_file).resolve()
        if module_path != _LOSSY_CLONE_DIR and _LOSSY_CLONE_DIR not in module_path.parents:
            raise RuntimeError(
                f"'{name}' 모듈이 lossy_clone/ 밖({module_path})에서 import되었습니다. "
                f"'lossy_clone.{name}'처럼 접두사를 붙이거나 패키지 내부에서는 상대 "
                f"import를 사용하세요 (ADR-001 독립성 위반 가능성)."
            )
