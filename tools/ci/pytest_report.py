"""Opt-in pytest evidence; importing this module does not import Tapestry."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pytest

LANES = ("unit", "integration")
LAYERS = {"unit", "integration", "e2e"}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("suite evidence")
    group.addoption("--suite-report", type=Path, help="Write collection and execution JSON.")
    group.addoption("--suite-lane", choices=(*LANES, "inventory"))


def pytest_configure(config: pytest.Config) -> None:
    destination = config.getoption("suite_report")
    if destination is None:
        return
    lane = config.getoption("suite_lane")
    if lane is None:
        raise pytest.UsageError("--suite-report requires --suite-lane")
    if lane == "inventory" and not config.getoption("collectonly"):
        raise pytest.UsageError("the inventory lane requires --collect-only")
    config.pluginmanager.register(SuiteReport(config, destination, lane), "suite-evidence")


def _git(root: Path, *arguments: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _revision(root: Path) -> dict[str, Any]:
    lock = root / "uv.lock"
    return {
        "commit": _git(root, "rev-parse", "HEAD"),
        "tracked_dirty": bool(_git(root, "status", "--porcelain", "--untracked-files=no")),
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None,
    }


def _metadata(root: Path, lane: str) -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for name in (
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "pytest-randomly",
        "sqlalchemy",
        "coverage",
    ):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "source": _revision(root),
        "dependencies": {},  # Standalone SDK: no sibling application checkout.
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "packages": packages,
        # Deliberately allowlist non-secret runner metadata, never dump the environment.
        "runner": {
            name: os.environ.get(name)
            for name in (
                "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT",
                "GITHUB_JOB",
                "RUNNER_OS",
                "RUNNER_ARCH",
                "CI_UV_CACHE_HIT",
                "CI_UV_VERSION",
                "CI_INSTALL_SECONDS",
            )
        },
    }


def _case(item: pytest.Item) -> dict[str, Any]:
    markers = sorted({marker.name for marker in item.iter_markers()})
    owners = ["unit" if "isolated_unit" in markers else "integration"]
    declared = sorted(LAYERS.intersection(markers))
    layer = owners[0]
    return {
        "nodeid": item.nodeid,
        "owners": owners,
        "layer": layer,
        "layer_basis": {
            "unit": "isolated unit fixtures",
            "e2e": "live browser process",
            "integration": "real filesystem or runner components",
        }[layer],
        "declared_layers": declared,
        "markers": markers,
        "fixtures": sorted(getattr(item, "fixturenames", [])),
    }


class SuiteReport:
    def __init__(self, config: pytest.Config, destination: Path, lane: str) -> None:
        self.config = config
        self.destination = destination
        self.lane = lane
        self.started = time.perf_counter()
        self.started_at = datetime.now(UTC).isoformat()
        self.metadata = _metadata(config.rootpath, lane)
        self.collection_seconds = 0.0
        self.cases: list[dict[str, Any]] = []
        self.selected: list[str] = []
        self.deselected: list[str] = []
        self.phases: dict[str, list[dict[str, Any]]] = {}
        self.collection_errors: list[str] = []

    @pytest.hookimpl(hookwrapper=True)
    def pytest_collection(self) -> Iterator[None]:
        started = time.perf_counter()
        yield
        self.collection_seconds = time.perf_counter() - started

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> Iterator[None]:
        self.cases = [_case(item) for item in items]
        yield
        self.selected = [item.nodeid for item in items]

    def pytest_deselected(self, items: list[pytest.Item]) -> None:
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        phase: dict[str, Any] = {
            "when": report.when,
            "outcome": report.outcome,
            "seconds": report.duration,
        }
        if report.skipped:
            phase["reason"] = str(
                report.longrepr[2] if isinstance(report.longrepr, tuple) else report.longrepr
            )
        if hasattr(report, "wasxfail"):
            phase["wasxfail"] = report.wasxfail
        self.phases.setdefault(report.nodeid, []).append(phase)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        outcomes: Counter[str] = Counter()
        for phases in self.phases.values():
            if any(phase["outcome"] == "failed" for phase in phases):
                outcomes["failed"] += 1
            elif any(phase["outcome"] == "skipped" for phase in phases):
                outcomes["skipped"] += 1
            elif any(phase["when"] == "call" and phase["outcome"] == "passed" for phase in phases):
                outcomes["passed"] += 1
            else:
                outcomes["incomplete"] += 1
        payload = {
            "schema_version": 1,
            "lane": self.lane,
            "collect_only": self.config.getoption("collectonly"),
            "started_at": self.started_at,
            "elapsed_seconds": time.perf_counter() - self.started,
            "collection_seconds": self.collection_seconds,
            "exitstatus": int(exitstatus),
            "random_seed": self.config.getoption("randomly_seed", default=None),
            "metadata": self.metadata,
            "cases": sorted(self.cases, key=lambda case: case["nodeid"]),
            "selected": sorted(self.selected),
            "deselected": sorted(self.deselected),
            "phases": self.phases,
            "collection_errors": self.collection_errors,
            "outcomes": dict(outcomes),
        }
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.destination)
