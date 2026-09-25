"""tools/dashboard.py -- run as a real subprocess against a seeded project, the way
/takshak:dashboard actually invokes it, and the rendered HTML inspected for the
review-verdict banner, the pipeline stepper, and the metrics/adversary sections."""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import ROOT, Proj

DASHBOARD = ROOT / "tools" / "dashboard.py"


def render(project: Proj) -> str:
    result = subprocess.run(
        [sys.executable, str(DASHBOARD), "--project", str(project.root)],
        capture_output=True, text=True, env=project.env, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    return (project.claude / "dashboard.html").read_text(encoding="utf-8")


def write_sdd(project: Proj, risk_rows: str) -> None:
    sdd = project.root / "docs" / "sdd" / "feature.md"
    sdd.parent.mkdir(parents=True, exist_ok=True)
    sdd.write_text(
        "# SDD: Feature\n\n## Risk Register\n"
        "| Risk | Class | Severity | Mitigation | Status |\n|---|---|---|---|---|\n"
        f"{risk_rows}\n\n## Amendments\n", encoding="utf-8",
    )


def test_verdict_ready_when_all_risks_mitigated(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(sdd_path="docs/sdd/feature.md"))
    write_sdd(project, "| Overflow | Data integrity | High | Bound check | mitigated (§X) |")
    html = render(project)
    assert "Ready for review" in html
    assert "no open blockers" in html


def test_verdict_blocked_on_unmitigated_critical_risk(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(sdd_path="docs/sdd/feature.md"))
    write_sdd(project, "| Data loss | Partial failure | Critical | TBD | open |")
    html = render(project)
    assert "Blocked on" in html
    assert "unmitigated Critical/High" in html


def test_verdict_blocked_on_staged_amendments(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(sdd_path="docs/sdd/feature.md"))
    write_sdd(project, "| Overflow | Data integrity | High | Bound check | mitigated (§X) |")
    project.write(".claude/amendments_pending.json", {"candidates": [{"gap": "missed a case", "sdd_section": "Error Handling"}], "style_candidates": []})
    html = render(project)
    assert "Blocked on" in html
    assert "staged amendment" in html


def test_pipeline_stage_reflects_task_state(project: Proj, plan) -> None:
    # no tasks yet -> Design is current
    html = render(project)
    assert '<div class="stage current"><span class="stage-dot"></span>Design</div>' in html

    # tasks exist, not approved -> Approval is current
    project.write(".claude/task_state.json", plan(approved=False))
    html = render(project)
    assert '<div class="stage current"><span class="stage-dot"></span>Approval</div>' in html

    # approved -> Implement is current
    project.write(".claude/task_state.json", plan(approved=True))
    html = render(project)
    assert '<div class="stage current"><span class="stage-dot"></span>Implement</div>' in html


def test_metrics_and_adversary_history_render_from_jsonl(project: Proj) -> None:
    rows = [
        {"t": "2026-01-01T00:00:00+00:00", "event": "adversary_pass", "passes": 2, "risks_critical": 0,
         "risks_high": 1, "risks_medium": 1, "converged": True, "sdd_path": "docs/sdd/feature.md"},
        {"t": "2026-01-01T00:00:00+00:00", "event": "quality_gate", "file": "a.py", "lint": 1, "types": 0, "clean": False},
        {"t": "2026-01-01T00:00:00+00:00", "event": "ship", "env": "staging", "result": "pass"},
    ]
    (project.claude / "metrics.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    html = render(project)
    assert "adversary convergence" in html and "100.0%" in html
    assert "quality gate hit rate" in html
    assert "docs/sdd/feature.md" in html  # adversary history table


def test_dashboard_with_no_state_does_not_crash(tmp_path) -> None:
    bare = Proj(tmp_path / "empty", tmp_path / "data")
    (bare.root / ".claude").mkdir(parents=True)
    (bare.root / ".claude" / "framework.json").write_text("{}", encoding="utf-8")
    html = render(bare)
    assert "No metrics yet" in html
    assert "No risk register" in html
