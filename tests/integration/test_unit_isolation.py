from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
UNIT_FIXTURES = Path(__file__).resolve().parents[2] / "unit_tests/conftest.py"


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("import sqlalchemy", "cannot import sqlalchemy"),
        ("socket.create_connection(('127.0.0.1', 1))", "cannot perform socket."),
        (
            "subprocess.run([sys.executable, '-c', 'pass'], check=True)",
            "cannot perform subprocess.Popen",
        ),
    ],
    ids=["database-import", "network", "subprocess"],
)
def test_unit_guard_rejects_side_effects_and_cleans_up_after_failure(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    message: str,
) -> None:
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    pytester.makeconftest(
        UNIT_FIXTURES.read_text(encoding="utf-8")
        + """

def pytest_sessionfinish():
    # Runner bookkeeping is permitted again after the failing test tears down.
    import subprocess
    subprocess.run([sys.executable, '-c', 'pass'], check=True)
"""
    )
    pytester.makepyfile(
        test_boundary=f"""
        import socket
        import subprocess
        import sys

        def test_boundary():
            {operation}
        """
    )
    result = pytester.runpytest_subprocess("--unit-isolation", "-q", timeout=60)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([f"*Isolated unit tests {message}*"])


def test_unit_guard_rejects_an_already_loaded_database_plugin(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    pytester.makeconftest(
        UNIT_FIXTURES.read_text(encoding="utf-8")
        + '\nfrom types import ModuleType\nsys.modules["sqlalchemy"] = ModuleType("sqlalchemy")\n'
    )
    pytester.makepyfile("def test_placeholder(): pass")
    result = pytester.runpytest_subprocess("--unit-isolation", "-q", timeout=60)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["*Unit runner already imported application/database modules*sqlalchemy*"]
    )
