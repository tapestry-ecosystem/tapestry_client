"""Check current full-lane ownership without reducing the tests CI selects."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

LANES = ("unit", "integration")


def _identity(case: dict[str, Any]) -> dict[str, Any]:
    # A dedicated unit process has different runner fixtures/plugins than full
    # inventory collection. Keep identity, markers, ownership, and layer strict.
    return {key: value for key, value in case.items() if key != "fixtures"}


def audit(inventory: dict[str, Any], reports: list[dict[str, Any]]) -> list[str]:
    errors = []
    if not inventory["metadata"]["source"]["commit"]:
        errors.append("the source revision is missing")
    for name, dependency in inventory["metadata"]["dependencies"].items():
        if not dependency["commit"]:
            errors.append(f"the {name} dependency revision is missing")
    cases = {case["nodeid"]: case for case in inventory["cases"]}
    if not cases or len(cases) != len(inventory["cases"]):
        errors.append("inventory must contain nonempty, unique node IDs")
    if (
        inventory["lane"] != "inventory"
        or not inventory["collect_only"]
        or inventory["exitstatus"] != 0
    ):
        errors.append("the independent full collection did not finish successfully")
    if set(inventory["selected"]) != set(cases) or inventory["deselected"]:
        errors.append("the inventory must collect every case without selection")
    for case in cases.values():
        if len(case["owners"]) != 1 or case["owners"][0] not in LANES:
            errors.append(f"ambiguous lane ownership: {case['nodeid']}")
        if len(case["declared_layers"]) > 1:
            errors.append(f"multiple primary layer markers: {case['nodeid']}")
    lanes = [report["lane"] for report in reports]
    if sorted(lanes) != sorted(LANES):
        errors.append(f"expected exactly one report for each of {LANES}; received {lanes}")
    for report in [inventory, *reports]:
        if report["schema_version"] != 1 or report["collection_errors"]:
            errors.append(f"invalid or incomplete collection: {report['lane']}")
        if report["metadata"]["source"] != inventory["metadata"]["source"]:
            errors.append(f"{report['lane']}: source revisions differ from the inventory")
        dependencies = {} if report["lane"] == "unit" else inventory["metadata"]["dependencies"]
        if report["metadata"]["dependencies"] != dependencies:
            errors.append(f"{report['lane']}: dependency revisions differ from the inventory")
    for report in reports:
        lane = report["lane"]
        if report["collect_only"] or report["exitstatus"] != 0:
            errors.append(f"{lane}: execution did not complete successfully")
        expected = {nodeid for nodeid, case in cases.items() if case["owners"] == [lane]}
        observed = {case["nodeid"]: _identity(case) for case in report["cases"]}
        # Unit collection must not import the integration suite. Other lanes
        # still collect the full inventory and then use the existing selectors.
        expected_collection = expected if lane == "unit" else set(cases)
        if observed != {nodeid: _identity(cases[nodeid]) for nodeid in expected_collection}:
            errors.append(f"{lane}: collected inventory or classification changed")
        if len(observed) != len(report["cases"]):
            errors.append(f"{lane}: duplicate collected node IDs")
        if (
            not expected
            or set(report["selected"]) != expected
            or len(report["selected"]) != len(expected)
        ):
            errors.append(
                f"{lane}: selection is empty, duplicated, missing, or includes another lane"
            )
        if set(report["phases"]) != expected:
            errors.append(f"{lane}: missing or unexpected execution results")
        for nodeid, phases in report["phases"].items():
            if [phase["when"] for phase in phases] != ["setup", "call", "teardown"]:
                errors.append(f"{lane}: incomplete or repeated execution: {nodeid}")
            elif any(phase["outcome"] != "passed" or "wasxfail" in phase for phase in phases):
                errors.append(f"{lane}: failed, skipped, or expected-failure case: {nodeid}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("reports", type=Path, nargs="+")
    arguments = parser.parse_args()
    try:
        inventory = json.loads(arguments.inventory.read_text(encoding="utf-8"))
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in arguments.reports]
        errors = audit(inventory, reports)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.stderr.write(f"Cannot validate test evidence: {exc}\n")
        return 1
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    sys.stdout.write(
        f"Validated {len(inventory['cases'])} cases exactly once across {', '.join(LANES)}.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
