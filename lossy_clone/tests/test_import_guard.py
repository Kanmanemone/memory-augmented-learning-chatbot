import sys
import types
from pathlib import Path

import pytest

from lossy_clone.tests.conftest import pytest_collection_modifyitems

LOSSY_CLONE_DIR = Path(__file__).resolve().parent.parent


def _fake_module(file_path: str) -> types.ModuleType:
    module = types.ModuleType("fake")
    module.__file__ = file_path
    return module


@pytest.fixture
def restore_sys_modules():
    saved = {}
    for name in ("chatbot", "memory"):
        if name in sys.modules:
            saved[name] = sys.modules[name]
    try:
        yield
    finally:
        for name in ("chatbot", "memory"):
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_guard_raises_when_chatbot_module_is_outside_lossy_clone(restore_sys_modules):
    outside_path = str(LOSSY_CLONE_DIR.parent / "chatbot.py")
    sys.modules["chatbot"] = _fake_module(outside_path)

    with pytest.raises(RuntimeError):
        pytest_collection_modifyitems(session=None, config=None, items=[])


def test_guard_raises_when_memory_module_is_outside_lossy_clone(restore_sys_modules):
    outside_path = str(LOSSY_CLONE_DIR.parent / "memory" / "__init__.py")
    sys.modules["memory"] = _fake_module(outside_path)

    with pytest.raises(RuntimeError):
        pytest_collection_modifyitems(session=None, config=None, items=[])


def test_guard_allows_chatbot_module_inside_lossy_clone(restore_sys_modules):
    inside_path = str(LOSSY_CLONE_DIR / "chatbot.py")
    sys.modules["chatbot"] = _fake_module(inside_path)

    pytest_collection_modifyitems(session=None, config=None, items=[])


def test_guard_noop_when_no_conflicting_module_imported(restore_sys_modules):
    sys.modules.pop("chatbot", None)
    sys.modules.pop("memory", None)

    pytest_collection_modifyitems(session=None, config=None, items=[])
