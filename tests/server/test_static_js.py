from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

import auris.server

STATIC = pathlib.Path(auris.server.__file__).resolve().parent / "static"
REPO = pathlib.Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed; JS checks skipped")


def test_static_is_declared_an_es_module_package():
    assert (STATIC / "package.json").read_text().strip() == '{"type": "module"}'


@pytest.mark.parametrize("module", sorted(p.name for p in (STATIC / "js").glob("*.js")))
def test_module_parses(module):
    subprocess.run([NODE, "--check", str(STATIC / "js" / module)], check=True)


def test_js_unit_tests_pass():
    tests = REPO / "tests" / "js"
    if not tests.is_dir() or not any(tests.glob("*.test.mjs")):
        pytest.skip("no JS unit tests yet")  # task 5 adds the first one
    # `node --test <directory>` fails to auto-discover files on this node build (reproduced
    # outside the repo too: MODULE_NOT_FOUND, as if the directory were passed to require()).
    # A glob pattern is interpreted by node's own test-runner matcher instead, and works.
    subprocess.run([NODE, "--test", str(tests / "**" / "*.test.mjs")], check=True, cwd=REPO)
