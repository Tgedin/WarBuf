# Current State

## Mandatory Rule

Any agent must read this file before starting work and keep it updated.
This file stays short and is the single source of truth for:

- where we are vs goal
- next actions
- future improvement queue

## Goal Alignment Snapshot

- Goal: build a robust, well-tested, autonomous IBKR paper-trading machine. Start with EUR 300, deploy it in paper mode, and refine it through repeated paper-trading review cycles while keeping idle cash low except for the intentional safety floor.
- Current state: local code quality is stable (tests green), Hetzner deployment is intentionally stopped, IBKR enrollment is not complete yet.
- Gap to goal: gateway auth cannot be validated end-to-end until enrollment/2FA is fully active, and the testing/TDD workflow plus paper-validation scorecard are not yet formalized.

## Next Session Start Here

- Read order: `GOAL.md` -> `CURRENT_STATE.md` -> `AGENTS.md`.
- If IBKR enrollment / 2FA is still incomplete, treat real gateway authentication as an external blocker and do not begin with Hetzner work, manual login work, or remote deployment work.
- Start with the first unblocked local implementation slice in the order below.
- The first coding slice is the non-paper account safety gate in smoke/startup checks.
- Full IBKR enrollment is a manual prerequisite for later end-to-end gateway auth validation, not the first implementation task.
- Every behavior change starts with one failing test or one reproducible failing check.
- After each slice: run the narrowest validation that can falsify it, then update this file.

## First Coding Slice

- [ ] Implement non-paper account safety gate in smoke/startup checks.
      Purpose: highest-safety local change and the required first implementation task.
      Done when: startup or smoke checks reject any non-paper account configuration, and the behavior is covered by focused tests.

## External Blocker (Not First Coding Slice)

- [ ] Complete IBKR enrollment and confirm account can authenticate through Client Portal.
      Purpose: unblock real end-to-end gateway auth validation after the local safety slices are done.
      Done when: manual login and API session auth are both confirmed.

## Recommended Implementation Order

1. Non-paper account safety gate in smoke/startup checks.
   Why first: highest safety value, fully local, and directly testable.
2. Broker auth-unavailable guardrails and clear failure messages.
   Why next: protects order paths before runtime debugging begins.
3. Dedicated smoke-test DB path and migration idempotency coverage.
   Why next: makes repeated preflight validation trustworthy.
4. Docker healthchecks and startup gating for gateway readiness.
   Why next: removes startup flakiness only after local safety checks are solid.
5. Single local regression/readiness command.
   Why next: turns the validated pieces into one repeatable pass/fail gate.
6. Only after enrollment is ready: end-to-end local gateway auth validation, then any Hetzner redeploy decision.

## Evidence Baseline (IBKR Source of Truth)

- Paper accounts are simulated and do not execute on exchanges or clear at a clearing house.
  Source: https://ibkrcampus.com/campus/trading-lessons/signing-up-for-a-paper-trading-account/

- Paper accounts mirror the associated account configuration (permissions, subscriptions, base currency).
  Source: https://www.interactivebrokers.com/faq?id=23298988

- Paper simulator limitations include top-of-book simulated fills, limited order-type support, simulated stops, and no mutual funds.
  Source: https://www.interactivebrokers.com/faq?id=35807733

- IBKR explicitly states paper execution may not exactly match market outcomes.
  Source: https://www.ibkrguides.com/kb/article-252.htm

- Real-time data in paper requires a funded primary account and shared subscriptions.
  Source: https://www.interactivebrokers.com/faq?id=26561453

- Web API access for individuals (including associated paper account use) requires a fully open and funded primary account.
  Source: https://ibkrcampus.com/campus/ibkr-api-page/webapi-doc/

## Active Todo (Before Any Hetzner Paper Redeploy)

- [ ] Formalize the initial EUR 300 paper run as an operational-validation cycle, not an alpha contest.
      Purpose: test realism, safety, and autonomy before making the machine more ambitious.
      Done when: acceptance criteria are written and signed off.

- [ ] Define and lock the paper-validation scorecard (reliability, fee-model realism, auth stability, idle-cash discipline, review quality).
      Purpose: make keep/change/improve decisions objective and auditable.
      Done when: pass/fail thresholds are explicit.

- [ ] Keep Hetzner stack stopped until all checks below are green.
      Purpose: prevent accidental remote execution during hardening.  
       Done when: redeploy decision is explicitly approved.

