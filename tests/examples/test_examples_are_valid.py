from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.mark.parametrize("path", sorted(_EXAMPLES.glob("*.py")), ids=lambda p: p.name)
def test_example_compiles(path):
    py_compile.compile(str(path), doraise=True)


def test_examples_exist():
    names = {p.name for p in _EXAMPLES.glob("*")}
    assert {
        "python_quickstart.py",
        "clone_and_speak.py",
        "openai_sdk.py",
        "curl.sh",
        "README.md",
    } <= names
