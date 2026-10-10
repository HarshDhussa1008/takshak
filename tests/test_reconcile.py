"""Docs-vs-code drift: the deterministic half of /takshak:reconcile.

Everything here runs the way Claude Code runs it -- hooks over stdin/stdout, tools as
subprocesses, against a real git repository -- because the interesting behaviour is how git
tree snapshots, the edit tracker and the Stop hook interact, not any one function."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import ROOT, TEMPLATES, Proj

sys.path.insert(0, str(ROOT / "hooks"))

import _reconcile as reconcile  # noqa: E402
from _common import Project  # noqa: E402

TOOL = ROOT / "tools" / "reconcile.py"

GUIDE = """# Implementation Guide: rate limit

## Gist
Token bucket per tenant.

## Module map
<!-- covers: app/limiter/ -->
| Module | Responsibility |
|---|---|
| `app/limiter/` | bucket maths |

## API contracts (as built)
<!-- covers: app/api.py -->
| Method & path | Source |
|---|---|
| `POST /check` | `app/api.py:12` |
"""


def run_tool(project: Proj, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args, "--project", str(project.root)],
        capture_output=True, text=True, env=project.env, timeout=60, check=False,
    )


def status(project: Proj) -> dict:
    out = run_tool(project, "status", "--json")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def edit(project: Proj, rel: str, text: str) -> None:
    path = project.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    project.hook("reconcile_tracker", {"tool_name": "Edit", "tool_input": {"file_path": str(path)}})


@pytest.fixture
def repo(project: Proj, plan) -> Proj:
    """An approved plan, a git repo, and a committed baseline of source + a guide."""
    project.init_git()
    project.write(".claude/.gitignore", "*\n")  # runtime state is never version-controlled
    project.write(".claude/task_state.json", plan(approved=True, statuses=("in_progress", "pending")))
    project.write("app/limiter/bucket.py", "def take(n: int) -> bool:\n    return n > 0\n")
    project.write("app/api.py", "def check() -> dict:\n    return {}\n")
    project.write("docs/implementation/rate-limit.md", GUIDE)
    project.git("add", "-A")
    project.git("commit", "-qm", "baseline")
    return project


# ------------------------------------------------------------------ classification

def test_only_source_files_count_as_drift(project: Proj) -> None:
    p = Project(project.root)
    for rel in ("app/api.py", "app/limiter/bucket.py", "openapi.yaml", "migrations/001_add_bucket.sql", "config/limits.toml"):
        assert reconcile.is_source_file(p, rel), rel
    for rel in (
        "README.md", "docs/sdd/rate-limit.md", "docs/implementation/rate-limit.md", ".claude/task_state.json",
        "tests/test_api.py", "app/test_bucket.py", "app/bucket.test.ts", "node_modules/x/index.js",
        "dist/bundle.js", "poetry.lock", "logo.png",
    ):
        assert not reconcile.is_source_file(p, rel), rel


def test_covers_markers_map_sections_to_paths() -> None:
    covers = reconcile.parse_covers(GUIDE)
    assert covers == {"Module map": ["app/limiter/"], "API contracts (as built)": ["app/api.py"]}
    assert reconcile.path_covered("app/limiter/bucket.py", "app/limiter/")
    assert reconcile.path_covered("app/api.py", "app/api.py")
    assert not reconcile.path_covered("app/api_v2.py", "app/api.py")  # not a prefix match on bare names


def test_shipped_template_declares_covers_for_its_sections() -> None:
    covers = reconcile.parse_covers((TEMPLATES / "IMPLEMENTATION.md.template").read_text(encoding="utf-8"))
    assert {"Module map", "Control flow", "API contracts (as built)", "Data models (as built)"} <= set(covers)


# ------------------------------------------------------------------ tracker hook

def test_tracker_is_silent_and_records_source_edits(repo: Proj) -> None:
    path = repo.root / "app" / "limiter" / "bucket.py"
    result = repo.hook("reconcile_tracker", {"tool_name": "Edit", "tool_input": {"file_path": str(path)}})
    assert result.stdout == ""
    assert repo.read(".claude/reconcile_state.json")["edited"] == {"app/limiter/bucket.py": 1}


def test_tracker_ignores_docs_tests_and_outside_files(repo: Proj, tmp_path) -> None:
    for target in (repo.root / "README.md", repo.root / "tests" / "test_x.py", tmp_path / "elsewhere.py"):
        repo.hook("reconcile_tracker", {"tool_name": "Write", "tool_input": {"file_path": str(target)}})
    assert not (repo.claude / "reconcile_state.json").exists()


def test_tracker_off_when_reconcile_disabled(repo: Proj) -> None:
    config = repo.read(".claude/framework.json")
    config["reconcile"] = False
    repo.write(".claude/framework.json", config)
    repo.hook("reconcile_tracker", {"tool_input": {"file_path": str(repo.root / "app" / "api.py")}})
    assert not (repo.claude / "reconcile_state.json").exists()


# ------------------------------------------------------------------ drift lifecycle

def test_first_reconcile_is_init_then_clean_after_mark(repo: Proj) -> None:
    repo.write("docs/implementation/rate-limit.md", GUIDE)
    (repo.root / "docs" / "implementation" / "rate-limit.md").unlink()
    edit(repo, "app/api.py", "def check() -> dict:\n    return {'ok': True}\n")
    first = status(repo)
    assert first["needs"] == "init" and not first["impl_doc_exists"] and first["changed"] == ["app/api.py"]

    repo.write("docs/implementation/rate-limit.md", GUIDE)
    assert run_tool(repo, "mark").returncode == 0
    after = status(repo)
    assert after["needs"] == "none" and after["changed"] == [] and after["has_baseline"]
    assert run_tool(repo, "check").returncode == 0


def test_edit_after_mark_is_drift_and_maps_to_sections(repo: Proj) -> None:
    run_tool(repo, "mark")
    edit(repo, "app/limiter/bucket.py", "def take(n: int) -> bool:\n    return n >= 1\n")
    edit(repo, "app/worker.py", "def run() -> None:\n    pass\n")
    info = status(repo)
    assert info["needs"] == "update"
    assert info["changed"] == ["app/limiter/bucket.py", "app/worker.py"]
    assert info["stale_sections"] == ["Module map"]
    assert info["undocumented"] == ["app/worker.py"]  # no section covers it yet
    check = run_tool(repo, "check")
    assert check.returncode == 1 and "/takshak:reconcile" in check.stdout


def test_committing_reconciled_work_does_not_reflag_it(repo: Proj) -> None:
    """The reason the baseline is a tree snapshot and not a commit: reconcile runs *before* the
    task's commit, so a commit-based baseline would call the same files drifted again."""
    run_tool(repo, "mark")
    edit(repo, "app/api.py", "def check() -> dict:\n    return {'v': 2}\n")
    assert status(repo)["needs"] == "update"
    run_tool(repo, "mark")  # skill finished: guide now reflects the edit
    repo.git("add", "-A")
    repo.git("commit", "-qm", "T1: tweak api")
    assert status(repo)["needs"] == "none"