- [ ] Run local quality gate: `.venv/bin/pytest tests/ -q && ruff check .`.
      Purpose: ensure baseline code reliability before runtime checks.
      Done when: both commands pass without warnings to address.

- [ ] Adopt TDD-by-default for behavior changes and bug fixes.
      Purpose: force every important change to start from a failing test or reproducible failing check.
      Done when: new logic is introduced through red -> green -> refactor rather than patch-first editing.

- [ ] Expand automated tests around broker auth failure, scheduler restart behavior, DB migrations, and deployment smoke paths.
      Purpose: catch the exact failures most likely to break a paper deployment.
      Done when: these runtime-critical paths have focused executable coverage.

- [ ] Define a local regression suite for paper deployment readiness.
      Purpose: make every redeploy depend on more than unit tests alone.
      Done when: one repeatable local sequence covers tests, compose startup, broker preflight, and smoke checks.

- [ ] Start local Docker stack and keep it stable for 10+ minutes.
      Purpose: detect restart loops, startup races, and container crashes early.
      Done when: all services remain up and logs are clean of critical errors.

- [ ] Verify `warbuf` container reaches gateway endpoint over Docker network.
      Purpose: confirm real app path connectivity, not host-only connectivity.
      Done when: endpoint call succeeds from inside `warbuf`.

- [ ] Confirm gateway protocol/bind behavior is correct for inter-container traffic.
      Purpose: remove ambiguity around localhost vs service DNS and http vs https.
      Done when: compose config and runtime behavior match exactly.

- [ ] Add/verify healthchecks and startup gating (warbuf waits for gateway readiness).
      Purpose: avoid brittle startup order and transient auth failures at boot.
      Done when: `warbuf` starts only after gateway health is confirmed.

- [ ] Verify broker preflight failure is clear and safe when auth is unavailable.
      Purpose: make failures diagnosable and non-destructive.
      Done when: logs include actionable reason and next step.

- [ ] Verify no trade action can execute while gateway auth is unavailable.
      Purpose: guarantee trading safety under partial outage conditions.
      Done when: order paths are blocked by explicit auth guardrails.

- [ ] Validate smoke flow on dedicated test DB and confirm migration idempotency.
      Purpose: ensure repeated runs do not corrupt schema or state.
      Done when: smoke can run multiple times with consistent DB results.

- [ ] Validate scheduler restart/misfire behavior under container restarts.
      Purpose: ensure jobs behave correctly after downtime/restart events.
      Done when: no duplicate or stale executions occur.

- [ ] Define go/no-go redeploy gate and rollback command sequence.
      Purpose: make deployment decisions explicit, auditable, and reversible.
      Done when: deployment checklist and rollback commands are tested and documented.

- [ ] Define the post-deployment refinement loop: what to keep, what to change, what to improve after each paper cycle.
      Purpose: turn paper trading into a disciplined learning machine instead of a one-off experiment.
      Done when: each cycle ends with a structured review and a concrete next iteration plan.

## Security Focus

- [ ] Secrets hygiene check for `.env`, logs, and commits.
      Purpose: prevent credential leakage in git history, CI, and runtime logs.
      Done when: no secrets are exposed and templates remain sanitized.

- [ ] Access hardening for Hetzner services (SSH and dashboard path).
      Purpose: reduce attack surface while deployment is paused and after relaunch.
      Done when: only required ports/services are reachable.

## Design and Operability Focus

Design here means frontend design first (dashboard UX, clarity, hierarchy, usability), not only infra design.

- [ ] Improve startup flow design for reliability.
      Purpose: make service dependencies clear and deterministic.
      Done when: startup order and health semantics are simple and predictable.

- [ ] Improve frontend/dashboard design for decision-making clarity.
      Purpose: make weekly decisions faster and less error-prone through better visual hierarchy and information density.
      Done when: key actions and risk signals are understandable in one quick scan.

- [ ] Improve observability design (logs and health signals).
      Purpose: make failures understandable in under 2 minutes.
      Done when: each failure class has a clear log signature and next action.

- [ ] Improve deployment UX design (single readiness command).
      Purpose: make go/no-go obvious and repeatable for future deploys.
      Done when: one command returns pass/fail with concise reasons.

## Future Improvements Queue

- [ ] Add post-deploy verification checklist (health + auth + dashboard + scheduler).
- [ ] Add CI guard for deployment readiness checks (non-secret parts).
- [ ] Add monthly evidence review against IBKR docs to keep assumptions current.
- [ ] Add a weekly keep/change/improve review template for deployed paper cycles.
