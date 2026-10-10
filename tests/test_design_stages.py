"""Staged design: scaffold -> freeze -> LLD stamp -> readiness -> reviewer pack."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import ROOT, Proj

sys.path.insert(0, str(ROOT / "hooks"))
import _design as design  # noqa: E402
from _common import Project  # noqa: E402

SDD_REL = "docs/sdd/rate-limiter.md"

SDD = """# SDD: Rate limiter

## Overview
Per-tenant request limiting in front of the public API so one noisy tenant cannot starve the rest.

## Goals
- Protect shared capacity with per-tenant quotas

## Non-Goals
- Global (cross-region) quotas; billing integration

## Requirements
| ID | Requirement | Acceptance criterion | Priority |
|----|-------------|----------------------|----------|
| R1 | Reject requests over the tenant quota | 429 with Retry-After after N calls in a window | Must |
| R2 | Quota is configurable per tenant | A config change applies within 30 s without restart | Must |
| NFR1 | Limiter adds little latency | p99 overhead under 5 ms at 2k rps | Must |
| R9 | Admin UI for quotas | n/a | Won't (later) |

## Architecture
```mermaid
flowchart LR
    Client --> Gateway
    Gateway --> Limiter
    Limiter --> Redis
```
Counters live in Redis because they must survive restarts (ADR-0001).

## Design Decisions
| ADR | Decision | Status |
|-----|----------|--------|
| ADR-0001 | Redis for counters | accepted |

## Module Map
| Module / path | Responsibility | Depends on | Public surface |
|---------------|----------------|------------|----------------|
| `app/limiter/bucket.py` | token bucket maths | - | `take(tenant, n)` |
| `app/limiter/middleware.py` | HTTP integration | `bucket.py` | `RateLimitMiddleware` |

## Key Flows
```mermaid
sequenceDiagram
    participant Client
    participant Middleware
    participant Bucket
    Client->>Middleware: request
    Middleware->>Bucket: take(tenant, 1)
    Bucket-->>Middleware: allowed
    Middleware-->>Client: 200
```
Failure path: if Redis times out the middleware fails open, logs an error and emits a metric.

## Data Models
Counter key `rl:{tenant}:{window}` holding an integer with a TTL of two windows.

## API Contracts
`POST /v1/check` returns 200 with remaining quota. Errors: 429 when over quota with a Retry-After header; 503 when the limiter cannot decide and fail-open is disabled.

## Detailed Design
Only `bucket.py`: a Lua script performs the decrement atomically so concurrent workers cannot double count.

## Error Handling
Redis timeouts fail open by default; configuration errors fail at startup.

## Observability
Counters for allowed, rejected and fail-open decisions per tenant; an alert fires when fail-open exceeds 1 percent.

## Security
Tenant identity comes from the verified token, never from a header the client controls. Spoofing is mitigated by the gateway; tampering with counters requires Redis access, which is network-restricted; denial of service by key explosion is bounded by the tenant count.

## Rollout and Rollback
Ships behind a flag at 5 percent of tenants, then 50, then 100. Roll back by disabling the flag; counters expire on their own so no data cleanup is needed.

## Risk Register
| Risk | Class | Severity | Mitigation | Status |
|------|-------|----------|------------|--------|
| Double counting under load | Concurrency / races | Critical | atomic Lua decrement | mitigated (Detailed Design) |
| Retried request counted twice | Partial failure / retries, Idempotency | High | request id dedupe | mitigated (Data Models) |
| Cannot revert | Rollback / migration | Medium | flag off | mitigated (Rollout) |
| Tenant spoofing | Auth / authz | High | token identity | mitigated (Security) |
| Cost at 100x | Quota / limits / cost | Medium | key TTL | mitigated (Data Models) |
| Window skew | Clock / ordering | Medium | server-side clock | mitigated (Detailed Design) |
| Orphan keys | Data integrity | Medium | TTL | mitigated (Data Models) |
| Silent fail-open | Observability gaps | High | alert | mitigated (Observability) |
| Existing clients | Backward compatibility | Medium | flag rollout | mitigated (Rollout) |

