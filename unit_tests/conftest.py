from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType

import pytest

FORBIDDEN_IMPORTS = (
    "sqlalchemy",
    "fastapi",
    "tapestry.app.main",
    "tapestry.app.models",
    "tapestry.app.core.config",
    "tapestry.app.core.database",
    "tapestry.app.core.security",
)
_unit_active = False


class _UnitImports(MetaPathFinder):
    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if any(
            fullname == prefix or fullname.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS
        ):
            raise RuntimeError(
                f"Isolated unit tests cannot import {fullname}; use integration fixtures."
            )
        return None


_import_guard = _UnitImports()


def _audit_unit_activity(event: str, _arguments: tuple[object, ...]) -> None:
    if _unit_active and event in {
        "socket.connect",
        "socket.bind",
        "socket.getaddrinfo",
        "socket.sendto",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.fork",
    }:
        raise RuntimeError(f"Isolated unit tests cannot perform {event}; use integration fixtures.")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--unit-isolation", action="store_true", help="Enforce the pure unit boundary."
    )


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("unit_isolation", default=False):
        return
    loaded = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS)
    )
    if loaded:
        raise pytest.UsageError(
            f"Unit runner already imported application/database modules: {loaded}"
        )
    sys.meta_path.insert(0, _import_guard)
    sys.addaudithook(_audit_unit_activity)


def pytest_unconfigure() -> None:
    if _import_guard in sys.meta_path:
        sys.meta_path.remove(_import_guard)


@pytest.fixture(autouse=True)
def unit_boundary(request: pytest.FixtureRequest) -> Iterator[None]:
    global _unit_active
    if not request.config.getoption("unit_isolation", default=False):
        pytest.fail("Run unit_tests with pytest-unit.ini and disabled plugin autoload.")
    _unit_active = True
    try:
        yield
    finally:
        _unit_active = False