def test_reverted_edit_is_not_drift(repo: Proj) -> None:
    run_tool(repo, "mark")
    original = (repo.root / "app" / "api.py").read_text(encoding="utf-8")
    edit(repo, "app/api.py", "def check() -> dict:\n    return {'tmp': 1}\n")
    edit(repo, "app/api.py", original)  # tracker saw two edits, content is back to baseline
    assert status(repo)["needs"] == "none"


def test_user_editing_outside_claude_is_still_detected(repo: Proj) -> None:
    run_tool(repo, "mark")
    (repo.root / "app" / "api.py").write_text("def check() -> dict:\n    return {'by': 'hand'}\n", encoding="utf-8")
    assert status(repo)["changed"] == ["app/api.py"]  # no tracker involved: the tree diff sees it


def test_snapshot_never_touches_the_users_staging_area(repo: Proj) -> None:
    run_tool(repo, "mark")
    edit(repo, "app/new_module.py", "X = 1\n")
    staged_before = repo.git("diff", "--cached", "--name-only")
    status(repo)
    assert repo.git("diff", "--cached", "--name-only") == staged_before == ""


def test_final_pass_only_counts_until_the_next_change(repo: Proj) -> None:
    run_tool(repo, "mark", "--final")
    assert status(repo)["final"] is True
    edit(repo, "app/api.py", "def check() -> dict:\n    return {'late': 1}\n")
    assert status(repo)["final"] is False


def test_works_without_git_via_the_edit_tracker(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=True))
    edit(project, "app/api.py", "x = 1\n")
    assert status(project)["changed"] == ["app/api.py"]


# ------------------------------------------------------------------ Stop hook

def test_stop_asks_for_reconcile_when_a_task_completes(repo: Proj, plan) -> None:
    run_tool(repo, "mark")
    repo.write(".claude/task_state.json", plan(approved=True, statuses=("completed", "in_progress")))
    repo.write(".claude/checkpoint.json", {"active_skill": "sdd-implementer", "phase": 1})
    edit(repo, "app/limiter/bucket.py", "def take(n: int) -> bool:\n    return n >= 1\n")
    result = repo.hook_json("stop_checklist")
    assert result["decision"] == "block"
    assert "takshak:reconcile" in result["reason"] and "docs/implementation/rate-limit.md" in result["reason"]
    assert "task(s) 1 completed" in result["reason"]
    # one nudge, not a loop
    assert "decision" not in repo.hook_json("stop_checklist")


