"""Takshak pipeline metrics -- the evidence layer behind the review dashboard.

    run.sh tools/metrics.py record --project DIR --event NAME --data '{"k": "v", ...}'
    run.sh tools/metrics.py summary --project DIR [--json]

Events are appended to .claude/metrics.jsonl (one JSON object per line, capped like
budget_history.jsonl). Two events are recorded directly by python hooks (quality_gate.py
on every gate run, prompt_submit.py on approval) since those already run in-process.
The other two -- adversary_pass (design) and ship (ship) -- happen entirely inside a
skill's own multi-step instructions, so the skill shells out to `record` explicitly,
the same way it already writes checkpoint.json and task_state.json as explicit steps.

`summary` computes the rollups the dashboard renders: adversary catch rate (risks found
and mitigated per pass, convergence rate), quality-gate hit rate (findings per edit,
recurring codes), approval latency (breakdown -> approved), and ship pass/fail rate.
Never raises: a broken or missing metrics.jsonl yields an empty summary, not a crash.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

from _common import Project, read_metrics, record_metric  # noqa: E402


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 1) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gate = [r for r in rows if r.get("event") == "quality_gate"]
    approvals = [r for r in rows if r.get("event") == "approval"]
    adversary = [r for r in rows if r.get("event") == "adversary_pass"]
    ships = [r for r in rows if r.get("event") == "ship"]

    gate_summary = {
        "runs": len(gate),
        "clean_runs": sum(1 for r in gate if r.get("clean")),
        "hit_rate_pct": round(100 * (1 - sum(1 for r in gate if r.get("clean")) / len(gate)), 1) if gate else None,
        "avg_lint_findings": _mean([float(r.get("lint", 0)) for r in gate]),
        "avg_type_findings": _mean([float(r.get("types", 0)) for r in gate]),
    }

    latencies = [float(r["latency_seconds"]) for r in approvals if r.get("latency_seconds") is not None]
    approval_summary = {
        "count": len(approvals),
        "avg_latency_seconds": _mean(latencies),
        "median_latency_seconds": _median(latencies),
    }

    # One adversary_pass event is meant to be the FINAL state of one design session (the
    # design skill records it once, after its converge-or-cap-at-3 loop ends). But a re-run
    # of /takshak:design on the same SDD (after amendments, say) legitimately produces a
    # second event for the same sdd_path -- dedupe by keeping the latest per path so a
    # feature is counted once, by its most recent outcome, not once per recording.
    latest_by_sdd: dict[str, dict[str, Any]] = {}
    for r in adversary:
        key = str(r.get("sdd_path") or id(r))
        latest_by_sdd[key] = r  # rows arrive in file order, so the last write wins
    features = list(latest_by_sdd.values())
    converged = [r for r in features if r.get("converged")]
    adversary_summary = {
        "features": len(features),
        "converged": len(converged),
        "convergence_rate_pct": round(100 * len(converged) / len(features), 1) if features else None,
        "avg_passes": _mean([float(r.get("passes", 0)) for r in features]),
        "avg_critical_found": _mean([float(r.get("risks_critical", 0)) for r in features]),
        "avg_high_found": _mean([float(r.get("risks_high", 0)) for r in features]),
    }

    ship_pass = [r for r in ships if r.get("result") == "pass"]
    ship_fail = [r for r in ships if r.get("result") == "fail"]
    failed_gates: dict[str, int] = {}
    for r in ship_fail:
        gate_name = str(r.get("gate_failed") or "unknown")
        failed_gates[gate_name] = failed_gates.get(gate_name, 0) + 1
    ship_summary = {
        "attempts": len(ships),
        "passed": len(ship_pass),
        "failed": len(ship_fail),
        "pass_rate_pct": round(100 * len(ship_pass) / len(ships), 1) if ships else None,
        "failed_gates": failed_gates,
    }

    return {
        "quality_gate": gate_summary,
        "approval": approval_summary,
        "adversary": adversary_summary,
        "ship": ship_summary,
        "total_events": len(rows),
    }


def cmd_record(args: argparse.Namespace) -> int:
    project = Project(Path(args.project).expanduser().resolve())
    data = json.loads(args.data) if args.data else {}
    if not isinstance(data, dict):
        print("--data must be a JSON object", file=sys.stderr)
        return 1
    record_metric(project, args.event, **data)
    print(f"[metrics] recorded {args.event}")
    return 0


def _fmt(value: float | None, suffix: str = "") -> str:
    return f"{value}{suffix}" if value is not None else "-"


def cmd_summary(args: argparse.Namespace) -> int:
    project = Project(Path(args.project).expanduser().resolve())
    result = summarize(read_metrics(project))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    qg, ap, ad, sh = result["quality_gate"], result["approval"], result["adversary"], result["ship"]
    print(f"Quality gate: {qg['runs']} run(s), hit rate {_fmt(qg['hit_rate_pct'], '%')}, "
          f"avg lint {qg['avg_lint_findings']}, avg type {qg['avg_type_findings']}")
    print(f"Approval latency: {ap['count']} approval(s), avg {_fmt(ap['avg_latency_seconds'], 's')}, "
          f"median {_fmt(ap['median_latency_seconds'], 's')}")
    print(f"Adversary: {ad['features']} feature(s), convergence {_fmt(ad['convergence_rate_pct'], '%')}, "
          f"avg {ad['avg_passes']} passes, avg Critical/High found {ad['avg_critical_found']}/{ad['avg_high_found']}")
    print(f"Ship: {sh['attempts']} attempt(s), pass rate {_fmt(sh['pass_rate_pct'], '%')}, failed gates {sh['failed_gates']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record")
    rec.add_argument("--project", required=True)
    rec.add_argument("--event", required=True)
    rec.add_argument("--data", default="{}", help="JSON object of extra fields")
    rec.set_defaults(func=cmd_record)

    summ = sub.add_parser("summary")
    summ.add_argument("--project", required=True)
    summ.add_argument("--json", action="store_true")
    summ.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
