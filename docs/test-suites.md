# SDK test boundaries

Part of [#2](https://github.com/tapestry-ecosystem/tapestry_client/issues/2) and
the [ecosystem test plan](https://github.com/tapestry-ecosystem/tapestry/issues/319).

The SDK installs independently with `uv sync --frozen --extra dev`; no Tapestry,
companion, database, or design-system checkout is required. No runtime or
dependency-version change accompanies this pilot.

- `unit_tests/` owns HTTP serialization, response schemas, webhook verification,
  and transport error behavior. HTTPX `MockTransport` supplies external
  responses; the real SDK constructs each request and validates each response.
  The unit runner rejects application/database imports and socket/process
  operations. Its guards also detect forbidden modules loaded by a plugin.
- `tests/integration/` owns real temporary credential files, private permissions,
  serialized renewal, restart/readback, failed persistence, and shutdown. The
  existing renewal concurrency check uses multiple clients in one process; it
  does not prove separate OS worker behavior. Test-runner/guard regressions
  also belong here because they intentionally launch isolated pytest processes.
- Real SDK/platform API and authorization conformance remains in Core's
  `tests/test_tapestry_client/` and Expenses' two-database contract journeys.
  SDK transport doubles are not a replacement for those tests.

All 26 original cases retain their test bodies/assertions. Eight new purchase
transport cases check failed/ambiguous submission without automatic POST replay
and concurrent callers retaining their own delegation headers. Seven tooling
regressions check inventory failures and unit boundary enforcement. The pilot
therefore has 27 unit and 14 integration cases.

## Run both layers

After the locked install, run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --frozen pytest -c pytest-unit.ini \
  -p pytest_asyncio.plugin -p pytest_cov.plugin --cov=tapestry_client \
  --cov-config=pyproject.toml --cov-report=
uv run --frozen pytest --cov-append
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tests unit_tests tools
```

Default pytest now selects integration. The unit runner has a separate config
and disabled plugin autoload; invoking unit files through the default runner
fails explicitly instead of quietly running without isolation.

CI keeps the existing required job context. It collects an independent full
inventory, runs each layer once, and checks their exact disjoint union. A
missing, failed, skipped, cancelled, duplicated, or incomplete lane cannot pass
the audit. The evidence implementation is adapted from the validated Core
pilot; regression tests cover its SDK-specific two-lane ownership.

Artifacts include source/lock/interpreter/tool versions, cache/install metadata,
collection and setup/call/teardown timings, skip reasons, JUnit, raw coverage,
and combined branch/line coverage. Coverage is appended only within the same
job, source tree, and dependency environment. The prior workflow collected no
coverage and enforced no numeric floor; this change makes coverage visible
without inventing a per-partial-lane threshold. Artifacts expire after 14 days.

The untouched baseline passed 26 cases in 0.88 seconds locally. This pilot adds
failure and tooling checks and makes no speedup claim for an already-small SDK
suite. CI must pass before maintainer review. Rollback is a PR restoring the
original paths and single full selection; runtime behavior is unchanged.