## Test Plan
| Req | Test | Type |
|-----|------|------|
| R1 | over-quota returns 429 | integration |
| R2 | config change applies | integration |
| NFR1 | latency benchmark | perf |

## Amendments
(none)

## Open Questions
- [x] Fail open or closed? -- resolved: open, see ADR-0001
"""

NOTES = """# Design notes: Rate limiter

## Problem and context
One tenant's burst took the API down for everyone last quarter; support escalations followed.

## Assumptions
- Redis is already available in every region -- confirmed by platform team

## Discussion log
- Fail open or closed? -> open, because availability matters more than strict limiting
"""

ADR = """# ADR-0001: Redis for counters

- Status: accepted
- Date: 2026-01-01
- Requirements: R1, NFR1
- Supersedes: -

## Context
Counters must survive restarts and be shared across workers.

## Options considered
### Option A - Redis with a Lua script
Atomic, shared, one network hop.

### Option B - in-process memory
No dependency, but lost on restart and not shared.

## Decision
Option A, because shared atomic counters are the requirement.

## Consequences
- Positive: correct under concurrency
- Negative: a new runtime dependency
- Revisit when: p99 latency exceeds 5 ms
"""

LLD = """# LLD: Rate limiter

## Files and layout
| Path | New / changed | Purpose |
|------|---------------|---------|
| `app/limiter/bucket.py` | new | counter maths |

