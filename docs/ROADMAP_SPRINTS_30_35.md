# LeanEcon v3 Roadmap: Sprints 30-35

**Purpose:** move from a local alpha checkpoint to a deployable and expandable
LeanEcon v3 system without blurring release-reliable and frontier capabilities.

## Sprint 30: Local Release Candidate

Primary outcome: decide whether the current alpha is deployable.

Focus:

- run deterministic tests, fast Lean gate, tier 1 release evaluation, and small
  API smoke locally
- audit generated artifacts, logs, frontier queues, and benchmark reports
- fix only release-blocking bugs or severe documentation mismatches
- defer Railway until the local release candidate is green

Do not expand the preamble or change frontier strategy in this sprint.

## Sprint 31: Evaluation Modes And Budgets

Primary outcome: make capability ceilings explicit and configurable.

Focus:

- define release, frontier, and research budget profiles
- make timeout and tool-call policies visible in CLI/API outputs
- preserve current conservative defaults for release gates
- add a simple frontier queue review workflow

Expected result: users can tell whether a failure is a capability gap, a budget
limit, or a tooling/provider issue.

## Sprint 32: Lean Build And Preamble Operations

Primary outcome: make Lean infrastructure predictable.

Focus:

- separate fast edit-loop gates from full release-image gates
- pre-warm or cache Lake/Mathlib artifacts in CI/deployment images
- split the preamble into stable release modules and experimental frontier
  modules if build cost or API clarity requires it
- document which commands are developer checks, release checks, and deployment
  checks

Expected result: Lean build cost is managed by infrastructure rather than
handled ad hoc during proof attempts.

## Sprint 33: Agentic Frontier Learning Loop

Primary outcome: turn frontier failures into useful training and engineering
data.

Focus:

- review frontier queue records by failure class and next action
- identify high-value missing preamble theorems and formalizer templates
- improve planner/formalizer/prover contracts only where traces show repeated
  gaps
- keep release metrics separate from exploratory improvements

Expected result: a data flywheel that prioritizes preamble polishing, prompt
changes, retrieval additions, or fine-tuning candidates.

## Sprint 34: Deployment Hardening

Primary outcome: redeploy safely once local gates justify it.

Focus:

- rebuild Railway or equivalent hosting with explicit environment variables
- validate `/health`, `/metrics`, job queue behavior, and one small proof smoke
- ensure Lean/LSP modes are observable in production
- document deployment limits and rollback steps

Expected result: hosted alpha reflects the same scope and reliability story as
local evaluation.

## Sprint 35: Coverage Expansion

Primary outcome: broaden undergraduate mathematical economics coverage without
weakening release honesty.

Focus:

- add preamble definitions/theorems from repeated frontier gaps
- promote claims into the release denominator only after deterministic evidence
- add benchmark examples for consumer theory, producer theory, games, dynamic
  programming, and constrained optimization
- preserve clear tiers: reliable, supported attempt, frontier, out of scope

Expected result: a larger reliable surface, not a larger unsupported promise.