def test_stop_asks_after_threshold_edits_even_without_a_finished_task(repo: Proj) -> None:
    run_tool(repo, "mark")
    for i in range(4):
        edit(repo, f"app/m{i}.py", f"V = {i}\n")
    assert "decision" not in repo.hook_json("stop_checklist") or "reconcile" not in repo.hook_json("stop_checklist").get("reason", "")
    edit(repo, "app/m4.py", "V = 4\n")  # fifth distinct file reaches reconcile_edit_threshold
    repo.write(".claude/checkpoint.json", {"active_skill": "sdd-implementer", "phase": 1})
    result = repo.hook_json("stop_checklist", {"session_id": "s2"})
    assert result.get("decision") == "block" and "5 source file(s) changed" in result["reason"]


def test_stop_says_create_when_no_guide_exists_yet(repo: Proj, plan) -> None:
    (repo.root / "docs" / "implementation" / "rate-limit.md").unlink()
    repo.write(".claude/task_state.json", plan(approved=True, statuses=("completed", "pending")))
    repo.write(".claude/checkpoint.json", {"active_skill": "sdd-implementer", "phase": 1})
    edit(repo, "app/api.py", "def check() -> dict:\n    return {'a': 1}\n")
    result = repo.hook_json("stop_checklist")
    assert result["decision"] == "block" and " create docs/implementation/rate-limit.md" in result["reason"]


def test_stop_does_not_ask_before_approval_or_when_disabled(repo: Proj, plan) -> None:
    run_tool(repo, "mark")
    for i in range(6):
        edit(repo, f"app/m{i}.py", f"V = {i}\n")
    repo.write(".claude/task_state.json", plan(approved=False))
    assert "reconcile" not in repo.hook_json("stop_checklist").get("reason", "")

    repo.write(".claude/task_state.json", plan(approved=True))
    config = repo.read(".claude/framework.json")
    config["reconcile"] = False
    repo.write(".claude/framework.json", config)
    assert "reconcile" not in repo.hook_json("stop_checklist", {"session_id": "s3"}).get("reason", "")


# ------------------------------------------------------------------ surfaces

def test_session_start_points_at_the_guide_and_flags_staleness(repo: Proj) -> None:
    run_tool(repo, "mark")
    out = repo.hook("session_start").stdout
    assert "[GUIDE] docs/implementation/rate-limit.md" in out and "Out of date" not in out
    edit(repo, "app/api.py", "def check() -> dict:\n    return {'z': 1}\n")
    assert "Out of date: 1 source file(s)" in repo.hook("session_start").stdout


def test_session_start_silent_about_a_guide_that_does_not_exist(repo: Proj) -> None:
    (repo.root / "docs" / "implementation" / "rate-limit.md").unlink()
    assert "[GUIDE]" not in repo.hook("session_start").stdout


def test_dashboard_lists_stale_guide_as_a_review_blocker(repo: Proj) -> None:
    run_tool(repo, "mark")
    edit(repo, "app/api.py", "def check() -> dict:\n    return {'dash': 1}\n")
    sdd = repo.root / "docs" / "sdd" / "rate-limit.md"
    sdd.parent.mkdir(parents=True, exist_ok=True)
    sdd.write_text(
        "# SDD\n\n## Risk Register\n| Risk | Class | Severity | Mitigation | Status |\n|---|---|---|---|---|\n"
        "| r | Concurrency | High | m | mitigated |\n", encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "tools" / "dashboard.py"), "--project", str(repo.root)],
                   capture_output=True, text=True, env=repo.env, timeout=30, check=True)
    html = (repo.claude / "dashboard.html").read_text(encoding="utf-8")
    assert "Implementation Guide is behind the code (1 source file(s) changed)" in html


def test_reconcile_event_is_recorded_for_metrics(repo: Proj) -> None:
    edit(repo, "app/api.py", "def check() -> dict:\n    return {'m': 1}\n")
    run_tool(repo, "mark")
    rows = [json.loads(ln) for ln in (repo.claude / "metrics.jsonl").read_text().splitlines()]
    assert rows[-1]["event"] == "reconcile" and rows[-1]["files"] >= 1


# ------------------------------------------------------------------ plugin contract

def test_reconcile_skill_and_ship_gate_are_wired() -> None:
    skill = (ROOT / "skills" / "reconcile" / "SKILL.md").read_text(encoding="utf-8")
    assert "disable-model-invocation" not in skill  # the Stop hook asks Claude to invoke it
    assert "reconcile.py\" mark" in skill and "--final" in skill
    ship = (ROOT / "skills" / "ship" / "SKILL.md").read_text(encoding="utf-8")
    assert "reconcile.py\" check" in ship
    sdd_template = (ROOT / "templates" / "SDD.md.template").read_text(encoding="utf-8")
    assert "## Module Map" in sdd_template and "## Key Flows" in sdd_template