## Module specifications
### `app/limiter/bucket.py`
- Responsibility: token bucket maths
- Public interface: `def take(tenant: str, n: int) -> bool`
### `app/limiter/middleware.py`
- Responsibility: HTTP integration; calls `take` and maps the result to 200 or 429
"""


def tool(project: Proj, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "design.py"), *args, "--project", str(project.root)],
                       capture_output=True, text=True, env=project.env, timeout=60, check=False)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def checks(project: Proj) -> dict[str, dict]:
    out = tool(project, "readiness", "--json", check=False)
    return {c["check"]: c for c in json.loads(out.stdout)["checks"]}


def failing(project: Proj) -> set[str]:
    return {name for name, c in checks(project).items() if not c["ok"]}


@pytest.fixture
def design_project(project: Proj) -> Proj:
    project.write(SDD_REL, SDD)
    project.write("docs/design-notes/rate-limiter.md", NOTES)
    project.write("docs/adr/0001-redis-for-counters.md", ADR)
    project.write("docs/lld/rate-limiter.md", LLD)
    project.write(".claude/task_state.json", {**json.loads((ROOT / "templates/state/task_state.json").read_text()), "sdd_path": SDD_REL})
    return project


@pytest.fixture
def ready(design_project: Proj) -> Proj:
    tool(design_project, "freeze")
    tool(design_project, "stamp-lld")
    return design_project


# ------------------------------------------------------------------ the happy path

def test_a_complete_staged_design_is_ready(ready: Proj) -> None:
    assert failing(ready) == set()
    assert tool(ready, "readiness").returncode == 0
    status = json.loads(tool(ready, "status", "--json").stdout)
    assert status["stage"] == "ready" and status["ready"] and status["freeze"]["intact"]


def test_scaffold_creates_the_canonical_files_once(project: Proj) -> None:
    first = json.loads(tool(project, "scaffold", "--title", "Rate limiter").stdout)
    assert first["created"] == ["docs/sdd/rate-limiter.md", "docs/design-notes/rate-limiter.md"]
    assert json.loads(tool(project, "scaffold", "--title", "Rate limiter").stdout)["created"] == []
    assert tool(project, "readiness", check=False).returncode == 1  # a skeleton is nowhere near ready
    assert json.loads(tool(project, "status", "--json").stdout)["stage"] == "hld"


# ------------------------------------------------------------------ each check bites

MUTATIONS = {
    "Every requirement is testable": lambda t: t.replace("429 with Retry-After after N calls in a window", ""),
    "Non-functional requirements are numeric": lambda t: t.replace("p99 overhead under 5 ms at 2k rps", "fast"),
    "Test plan covers every requirement": lambda t: t.replace("| NFR1 | latency benchmark | perf |\n", ""),
    "Threat model": lambda t: t.replace("## Security\n", "## Security\n{{todo}} ").replace("Tenant identity", "Tenant"),
    "Rollout and rollback plan": lambda t: t.replace("Roll back by disabling the flag", "Nothing to do"),
    "Observability": lambda t: t.replace("Counters for allowed, rejected and fail-open decisions per tenant; an alert fires when fail-open exceeds 1 percent.", "TBD"),
    "No unresolved open questions": lambda t: t.replace("- [x] Fail open or closed? -- resolved: open, see ADR-0001", "- Fail open or closed?"),
    "No template placeholders / TBD": lambda t: t.replace("(none)", "TODO decide later"),
    "Architecture diagram": lambda t: t.replace("    Client --> Gateway\n    Gateway --> Limiter\n    Limiter --> Redis\n", "    Client\n"),
    "Key flows incl. a failure path": lambda t: t.replace("Failure path: if Redis times out the middleware fails open, logs an error and emits a metric.", "Happy only."),
    "Adversarially hardened": lambda t: t.replace("| Cost at 100x | Quota / limits / cost | Medium | key TTL | mitigated (Data Models) |\n", ""),
    "Every endpoint specifies its errors": lambda t: t.replace("`POST /v1/check` returns 200 with remaining quota. Errors: 429 when over quota with a Retry-After header; 503 when the limiter cannot decide and fail-open is disabled.", "`POST /v1/check` returns 200 with remaining quota."),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_readiness_check_fails_when_its_condition_is_broken(design_project: Proj, name: str) -> None:
    path = design_project.root / SDD_REL
    mutated = MUTATIONS[name](path.read_text())
    assert mutated != path.read_text(), "mutation did not change the SDD"
    path.write_text(mutated)
    assert name in failing(design_project)


def test_critical_risk_must_be_mitigated(design_project: Proj) -> None:
    path = design_project.root / SDD_REL
    path.write_text(path.read_text().replace("atomic Lua decrement | mitigated (Detailed Design)", "atomic Lua decrement | open"))
    assert "Adversarially hardened" in failing(design_project)
    assert any("Critical risk" in p for p in design.hardening_problems(path.read_text()))


def test_decisions_need_alternatives_and_a_verdict(design_project: Proj) -> None:
    adr_path = design_project.root / "docs/adr/0001-redis-for-counters.md"
    adr_path.write_text(adr_path.read_text().replace("### Option B - in-process memory", "### Notes"))
    assert "ADRs are sound (alternatives, no placeholders)" in failing(design_project)
    adr_path.write_text(ADR.replace("- Status: accepted", "- Status: proposed"))
    assert {"No ADR is still only proposed", "Decisions recorded as ADRs"} <= failing(design_project)


def test_design_notes_must_be_real(design_project: Proj) -> None:
    (design_project.root / "docs/design-notes/rate-limiter.md").write_text("# notes\n")
    assert "Design notes recorded" in failing(design_project)


# ------------------------------------------------------------------ freeze + LLD

def test_cannot_freeze_an_unfinished_design(project: Proj) -> None:
    tool(project, "scaffold", "--title", "Rate limiter")
    r = tool(project, "freeze", check=False)
    assert r.returncode == 1 and "cannot freeze" in r.stderr


def test_lld_requires_a_freeze_and_a_current_stamp(design_project: Proj) -> None:
    assert {"Design frozen", "LLD derived from the frozen design"} <= failing(design_project)
    assert tool(design_project, "stamp-lld", check=False).returncode == 1  # nothing frozen yet
    tool(design_project, "freeze")
    assert "LLD derived from the frozen design" in failing(design_project)  # frozen but unstamped
    assert json.loads(tool(design_project, "status", "--json").stdout)["stage"] == "frozen"
    tool(design_project, "stamp-lld")
    assert failing(design_project) == set()


def test_editing_the_design_after_the_freeze_is_detected(ready: Proj) -> None:
    path = ready.root / SDD_REL
    path.write_text(path.read_text().replace("Redis timeouts fail open by default", "Redis timeouts fail closed"))
    f = failing(ready)
    assert "Design frozen" in f and "changed after the freeze" in checks(ready)["Design frozen"]["detail"]
    assert json.loads(tool(ready, "status", "--json").stdout)["stage"] == "design-changed-after-freeze"
    r = tool(ready, "freeze", check=False)
    assert r.returncode == 1 and "--reason" in r.stderr  # a silent re-freeze is refused
    tool(ready, "freeze", "--reason", "DC-1: fail closed")  # a deliberate, recorded re-freeze (what /takshak:redesign does) ...
    assert "LLD derived from the frozen design" in failing(ready)  # ... makes the old LLD stale until regenerated
    tool(ready, "stamp-lld")
    assert failing(ready) == set()


def test_changing_an_accepted_decision_after_the_freeze_is_detected(ready: Proj) -> None:
    p = ready.root / "docs/adr/0001-redis-for-counters.md"
    p.write_text(p.read_text().replace("Option A, because shared atomic counters are the requirement.", "Option B, because simpler."))
    assert "Design frozen" in failing(ready)


def test_risk_register_and_amendments_may_change_after_the_freeze(ready: Proj) -> None:
    path = ready.root / SDD_REL
    path.write_text(path.read_text().replace("(none)", "2026-02-01: clarified retention (task 3)"))
    assert "Design frozen" not in failing(ready)


def test_lld_must_cover_every_module(ready: Proj) -> None:
    p = ready.root / "docs/lld/rate-limiter.md"
    p.write_text(p.read_text().replace("### `app/limiter/middleware.py`\n- Responsibility: HTTP integration; calls `take` and maps the result to 200 or 429\n", ""))
    f = checks(ready)["LLD specifies every module"]
    assert not f["ok"] and "middleware.py" in f["detail"]


def test_hash_is_stable_across_whitespace(design_project: Proj) -> None:
    a = design.design_hash(Project(design_project.root), SDD_REL)
    path = design_project.root / SDD_REL
    path.write_text(path.read_text().replace("\n\n## Goals", "\n\n\n\n## Goals"))
    assert design.design_hash(Project(design_project.root), SDD_REL) == a


# ------------------------------------------------------------------ reviewer pack

def test_review_pack_is_a_self_contained_summary(ready: Proj) -> None:
    rel = tool(ready, "pack").stdout.strip()
    assert rel == "docs/reviews/rate-limiter.html"
    page = (ready.root / rel).read_text()
    for needle in ("Rate limiter", "Readiness 24/24", "Design frozen", "Redis for counters", "NFR1",
                   'class="mermaid"', "Decisions needed from you", "Roll back by disabling the flag", "Requirement coverage"):
        assert needle in page, needle
    assert "Nothing is waiting on a decision" in page


def test_review_pack_surfaces_what_needs_a_human_and_escapes_content(design_project: Proj) -> None:
    p = design_project.root / "docs/adr/0001-redis-for-counters.md"
    p.write_text(ADR.replace("- Status: accepted", "- Status: proposed").replace("Atomic, shared, one network hop.", "Atomic <script>alert(1)</script> hop."))
    page = (design_project.root / tool(design_project, "pack").stdout.strip()).read_text()
    assert "ADR-0001 Redis for counters" in page.split("Decisions needed from you")[1].split("</div>")[0]
    assert "<script>alert(1)</script>" not in page
    assert "Readiness " in page and "not frozen" in page


def test_refreezes_are_recorded_and_shown_to_reviewers(ready: Proj) -> None:
    path = ready.root / SDD_REL
    path.write_text(path.read_text().replace("fail open by default", "fail closed by default"))
    tool(ready, "freeze", "--reason", "DC-1: fail closed")
    record = json.loads((ready.root / "docs/sdd/rate-limiter.freeze.json").read_text())
    assert [r["reason"] for r in record["refreezes"]] == ["DC-1: fail closed"]
    tool(ready, "stamp-lld")
    page = (ready.root / tool(ready, "pack").stdout.strip()).read_text()
    assert "Changes after the freeze" in page and "DC-1: fail closed" in page
    assert tool(ready, "freeze").returncode == 0  # re-freezing an unchanged design needs no reason and adds no entry
    assert len(json.loads((ready.root / "docs/sdd/rate-limiter.freeze.json").read_text())["refreezes"]) == 1
