"""Per-category pass rate at the end of a decision-eval run."""

from __future__ import annotations

from collections import defaultdict

_results: dict[str, list[str]] = defaultdict(list)


def pytest_runtest_logreport(report):
    category = dict(report.user_properties).get("category")
    if category is None:
        return
    if report.when == "call":
        _results[category].append("xfail" if hasattr(report, "wasxfail") else report.outcome)
    elif report.when == "setup" and report.skipped:
        _results[category].append("skipped")


def pytest_terminal_summary(terminalreporter):
    if not _results:
        return
    tr = terminalreporter
    tr.section("decision evals by category")
    total_passed = total_run = 0
    for category in sorted(_results):
        outcomes = _results[category]
        run = [o for o in outcomes if o in ("passed", "failed")]
        passed = run.count("passed")
        total_passed += passed
        total_run += len(run)
        extra = []
        if outcomes.count("xfail"):
            extra.append(f"{outcomes.count('xfail')} known gaps")
        if outcomes.count("skipped"):
            extra.append(f"{outcomes.count('skipped')} skipped")
        suffix = f"  ({', '.join(extra)})" if extra else ""
        tr.write_line(f"  {category:<16} {passed}/{len(run)}{suffix}")
    if total_run:
        tr.write_line(f"  {'overall':<16} {total_passed}/{total_run} ({100 * total_passed // total_run}%)")