# ------------------------------------------------------------------ adopting an existing codebase

@pytest.fixture
def legacy(project: Proj) -> Proj:
    """A committed codebase with no SDD, no plan and no guide -- the reverse-engineering case."""
    project.init_git()
    project.write(".claude/.gitignore", "*\n")
    project.write("pyproject.toml", "[project]\nname = 'billing'\n")
    project.write("README.md", "# billing\n")
    project.write("billing/api.py", "from flask import Flask\napp = Flask(__name__)\n\n@app.route('/invoices', methods=['POST'])\ndef create():\n    return {}\n")
    project.write("billing/ledger.py", "def post(entry):\n    return entry\n")
    project.write("worker/jobs.py", "import argparse\n\ndef main():\n    argparse.ArgumentParser()\n\nif __name__ == '__main__':\n    main()\n")
    project.write("tests/test_ledger.py", "def test_post():\n    pass\n")
    project.write("node_modules/dep/index.js", "x\n")
    project.git("add", "-f", "-A")
    project.git("commit", "-qm", "legacy")
    return project


def inventory(project: Proj, *extra: str) -> dict:
    out = run_tool(project, "inventory", "--json", *extra)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_clean_repo_without_a_guide_is_not_reported_in_sync(legacy: Proj) -> None:
    """The bug adopt mode exists to fix: no changes + no guide used to read as 'docs in sync'."""
    info = status(legacy)
    assert info["needs"] == "init" and not info["impl_doc_exists"] and info["changed"] == []
    assert run_tool(legacy, "check").returncode == 1


def test_inventory_maps_modules_entry_points_and_tests(legacy: Proj) -> None:
    inv = inventory(legacy)
    mods = {m["path"]: m for m in inv["modules"]}
    assert set(mods) == {"billing/", "worker/", "(root)"}  # (root) = pyproject.toml; node_modules and tests are not modules
    assert "http_route" in mods["billing/"]["entry_kinds"]
    assert {"main", "cli"} <= set(mods["worker/"]["entry_kinds"])
    assert mods["billing/"]["tests"] == ["tests/test_ledger.py"]  # matched to billing/ledger.py by stem
    assert "pyproject.toml" in inv["manifests"] and "README.md" in inv["existing_docs"]
    assert inv["totals"]["source_files"] == 4 and inv["totals"]["test_files"] == 1
    assert len(inv["batches"]) == 1 and set(inv["batches"][0]) == set(mods) and inv["parallel_recommended"] is False


def test_inventory_scopes_to_a_subdirectory(legacy: Proj) -> None:
    inv = inventory(legacy, "--path", "worker")
    assert [m["path"] for m in inv["modules"]] == ["worker/"] and inv["scope"] == "worker"


def test_inventory_splits_big_modules_into_their_own_batches(project: Proj) -> None:
    project.init_git()
    for i in range(30):  # one module over the per-batch file cap
        project.write(f"big/m{i}.py", "x = 1\n")
    project.write("small/a.py", "y = 2\n")
    project.git("add", "-A")
    project.git("commit", "-qm", "x")
    inv = inventory(project)
    big = next(m for m in inv["modules"] if m["path"] == "big/")
    assert big["split"] is True
    assert inv["batches"] == [["big/"], ["small/"]]


def test_inventory_works_outside_git(project: Proj) -> None:
    project.write("svc/app.py", "print('hi')\n")
    assert [m["path"] for m in inventory(project)["modules"]] == ["svc/"]


def test_mark_doc_records_where_an_adopted_guide_lives(legacy: Proj) -> None:
    legacy.write("docs/implementation/billing.md", GUIDE.replace("app/limiter/", "billing/").replace("app/api.py", "billing/api.py"))
    assert run_tool(legacy, "mark", "--doc", "docs/implementation/billing.md").returncode == 0
    info = status(legacy)
    assert info["impl_doc"] == "docs/implementation/billing.md" and info["impl_doc_exists"]
    assert info["needs"] == "none" and run_tool(legacy, "check").returncode == 0
    # and ordinary drift detection takes over from the adopted baseline
    edit(legacy, "billing/ledger.py", "def post(entry):\n    return {'e': entry}\n")
    after = status(legacy)
    assert after["needs"] == "update" and after["stale_sections"] == ["Module map"]


def test_adopt_mode_is_documented_in_the_skill() -> None:
    skill = (ROOT / "skills" / "reconcile" / "SKILL.md").read_text(encoding="utf-8")
    assert "adopt [path]" in skill and "reconcile.py\" inventory" in skill and "--doc" in skill
    assert "unknown — ask" in skill and "Inferred intent" in skill
