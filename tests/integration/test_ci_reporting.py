from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from tools.ci.audit_test_reports import audit

pytestmark = pytest.mark.integration
PLUGIN = Path(__file__).resolve().parents[2] / "tools/ci/pytest_report.py"


@pytest.fixture
def evidence_project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    # The nested project must not load the application's installed autouse plugin.
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    pytester.makepyfile(pytest_report=PLUGIN.read_text(encoding="utf-8"))
    pytester.makeini(
        "[pytest]\nmarkers =\n    postgres\n    browser\n    unit\n"
        "    isolated_unit\n    integration\n    e2e\n"
    )
    pytester.makepyfile(
        test_unit="""
        import pytest
        pytestmark = [pytest.mark.unit, pytest.mark.isolated_unit]

        def test_pure():
            assert 1 + 1 == 2
        """
    )
    pytester.makepyfile(
        test_sample="""
        import pytest

        @pytest.mark.parametrize('value', [1, 2], ids=['one', 'two'])
        def test_sqlite(value):
            assert value > 0

        @pytest.mark.postgres
        def test_postgres():
            pass

        @pytest.mark.browser
        def test_browser():
            pass
        """
    )
    return pytester


def _run(project: pytest.Pytester, lane: str, *arguments: str) -> dict[str, Any]:
    destination = project.path / f"{lane}.json"
    project.runpytest_subprocess(
        "-p",
        "pytest_report",
        "--suite-lane",
        lane,
        "--suite-report",
        str(destination),
        *arguments,
        timeout=60,
    )
    return json.loads(destination.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_evidence_retains_deselected_parameters_and_phase_durations(
    evidence_project: pytest.Pytester,
) -> None:
    inventory = _run(evidence_project, "inventory", "--collect-only")
    reports = [
        _run(evidence_project, lane, "-m", expression)
        for lane, expression in (("integration", "not isolated_unit"),)
    ]
    reports.append(_run(evidence_project, "unit", "test_unit.py"))
    # pytester's synthetic project is deliberately outside a Git checkout.
    assert audit(inventory, reports) == ["the source revision is missing"]
    for report in [inventory, *reports]:
        report["metadata"]["source"]["commit"] = "synthetic-fixture-revision"
    assert audit(inventory, reports) == []
    assert len(inventory["cases"]) == 5
    assert len(reports[0]["selected"]) == 4
    assert len(reports[0]["deselected"]) == 1
    assert {case["layer"] for case in inventory["cases"]} == {"unit", "integration"}
    assert any(case["nodeid"].endswith("[two]") for case in inventory["cases"])
    for report in reports:
        assert report["elapsed_seconds"] >= report["collection_seconds"] >= 0
        assert all(
            phase["seconds"] >= 0 for phases in report["phases"].values() for phase in phases
        )

    # Missing/cancelled reports, altered selection, duplicate execution, skips,
    # and incompatible source revisions must never validate as full coverage.
    assert audit(inventory, reports[:-1])
    assert audit(inventory, [*reports, reports[0]])
    for change in (
        "revision",
        "selection",
        "skip",
        "teardown",
        "repeat",
        "collect_only",
        "exitstatus",
    ):
        changed = copy.deepcopy(reports)
        first = changed[0]
        nodeid = first["selected"][0]
        if change == "revision":
            first["metadata"]["source"]["commit"] = "different"
        elif change == "selection":
            first["selected"].pop()
        elif change == "skip":
            first["phases"][nodeid][1]["outcome"] = "skipped"
        elif change == "teardown":
            first["phases"][nodeid].pop()
        elif change == "repeat":
            first["phases"][nodeid].extend(copy.deepcopy(first["phases"][nodeid]))
        elif change == "collect_only":
            first["collect_only"] = True
        else:
            first["exitstatus"] = 1
        assert audit(inventory, changed), change


def test_evidence_records_setup_skip_and_teardown_failure(
    evidence_project: pytest.Pytester,
) -> None:
    evidence_project.makepyfile(
        test_failures="""
        import pytest

        @pytest.fixture
        def missing_prerequisite():
            pytest.skip('synthetic prerequisite unavailable')

        @pytest.fixture
        def broken_cleanup():
            yield
            raise RuntimeError('synthetic cleanup failure')

        def test_skipped(missing_prerequisite):
            pass

        def test_teardown(broken_cleanup):
            pass
        """
    )
    report = _run(evidence_project, "integration", "test_failures.py")
    assert report["exitstatus"] == 1
    assert report["outcomes"] == {"skipped": 1, "failed": 1}
    skipped = report["phases"]["test_failures.py::test_skipped"]
    assert [phase["when"] for phase in skipped] == ["setup", "teardown"]
    assert "synthetic prerequisite unavailable" in skipped[0]["reason"]
    failed = report["phases"]["test_failures.py::test_teardown"]
    assert failed[-1]["when"] == "teardown"
    assert failed[-1]["outcome"] == "failed"


def test_evidence_preserves_collection_errors(evidence_project: pytest.Pytester) -> None:
    evidence_project.makepyfile(test_broken="raise RuntimeError('synthetic collection failure')")
    report = _run(evidence_project, "inventory", "--collect-only")
    assert report["exitstatus"] != 0
    assert report["collection_errors"] == ["test_broken.py"]
