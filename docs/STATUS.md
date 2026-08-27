# Project Status

This file is the durable gate handoff. Read it with [the specification](SPEC.md) before making changes.

## Gate 0 — Scope and repository rules

**State:** Complete

**Checkpoint:** `bcf48cc` (`gate 0: define red-team lab specification`)

### Deliverables

- `AGENTS.md`
- `docs/SPEC.md`
- `docs/STATUS.md`
- `docs/ADR-001-agent-boundary.md`
- `.gitignore`

### Validation

Performed on 2026-08-23 from the repository root:

- Workspace scope check using `rg --files -uu`: PASS; exactly the five permitted workspace files exist outside `.git`.
- Local Markdown-link check using PowerShell path resolution: PASS; every local link resolves.
- `Get-Command markdownlint-cli2, markdownlint`: neither Markdown linter is installed, so no repository Markdown linter was available.
- `git diff --check`: PASS (exit code 0).
- `git diff --no-index --check` for each untracked deliverable: initial run found three trailing-space hard breaks; they were removed and the final run passed with no whitespace errors.
- `git status --short`: PASS; shows only the five intended untracked deliverables (`.gitignore`, `AGENTS.md`, and the three files under `docs/`).

### Assumptions and decisions

- The active Codex session is in Default mode, so Gate 0 uses an explicit written plan rather than Codex Plan mode.
- “Gate 0 in progress” describes the initial state; this record will change to complete only after validation.
- yfinance is limited to explicit ingestion/manual smoke testing. Tests and evidence replay are network-isolated.
- Later gate definitions were not included in the Gate 0 brief and are not invented here.

### Blockers

None.

## Gate 1 — Bootstrap tooling and typed domain contracts

**State:** Complete

### Deliverables

- `pyproject.toml` with editable packaging, pytest/coverage, Ruff, and strict mypy configuration.
- `src/strategy_redteam/__init__.py`, `domain.py`, and `py.typed`.
- `tests/test_contracts.py` and two small deterministic JSON fixtures under `tests/fixtures/`.
- Pydantic v2 contracts for `DataManifest`, `StrategySpec`, `FailureRule`,
  `ExperimentSpec`, `StressComponent`, `StressScenario`, `AttackBatch`, `MetricSet`,
  `FailureBreach`, `StressResult`, `DefenderVerdict`, and `FailureReport`.

### Validation

Performed on 2026-08-23 from the repository root:

- `python --version`: unavailable on the session `PATH`.
- `& 'C:\Users\61450\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' --version`:
  PASS; the available bundled runtime is Python 3.12.13.
- `& 'C:\Users\61450\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv`:
  PASS; created the ignored local environment.
- `& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'`: the initial sandboxed
  run could not reach the package index; the approved network-enabled retry passed and installed
  `strategy-redteam` in editable mode with its declared development dependencies.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 45 passed in 0.62 seconds,
  87% branch-aware coverage, with no skips or expected failures.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: the first run found ten import/enum
  style findings; after mechanical fixes the final run passed with `All checks passed!`.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in two source files.
- `git diff --check`: PASS (exit code 0).
- `git -c core.autocrlf=false diff --no-index --check -- NUL <file>` applied to every
  untracked Gate 1 file: PASS; no whitespace errors.
- `git status --short`: PASS; shows only the intended Gate 1 additions and this status update.

### Assumptions and decisions

- The package targets Python 3.11 through `requires-python`, Ruff, and mypy configuration. The only
  Python available in this environment is 3.12.13, so local acceptance ran on that compatible
  runtime rather than claiming a 3.11 execution.
- Gate 1 uses the requested name `DataManifest` for the specification's dataset-manifest concept.
  The manifest-file hash remains outside that self-hashed document as required by the specification.
- The specification requires bounded component lists and sustained durations but does not assign
  those two values. Gate 1 records `MAX_COMPONENTS_PER_SCENARIO=8` and
  `MAX_STRESS_DURATION_ROWS=252`; changing either requires an explicit later decision.
- Failure breaches store adverse observations as positive magnitudes so their positive thresholds
  and normalized excess have one unambiguous representation.
- Narrative fields preserve shell-like and prompt-injection text verbatim as inert data. No download,
  backtest, transform execution, agent, storage adapter, or Azure implementation was added.

### Blockers

None.

## Gate 2 — Historical data layer

**State:** Complete

### Deliverables

- `src/strategy_redteam/data.py` with the injected `DataProvider` protocol, explicit
  `YFinanceDataProvider`, vectorized canonical validation, typed failures, deterministic Parquet
  hashing, immutable local store, canonical JSON manifest serialization, and verified cache reuse.
- `src/strategy_redteam/cli.py` and `__main__.py` with separate `data download` and
  `data validate` commands, plus the installed `strategy-redteam` entry point.
- Extended `DataManifest` fields for requested dates and the explicit missing-data policy, with
  Parquet media type; updated Gate 1 manifest fixture and helper.
- `tests/test_data.py` with tiny fixed `FakeDataProvider` fixtures covering normalized output,
  symbol ordering, duplicate and non-monotonic dates, missing/non-finite/out-of-range values,
  requested-period preservation, stable hashes, manifest provenance, tamper detection, verified
  cache reuse, provider failure, and repeatable CLI validation.
- `pyarrow` as the Parquet runtime dependency.

### Validation

Performed on 2026-08-23 from the repository root:

- `& '.\.venv\Scripts\python.exe' -m pip install -e .`: the initial sandboxed run could not
  reach the package index; the approved network-enabled retry passed and installed
  `pyarrow 25.0.1` plus the updated editable package.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 60 passed in 4.28 seconds, 79%
  branch-aware coverage, with no warnings, skips, expected failures, or network access.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in five source files.
- `& '.\.venv\Scripts\strategy-redteam.exe' data download --help` and the corresponding
  `data validate --help`: PASS; both installed commands resolve and document their contracts.
- `git diff --check`: PASS (exit code 0).
- `git status --short`: PASS; shows only the intended uncommitted project files and status update.

### Assumptions and decisions

- `DataManifest.start_date` and `end_date` remain the actual inclusive observed market dates;
  `requested_start_date` and `requested_end_date` record the inclusive caller request separately.
- The canonical dataset is a UTC-midnight `DatetimeIndex` with symbol-major adjusted `open`,
  `high`, `low`, and `close` columns plus `volume`, all stored as finite `float64`. Its calendar
  policy rejects non-monotonic or duplicate dates, and its explicit missing-data policy is
  `reject`; no fill or calendar intersection occurs.
- yfinance uses an exclusive API end boundary, so the adapter sends the day after the caller's
  inclusive end date, then rejects any returned observation outside the original requested range.
- Dataset objects are named by the SHA-256 of deterministic Parquet bytes. Request-keyed canonical
  manifests and content objects use create-once writes; the manifest self-hash is calculated and
  reported but remains external to the manifest as specified.
- Acceptance ran on the available Python 3.12.13 environment while package metadata continues to
  target Python 3.11 and static analysis uses the Python 3.11 target.
- The live SPY/TLT yfinance smoke test remains the explicitly separate manual action and is not
  part of pytest or Gate 2's deterministic acceptance run.

### Blockers

None.

## Gate 3 — Strategy interface and deterministic baseline backtest

**State:** Complete

### Deliverables

- `src/strategy_redteam/strategy.py` with the aligned `Strategy` protocol, drift-aware monthly
  60/40 SPY–TLT strategy, strict `CSVWeightsStrategy`, typed failures, and explicit opt-in handling
  for short, leveraged, or missing external weights.
- `src/strategy_redteam/backtest.py` with one documented close-to-next-return timing shift,
  vectorized effective weights, pre-trade drift, turnover, basis-point costs, asset contributions,
  net returns, equity, `MetricSet`, and all three configurable failure-rule evaluations.
- Extended `StrategySpec` permission fields and `ExperimentSpec.transaction_cost_bps`, all with safe
  defaults; public Gate 3 exports in `src/strategy_redteam/__init__.py`.
- `strategy-redteam baseline <experiment> <manifest> [--weights-csv ...]`, which validates the
  immutable local dataset against the `ExperimentSpec` before producing canonical JSON evidence.
- `tests/test_backtest.py` with exact hand calculations, look-ahead sentinel, rebalance-date,
  invalid CSV, permission, exact metric/breach-date, source-vectorization, and CLI tests.

### Validation

Performed on 2026-08-23 from the repository root:

- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 74 passed in 7.60 seconds,
  79% branch-aware package coverage, including 86% for `backtest.py` and 75% for `strategy.py`,
  with no warnings, skips, expected failures, placeholders, or network access.
- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_backtest.py`: PASS; all 14 Gate 3 tests
  passed in 6.94 seconds and reported the same per-engine-file coverage.
- A separately targeted pytest-cov form using `--cov=strategy_redteam.backtest` and
  `--cov=strategy_redteam.strategy` failed during collection with NumPy's Windows-only
  `cannot load module more than once per process` loader error. The configured package-level
  coverage commands above ran cleanly and produced per-file engine coverage, so no test was lost.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in seven source files.
- `& '.\.venv\Scripts\strategy-redteam.exe' baseline --help`: PASS; the installed command
  resolves and documents the experiment, manifest, and external-weight inputs.
- `git diff --check`: PASS (exit code 0); Git emitted only the existing CRLF conversion warning for
  `docs/STATUS.md`.
- `git status --short`: PASS; shows the prior uncommitted Gate 1–2 files, `.data-cache/`, the Gate 3
  additions, and this status update. No commit was created.

### Assumptions and decisions

- Gate 3's explicit transaction-cost request adds a configurable exception to the specification's
  frictionless baseline; its default remains zero basis points. Turnover is total absolute traded
  asset notional, initial funding is 100% turnover, and costs reduce the return on the effective
  date without changing relative asset-price drift.
- Daily monthly-strategy targets are the naturally drifted holdings between decision dates and
  reset to 60/40 at the initial and first observed close of each new calendar month. The serialized
  frequency is `month_start`. This prefix-invariant schedule never treats a truncated dataset's
  final mid-month row as a rebalance and preserves monthly—not daily—rebalancing.
- The no-return first dataset row is excluded from `MetricSet.observation_count` and all rolling
  windows. Fewer than 20 earned returns produce a deterministic zero worst-rolling metric; rolling
  rules remain non-breached until a complete window exists.
- External dates and cells remain strict by default. Explicit `allow_missing_weights` maps absent
  cells or dates to zero-weight, zero-yield cash and never forward-fills or normalizes a decision.
  Short and leveraged weights require their separate `StrategySpec` permissions.
- Failure rules evaluate net-of-cost portfolio returns. A baseline volatility-multiple comparison
  uses the same baseline path, and every zero or unavailable denominator window is disclosed.
- Acceptance ran on available Python 3.12.13 while package and static-analysis targets remain 3.11.
- No attack, scenario transform, LLM/agent, report, broker, live-trading, or Azure code was added.

### Blockers

None.

## Gate 4 — Deterministic historical attack discovery

**State:** Complete

### Deliverables

- `src/strategy_redteam/historical.py` with a single-baseline historical scanner using NumPy
  sliding-window views for configured 20-, 60-, and 126-earned-return windows, deterministic
  ranking, transitive overlap de-duplication, and bounded `top_k` evidence construction.
- `HistoricalWindowEvidence` with exact window start/end, first breach, loss start, trough, and
  recovery dates plus asset returns, annualized realized volatilities, SPY–TLT correlation,
  turnover, transaction cost, and peak-to-trough linked loss contributions.
- Extended `ExperimentSpec` historical-window configuration and `StressResult` provenance for the
  dataset ID, strategy ID, configuration hash, data hash, code version, and scanner version.
- Public Gate 4 exports in `src/strategy_redteam/__init__.py` and deterministic constructed-return
  tests in `tests/test_historical.py`; updated Gate 1 result fixtures for the stricter provenance
  contract.

### Validation

Performed on 2026-08-23 from the repository root:

- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_historical.py`: PASS; all 5 Gate 4 tests
  passed in 4.43 seconds, covering known worst/onset/trough/recovery dates, a known correlation
  regime change, exact asset loss contributions, all configured lengths, overlap de-duplication,
  bounded selection, no-failure output, provenance rejection, repeatability, and source
  vectorization checks.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 79 passed in 7.44 seconds with 81%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, or network
  access. `historical.py` reported 88% coverage.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in eight source files.
- `git diff --check`: PASS (exit code 0); Git emitted only the existing CRLF conversion warning for
  `docs/STATUS.md`.
- The first PowerShell aggregation of `git -c core.autocrlf=false diff --no-index --check -- NUL
  <file>` incorrectly treated Git's expected “files differ” exit code as a whitespace failure for
  all five untracked Gate 4 files. The corrected diagnostics-based aggregation passed: none of the
  files produced a whitespace diagnostic.
- `git status --short`: PASS; shows the prior uncommitted project files and `.data-cache/`, the Gate
  4 additions, and this status update. No commit was created.

### Assumptions and decisions

- A configured historical window contains exactly 20, 60, or 126 earned portfolio-return rows;
  the dataset's initial no-return row is never counted. The three lengths are an exact serialized
  `ExperimentSpec` tuple, and the existing bounded `top_k` selects at most three results.
- Candidates are ranked by the specification's result ordering. Transitively overlapping failing
  intervals describe one historical episode; only that episode's strongest candidate survives.
- Drawdown state resets to initial wealth at each candidate start. If the deepest drawdown's prior
  high-water mark predates the first selected return, `loss_start_date` is the selected start date.
  Recovery is the first later observed market date that regains that high-water mark within numeric
  tolerance, including dates after the selected window; it is `None` if the dataset ends first.
- Asset loss contributions use exact wealth-linked attribution from the high-water mark through the
  trough. Asset contributions plus the linked transaction-cost contribution reconcile to the
  portfolio loss at the trough. Turnover and transaction cost are summed over the full window.
- Asset realized volatility is annualized sample standard deviation using `sqrt(252)`; correlation
  uses the selected simple-return rows and is `None` for a zero-variance asset. Because historical
  windows are unchanged baseline paths, an evaluable volatility multiple is exactly one.
- Acceptance ran on the available Python 3.12.13 environment while package and static-analysis
  targets remain Python 3.11. No synthetic transform, LLM/agent call, cloud code, CLI expansion,
  broker, recommendation, or live-trading integration was added.

### Blockers

None.

## Gate 5 — Deterministic synthetic stress transforms

**State:** Complete

### Deliverables

- Extended `StressComponent` with the optional, execution-only
  `transaction_cost_multiplier` family and added typed `AssetReturnSummary`, `ReturnSummary`, and
  `ComponentTransformSummary` evidence contracts. Full drawdown breaches now retain explicit trough
  and recovery dates.
- `src/strategy_redteam/stress.py` with pure one-day gap, sustained cumulative shock,
  volatility-multiplier, symmetric correlation-target, and transaction-cost transforms plus
  atomic ordered scenario composition.
- `StressTransformResult` retaining private baseline and stressed return copies, ordered component
  families, identical-window component and scenario pre/post statistics, before/after execution
  assumptions, deterministic seed, and canonical byte/hash serialization.
- `run_backtest_with_asset_returns` and `run_stressed_backtest` for complete-dataset stressed replay,
  including stressed monthly holding drift, execution-layer costs, and failure/recovery evaluation
  after the scenario window without constructing or persisting a synthetic dataset.
- Public Gate 5 exports in `src/strategy_redteam/__init__.py` and fixed, network-free acceptance
  fixtures in `tests/test_stress.py`.

### Validation

Performed on 2026-08-23 from the repository root:

- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_stress.py`: PASS; all 15 Gate 5 tests
  passed in 4.12 seconds. They cover exact transform scope, realized cumulative shock,
  mean-preserving log-volatility scaling, restored marginals and target correlation, execution-only
  costs, explicit transform order, inert narrative text, unchanged input/hash, byte-equivalent
  replay, identical component/scenario summary windows, variance/eigenvalue tolerance rejection,
  singular/ill-conditioned matrices, atomic composite failure, stressed monthly drift, and delayed
  full-path breach/trough/recovery dates.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 94 passed in 5.38 seconds with 80%
  branch-aware package coverage, including 79% for `stress.py` and 83% for `backtest.py`, with no
  warnings, skips, expected failures, placeholders, or network access.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`. An earlier
  targeted check identified one unused test import and one overlong assertion; both were fixed
  before the final run.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in nine source files.
- `git diff --check`: PASS (exit code 0); Git emitted only the existing CRLF conversion warning for
  `docs/STATUS.md`.
- `git -c core.autocrlf=false diff --no-index --check -- NUL <file>` for the two untracked Gate 5
  files: PASS; neither file produced a whitespace diagnostic.
- `git status --short`: PASS; shows the prior uncommitted project files and `.data-cache/`, the Gate
  5 additions, and this status update. No commit was created.

### Assumptions and decisions

- Synthetic transforms consume a validated, UTC-normalized simple-return frame containing exactly
  SPY and TLT. Every value must be finite and strictly above `-1`; dates and source column order are
  retained exactly.
- Ordered composition is sequential: each component consumes the private output of its predecessor.
  The documented volatility mean and correlation marginal conventions therefore refer to the
  immediate pre-component log-return window. Static preflight plus a private sequential dry run
  validates every component before the evidence-producing pass; only the fully successful result is
  exposed and no transform function persists data.
- Volatility uses sample log-return standard deviation (`ddof=1`) and requires at least two rows.
  Correlation uses sample-standardized innovations and requires at least three rows. A variance or
  correlation eigenvalue at or below `numeric_tolerance` is treated as numerically zero or
  ill-conditioned and rejected, never clipped or approximated.
- Correlation targeting constructs the fixed MVP `2x2` SPY/TLT matrix from the schema's scalar
  target, validates symmetry, unit diagonal, positive semidefiniteness, strict nonsingularity, and
  conditioning, then uses unique symmetric eigendecomposition roots for whitening and coloring.
- At most one transaction-cost multiplier is allowed in a scenario. It scales basis-point execution
  assumption only and is consumed when the stressed backtest calculates execution costs. It must
  leave market returns byte-identical and keep the result in `[0, 10000)` basis points.
- Every component audit uses the same resolved rows before and after its transform: the selected date
  for a gap, resolved duration for a sustained shock, declared window for volatility/correlation,
  and scenario window for an execution-only component. Scenario pre/post summaries use the same
  inclusive evaluation window.
- Scenario windows bound transformations and summaries, not portfolio replay. The baseline and
  stressed paths run over the complete immutable dataset; fixed monthly holdings drift from the
  stressed return path, and rolling failures plus drawdown onset, trough, and recovery may occur
  after the scenario window. The recorded seed is retained even though transforms use no randomness.
- The shared engine default `DEFAULT_NUMERIC_TOLERANCE` is exactly `1e-9`. `ExperimentSpec`
  requires an explicit configurable value, and its canonical configuration hash therefore commits
  to that value. Gate 5 now also retains the exact value in `StressTransformResult` and its canonical
  transform hash. Variance, matrix symmetry/diagonal, eigenvalue, conditioning, and realized-target
  checks use this same value; there are no separate hidden variance or eigenvalue tolerances.
- Acceptance ran on available Python 3.12.13 while package metadata and static-analysis targets
  remain Python 3.11. No LLM, attacker orchestration, Azure/cloud code, broker integration,
  recommendation, scenario ranking, or later-gate implementation was added.

### Blockers

None.

## Gate 6 — Model-free bounded attack runner and evidence artifacts

**State:** Complete

### Deliverables

- `src/strategy_redteam/attack.py` with strict `AttackBudget` runtime accounting, monotonic
  deadline checks, versioned safe-YAML `AttackPolicy`, policy range rejection without clamping,
  a seeded `DeterministicOfflineProposer`, schema validation, cross-round ID and semantic
  de-duplication, bounded batch evaluation, documented tuple ranking, and evidence-condition stop.
- `src/strategy_redteam/artifacts.py` with engine-linked worst-window contribution evidence, the
  fixed failure-report template, exact artifact membership and cross-file verification, SHA-256
  indexing, hidden-directory staging, and atomic publication of the complete seven-file bundle.
- `config/attack-policy-v1.yaml`, the public Gate 6 exports, a typed timeout rejection code, and
  PyYAML as the only new runtime dependency.
- `tests/test_attack.py` with fixed network-free coverage of every hard/configured budget, proposer
  overflow, deadline arrival before and during evaluation, schema/policy rejection, no clamping,
  semantic de-duplication, early stop, bounded ranking, baseline reuse, deterministic offline
  proposals, stable bundle bytes, atomic-write failure, artifact tampering, and an honest
  no-failure report.

### Validation

Performed on 2026-08-23 from the repository root:

- `& '.\.venv\Scripts\python.exe' -m pip install -e .`: the initial sandboxed run could not
  reach the package index; the approved network-enabled retry passed, installed `PyYAML 6.0.3`,
  and refreshed the editable package without adding any model, agent, cloud, or Azure SDK.
- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_attack.py`: PASS; all 15 Gate 6 tests
  passed in 8.69 seconds, including timeout evidence discard and atomic artifact verification.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 109 passed in 10.89 seconds with 80%
  branch-aware package coverage, no warnings, skips, expected failures, placeholders, or network
  access. `attack.py` reported 81% coverage and `artifacts.py` reported 73%.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in 11 source files.
- `git diff --check`: PASS (exit code 0); Git emitted only the existing CRLF conversion warning for
  `docs/STATUS.md`.
- `git -c core.autocrlf=false diff --no-index --check -- NUL <file>` for every untracked file
  changed by Gate 6: PASS; no whitespace diagnostics were emitted.
- `git status --short`: PASS; shows the prior uncommitted Gate 1–5 work and `.data-cache/`, the
  intended Gate 6 additions/updates, and this status entry. No commit was created.

### Assumptions and decisions

- Gate 6 is entirely local and model-free. It adds no LLM SDK, Hosted Agent, Azure, broker,
  recommendation, or live-trading integration. Historical-window discovery remains exclusively in
  the Gate 4 scanner; the Gate 6 YAML allow-list covers the five Gate 5 synthetic families.
- The monotonic deadline covers baseline, proposal, and evaluation work. Checks occur at bounded
  operation boundaries; an in-flight deterministic vectorized evaluation is allowed to return, but
  its evidence is discarded and replaced by a typed timeout result if it finishes after deadline.
- Invalid, duplicate, and timed-out proposals consume their already reserved candidate slots.
  Early termination is evaluated after the current batch so a returned batch is handled atomically.
- Semantic identity contains only the ordered numeric components and evaluation window; scenario
  ID, hypothesis, and headline are excluded. Component order remains semantic because Gate 5
  composition is explicitly ordered.
- The baseline runs once. Each candidate transforms its private return copy and reuses baseline
  portfolio returns for failure comparison; no candidate invokes the unchanged baseline again.
- `experiment.json` is the bundle index written after the other six files in hidden staging. The
  directory becomes visible only by atomic rename after exact file, hash, schema, count,
  provenance, ranking, and report-notice verification. A timed-out bundle is artifact-complete but
  records `attack_completed=false` and `stop_reason=timeout`.
- Worst-window asset and transaction-cost contributions use deterministic wealth-linked
  attribution and reconcile to the compounded portfolio return. Reports show the documented
  severity tuple but no aggregate score, and state that Gate 6 defender replay was not run.
- Acceptance ran on available Python 3.12.13 while package metadata and static analysis continue
  to target Python 3.11.

### Blockers

None.

## Pre-Gate-7 audit — Gates 0–6 checkpoint

**State:** Audit complete; checkpoint commit pending

**Checkpoint:** All audited Gates 1–6 files are staged as one honest combined checkpoint. The commit
is pending explicit in-chat confirmation because repository approval did not recognize the commit
instruction contained in the attached audit request.

### Deliverables

- Added `.data-cache/` to `.gitignore`; downloaded Parquet objects and manifests remain local and
  ignored, while deterministic fixtures remain trackable.
- Replaced the prefix-dependent last-observed-row monthly schedule with the explicitly approved
  first-observed-close schedule and serialized `month_start` frequency; updated the specification,
  contracts, and affected fixtures.
- Added a prefix-invariance regression proving that extending data cannot change earlier rebalance
  decisions, target/effective weights, returns, turnover, or transaction costs, and that a truncated
  mid-month endpoint is not a rebalance.
- Recorded the exact shared `1e-9` numeric tolerance and added tests proving both experiment config
  hashes and Gate 5 transform hashes change when the configured tolerance changes.
- Performed the explicit real-yfinance download, validation, repeat-request cache-hit, and ignore
  smoke test outside pytest. No downloaded data was staged.

### Validation

Performed on 2026-08-23 from the repository root:

- `git check-ignore -v .data-cache/`: PASS; `.gitignore:37:.data-cache/` is the matching rule.
- `git status --short --ignored`: PASS; `.data-cache/` appears only with `!!` and never as a staged
  or untracked commit candidate.
- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_backtest.py tests/test_stress.py
  tests/test_attack.py tests/test_contracts.py`: PASS; 92 passed in 17.71 seconds.
- `& '.\.venv\Scripts\strategy-redteam.exe' data download --start 2020-02-03 --end
  2020-02-10`: PASS; `cache_status=downloaded`, six rows, data SHA-256
  `e19896c707ea6b263aba8f4b19031ae0f7e9304dcb370ce248ec09881ee20319`, and manifest SHA-256
  `08712f16204844ed7ec9b16f1bde1749bff607a72604ed23160ad79c66a82f1c`.
- `& '.\.venv\Scripts\strategy-redteam.exe' data validate
  '.data-cache\manifests\c94c2851272bdc274cc329ee8f649f781b40ebe8b401a31515712cbc096ea0f5.json'`:
  PASS; `validation=passed` with the same dataset and manifest hashes.
- Repeating the identical `data download` command: PASS; `cache_status=verified_hit` with the same
  dataset ID, data hash, manifest hash, and manifest path.
- `git check-ignore -v` on the downloaded `.parquet` and manifest `.json`: PASS; both resolve to
  `.gitignore:37:.data-cache/`. `git status --short` contains neither file.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 112 passed in 16.41 seconds with 80%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, or network
  access. The yfinance check remained separate from pytest.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in 11 source files.
- `git diff --check`: PASS (exit code 0); Git emitted only line-ending conversion warnings.
- `git diff --no-index --check -- NUL <file>` over all 22 new files: PASS; no whitespace
  errors.

### Assumptions and decisions

- The user explicitly approved the first observed trading day of a new month as the causal fix. The
  decision is made at that close and first affects the next observed return under the unchanged
  one-row execution lag.
- `numeric_tolerance` is a single absolute tolerance by design; no implicit per-operation values
  exist. It remains required in `ExperimentSpec`, configurable within `(0, 1)`, and provenance-bound.
- The live smoke data is operational cache, not evidence committed to Git. Its immutable manifest
  and content hashes are recorded above solely as the manual adapter-check result.
- Python 3.11 execution and logical-data hashing or exact PyArrow pinning remain recommended before
  Azure deployment; they are not blockers for this pre-Gate-7 audit and no Azure work was started.

### Blockers

- The repository approval layer rejected `git commit` because the commit instruction was supplied
  in an attachment rather than directly in chat. No technical or test blocker remains.

## Gate 7 — Local attacker/defender application boundaries

**State:** Complete

### Deliverables

- `src/strategy_redteam/services.py` with a JSON-only `ScenarioProposer`, compact bounded
  `AttackerEvidenceSummary`, fixed-template `AttackerService`, independently reloading and replaying
  `DefenderService`, strict provenance/result/event/hash comparison, causal-claim auditing, and a
  Markdown renderer whose numbers come only from replayed structured evidence.
- Deterministic `FakeScenarioProposer` and `FakeReportWriter` response queues with no network or
  model dependency, bounded raw-response handling, and typed `verified`, `contradicted`, or
  `unverifiable` causal assessments.
- External `prompts/attacker.md` and `prompts/defender.md` templates that preserve the agent/engine
  authority boundary, forbid invented P&L and operational use of narrative, require policy-bounded
  mechanism diversity, and constrain report prose to independently replayed evidence.
- Extended defender contracts for exact transform/event/result replay checks and honest reports
  without baseline metrics when immutable provenance cannot be verified; public Gate 7 exports and
  a compact public return-summary helper.
- `tests/test_services.py` with deterministic local coverage of malformed JSON, proposer overflow,
  semantic duplicates, fabricated numeric claims, unsupported causation, prompt-injection-like
  headline text, replay mismatch, changed dataset hash, timeout, verified failure, trusted-only
  report paths, and absence of code execution.

### Validation

Performed on 2026-08-23 from the repository root:

- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 122 passed in 9.62 seconds with 80%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access. `services.py` reported 80% coverage.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in 12 source files.
- `git diff --check`: PASS (exit code 0); the final check emitted only line-ending conversion
  warnings for `docs/STATUS.md`, four existing tracked source paths, and `tests/test_contracts.py`.
- `git -c core.autocrlf=false diff --no-index --check -- NUL <file>` for each of the four untracked
  Gate 7 files: PASS; Git returned its expected “files differ” exit code and no whitespace
  diagnostics for either prompt, `services.py`, or `test_services.py`.
- `git status --short`: PASS; shows the staged Gates 0–6 checkpoint, intended Gate 7 modifications,
  and the new prompt, service, and test paths. No commit was created.

### Assumptions and decisions

- `ScenarioProposer` receives only one aggregate SPY/TLT return summary, failure rules, policy,
  seed/budget context, and at most 16 prior valid result summaries. It receives neither daily price
  history nor any filesystem path. Responses are capped at 262,144 UTF-8 bytes and must validate as
  the requested `AttackBatch`; malformed batches consume a typed rejection slot and overflow stops
  before candidate iteration.
- `AttackerService` adapts the untrusted JSON client to the already accepted Gate 6 fixed-budget
  runner, which remains the owner of validation, semantic de-duplication, deterministic evaluation,
  deadline enforcement, ranking, and artifact publication.
- The defender's manifest and optional external-weight paths are trusted application inputs, never
  model fields. The defender reconstructs the strategy, baseline, and each top scenario from a fresh
  immutable-store load; it performs at most `TOP_K=3` replays.
- Numeric result fields use the experiment's single absolute tolerance. Dataset/config/scenario and
  transform hashes plus rule/event identities and dates compare exactly. A provenance mismatch is
  `invalid_evidence`; a deterministic replay mismatch is `not_reproduced`.
- The report writer returns causal assessments only. Unsupported typed mechanisms are rejected, raw
  attacker/report-writer prose is never rendered, and the application inserts every Markdown number
  from independently replayed `MetricSet`, breach, transform, contribution, budget, or provenance
  fields.
- Acceptance ran on available Python 3.12.13 while package metadata and static analysis continue to
  target Python 3.11. No Azure/cloud SDK, live model, network call, broker, recommendation, or
  live-trading integration was added.

### Blockers

None.

## Gate 8 — Complete local/offline vertical slice

**State:** Complete

### Deliverables

- `src/strategy_redteam/offline.py` with a strict dataset-independent YAML config, dataset binding
  only after immutable validation, deterministic JSON-only offline attacker/report clients, one
  baseline-to-attack-to-defence flow, exact hash continuity, replay enforcement, and atomic
  publication/verification of a twelve-file final bundle containing the intact nested attacker
  bundle plus typed defender verdicts, replay records, JSON/Markdown verified reports, and a final
  hash index.
- `redteam run --experiment <yaml> --dataset <manifest> --mode offline [--output <directory>]`
  plus the existing `strategy-redteam` alias. Schema, dataset hash, replay, interrupted attack,
  existing-output, and incomplete-artifact failures return nonzero; no Azure or model client exists.
- `config/example_60_40.yaml` with fixed 3/8/24/3 hard budgets and a bounded deterministic policy
  composing volatility, correlation, and sustained-shock components in declared order.
- A committed immutable Parquet fixture and canonical manifest under
  `tests/fixtures/offline-cache/`, plus `scripts/build_offline_fixture.py`. The known regime break
  begins on 2024-02-28: the first forty earned rows have correlation below `-0.99`, the next forty
  have correlation above `0.99`, and both later sample volatilities exceed four times the earlier
  values.
- `tests/test_offline.py` covering exact artifact names and bytes across repeated runs, dataset hash
  continuity, fixed/hard budgets, three ranked replays, fully reproduced verdict checks, known rule
  onsets, required report fields and causal chain, plus nonzero schema/hash/replay/incomplete exits.
- Strengthened fixed defender prose in `services.py`: its causal explanation uses only replay JSON
  evidence and explicitly links increased sleeve volatility, negative-to-positive SPY/TLT
  correlation, sleeve contributions, portfolio loss, named rule onset, and independent replay.
- `README.md` limited to local setup, the fixture demo/report review, and a separately invoked
  yfinance download followed by an offline cached-SPY/TLT run. It contains no asserted live values.

### Validation

Performed on 2026-08-23 from the repository root:

- `& '.\.venv\Scripts\python.exe' -m pip install -e . --no-deps`: the sandboxed build-isolation
  attempt could not reach the setuptools index. A `--no-build-isolation` retry also failed because
  setuptools was not importable inside the venv. The approved network-enabled retry passed and
  refreshed both local entry points without adding a runtime dependency.
- `& '.\.venv\Scripts\redteam.exe' run --help`: PASS; the command documents required experiment
  YAML and canonical manifest inputs, offline-only mode, and a new immutable output directory.
- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_offline.py`: the first complete Gate 8
  version passed all six tests. After tightening the transform to show negative-to-positive
  correlation, one exact expected rolling-loss onset changed by one observed row; the test was
  updated to the engine evidence and the final targeted run passed all six tests in 16.41 seconds.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 128 passed in 18.42 seconds with 80%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`. One new test import
  ordering diagnostic was mechanically fixed before the final run.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in thirteen source files.
- `& '.\.venv\Scripts\redteam.exe' run --experiment config/example_60_40.yaml --dataset
  tests/fixtures/offline-cache/manifests/correlation-break.json --mode offline --output
  artifacts/gate8-offline-fixture`: PASS with exit code 0; `candidate_slots_consumed=8`,
  `top_failures=3`, `replayed=3`, and `verified_failures=3`. Data SHA-256 was
  `6fa7e4f25f08fa8de56b09b1ba54a29a0f183baa793568ad9b55e870fb772c4d`; config SHA-256 was
  `40886cbee4bffc70cf241991184bf525d8bafb8e25756ce6fa95fc1995f70e1f`.
- The generated files were exactly `offline_run.json`, `defender_verdicts.json`,
  `replay_results.jsonl`, `failure_report.json`, `failure_report.md`, and the seven required files
  under `attack/`: `experiment.json`, `dataset_manifest.json`, `policy.json`,
  `proposed_scenarios.jsonl`, `results.jsonl`, `top_failures.json`, and `failure_report.md`.
- `verify_offline_artifacts(Path('artifacts/gate8-offline-fixture'))`: PASS with three verified
  failures. Human report review passed: the strongest scenario records both volatilities rising,
  correlation changing from negative to positive, both sleeves contributing negatively, the
  rolling-loss onset on 2024-03-04, and defence reproduction from matching hashes. It does not use
  Sharpe-only reasoning, and every rendered number is backed by JSON evidence.
- `git diff --check`: PASS (exit code 0); Git emitted only existing line-ending conversion warnings.
- `git -c core.autocrlf=false diff --no-index --check -- NUL <file>` for every new Gate 8 text file:
  PASS; no whitespace diagnostics were emitted. The committed Parquet fixture was excluded as a
  binary file.
- `git status --short`: PASS; it shows the prior staged Gates 0–6 checkpoint, uncommitted Gate 7
  work, the intended Gate 8 files/updates, and no generated `artifacts/` or cache file. No commit
  was created.

### Assumptions and decisions

- `--dataset` names the canonical manifest JSON, not a mutable CSV or bare Parquet file. The run
  locates the sibling content-addressed dataset, verifies manifest/data bytes and agreement, then
  binds their identifiers into `ExperimentSpec` and its configuration hash.
- The offline proposer is model-free and composes each policy-allowed family in serialized order.
  Volatility and correlation operate on the first half of earned rows, excluding the initial
  no-return row; the sustained shock starts at a deterministic bounded position. Stable inputs,
  config, code, and recorded seed therefore produce byte-identical artifacts.
- The final bundle nests the already accepted exact seven-file attacker bundle under `attack/`.
  The root `failure_report.md` is the independently replayed report; the nested file is explicitly
  the unverified attacker draft. The final index hashes every non-index file and rejects any extra,
  missing, altered, schema-invalid, provenance-discontinuous, or non-reproduced artifact.
- A data-specific config is not needed: the example YAML contains strategy, failure, policy, seed,
  timeout, and budgets, while the verified manifest supplies dataset identity and hash at runtime.
  This permits the same offline flow over a separately downloaded cache without claiming stable
  live market results.
- Acceptance used the available Python 3.12.13 runtime while package metadata and static analysis
  continue to target Python 3.11. The acceptance artifact directory is ignored generated output.
  No Azure/cloud SDK, real model client, broker, recommendation, or live-trading integration was
  added.

### Blockers

None.

## Gate 9A — Attacker hypothesis policy

**State:** Complete; awaiting review before any attacker-agent implementation

### Deliverables

- `docs/ATTACKER_DESIGN.md` with policy version 1.0, the supplied four-hypothesis table, explicit
  applicability, units, inclusive and half-open boundaries, left-to-right component ordering,
  existing Gate 6 budgets, timing rules, failure semantics, and bounded-search restrictions.
- Deterministic mappings from every policy variable to existing `StressScenario`,
  `StressComponent`, `StrategySpec`, `ExperimentSpec`, `ComponentTransformSummary`, or
  deterministic backtest fields. The rebalance-relative offset resolves from the immutable market
  calendar and predetermined monthly schedule into existing `StressComponent.date`; no
  `date_offset`, `window`, result, or verdict field was added.
- Explicit separation of invalid proposals, applicable-but-unsuccessful hypotheses, and
  `not_applicable` hypotheses. Volatility-sizing failure is `not_applicable` to fixed monthly 60/40
  because the strategy declares no volatility-sizing rule; no scenario is emitted for that row.
- Required inert `hypothesis`/`headline` narrative plus complete ordered numeric components, with
  the numeric components as the only evaluation authority. Three valid and three intentionally
  invalid JSON examples cover every applicable hypothesis.
- A numeric definition of transaction-cost materiality: incremental cost contribution at least
  `0.005` (50 bps) and at least `0.10` (10%) of absolute scenario loss, both inclusive.
- Tightened `config/attack-policy-v1.yaml` envelope: one-day gap `[-0.15, -0.02]`, sustained shock
  `[-0.25, -0.04]`, duration `[5, 20]`, volatility multiplier `[1.25, 3.00]`, correlation
  `[0.25, 0.90]`, and cost multiplier `[2.00, 5.00]`. No Gate 5 or Gate 6 bound was expanded.

### Validation

Performed on 2026-08-23 from the repository root:

- The first inline documented-example checker stopped before example validation because a direct
  Parquet read did not preserve MultiIndex level names. The fixture selector was corrected from
  `level="field"` to existing level index `1`; no repository file changed.
- The second inline checker stopped before policy validation because strict Python-object parsing
  rejects JSON date strings. It was corrected to the repository's actual JSON boundary,
  `StressScenario.model_validate_json`; no example or production contract changed.
- Final inline JSON checker, executed with
  `$code | & '.\.venv\Scripts\python.exe' -`: PASS. All three valid examples passed JSON parsing,
  `StressScenario.model_validate_json`, `load_attack_policy`/`AttackPolicy.validate_scenario`, and
  `apply_stress_scenario` Gate 5 preflight against the immutable Gate 8 fixture. All three invalid
  examples passed Pydantic and were rejected by `AttackPolicy` for exactly the intended reason:
  duration outside `[5, 20]`, shock outside `[-0.15, -0.02]`, and cost multiplier outside
  `[2.00, 5.00]`.
- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_contracts.py tests/test_stress.py
  tests/test_attack.py`: PASS; 77 passed in 8.16 seconds.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 128 passed in 16.42 seconds with 80%
  branch-aware package coverage and no warnings, skips, expected failures, or placeholders.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in 13 source files.
- `git diff --check`: PASS (exit code 0); Git emitted only existing line-ending conversion warnings.
- `git -c core.autocrlf=false diff --no-index --check -- NUL docs/ATTACKER_DESIGN.md`: PASS; Git
  returned the expected files-differ exit code and emitted no whitespace diagnostic.
- `git status --short`: PASS; it shows the prior Gates 1-8 work plus only the intended Gate 9A
  additions/updates: `docs/ATTACKER_DESIGN.md`, `config/attack-policy-v1.yaml`, and this status
  entry. No commit was created.

### Assumptions and decisions

- The supplied shock-duration maximum of 60 rows intersects the existing Gate 6 policy maximum of
  20 at `[5, 20]`. The supplied cost-multiplier range `[2.00, 20.00]` intersects the existing Gate 6
  maximum of 5 at `[2.00, 5.00]`. The not-applicable volatility-sizing row also records the tighter
  Gate 6 multiplier maximum 3 and duration maximum 20. Other table ranges already fit inside engine
  domains.
- `AttackPolicy` stores one shared range per family. Its YAML holds the tight common envelope, while
  the document retains the narrower SPY/TLT-specific shock ranges and contextual rules. Gate 9A
  does not expand validators to add per-symbol ranges, offsets, applicability, or expected-output
  fields.
- Correlation/volatility duration resolves to shared inclusive `start_date`/`end_date` values with
  20-126 observed rows. The inflation scenario order is volatility multiplier, correlation target,
  then sustained cumulative shock, so the achieved correlation observable is the correlation
  component's immediate post-transform summary.
- Rebalance timing uses only the immutable observed-date index and the built-in first-observed-close
  monthly schedule. The stale-weight counterfactual and transaction-cost materiality definitions
  use existing deterministic engine series; Gate 9A documents them but adds no calculation or
  result field.
- The existing schema permits `headline=None`, but this policy requires a non-empty headline for
  every proposal implementing the table. Enforcing that later-agent responsibility is outside Gate
  9A and no validator was changed.
- Acceptance used the available Python 3.12.13 runtime while package metadata and static analysis
  remain targeted at Python 3.11. No Hosted Agent, Azure/cloud, orchestration, LLM/model call,
  scenario transform, broker, recommendation, or live-trading functionality was added.

### Blockers

None.

## Gate 9 — Attacker hypothesis implementation

**State:** Complete

### Deliverables

- Extended the existing versioned `AttackPolicy` with four discriminated, schema-valid hypothesis
  rows. Each row records its required strategy mechanism, exact ordered component template, approved
  search dimensions, symbol-specific bounds, and fixed observable thresholds without widening the
  Gate 9A machine envelope.
- Added numeric-template classification that ignores `StressScenario.hypothesis` and `headline`,
  plus hypothesis-specific validation for component order, shared windows, exact SPY/TLT coverage,
  symbol ranges, 20-126-row correlation windows, 5-20-row shocks, predetermined rebalance offsets,
  positive baseline cost, resulting cost below 10,000 bps, and positive turnover in the inclusive
  evaluation window.
- Added runtime policy projection from `StrategySpec`. The fixed monthly 60/40 target receives the
  inflation, rebalance-timing, and friction rows; volatility regime jump is removed before the
  attacker sees the policy because no current strategy kind declares volatility sizing. An external
  weights spec also loses the fixed-monthly rebalance row.
- Updated `prompts/attacker.md` to require active hypothesis rows, exact numeric templates, bounded
  dates and values, contextual assumptions, and non-authoritative narrative. The attacker evidence
  summary now carries the typed target strategy and baseline transaction-cost rate, not paths or
  daily data.
- Updated both checked-in policies and the deterministic offline clients to emit hypothesis-valid
  candidates within the approved bounds. The Gate 8 demo retains three intended
  inflation-correlation failures while using the Gate 9 policy; hard budgets remain exactly
  3 rounds x 8 candidates = 24 total and `TOP_K=3`.
- Added `tests/test_attacker_hypotheses.py` with constructed discovery cases for all three applicable
  rows, a numerical volatility failure that is explicitly not attributed to unsupported volatility
  sizing, inclusive-boundary and invalid-assumption coverage, model-facing runtime removal, inert
  narrative checks, and one-row-at-a-time ablation identifying which structured family found each
  fixture failure. Updated Gate 8 exact scenario IDs and onset dates to the new deterministic engine
  evidence produced by the approved tighter policy.

### Validation

Performed on 2026-08-23 from the repository root:

- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_attack.py tests/test_services.py`:
  PASS; 26 passed in 14.75 seconds after the policy-schema implementation.
- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_attacker_hypotheses.py`: PASS; all five
  Gate 9 tests passed in 7.28 seconds. The constructed applicable cases produced engine breaches and
  their documented observables; volatility sizing was removed at both the model-facing and runner
  boundaries; boundary, invalid-assumption, and ablation assertions passed.
- `& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_offline.py
  tests/test_attacker_hypotheses.py`: PASS; 11 passed in 15.39 seconds. Earlier Gate 8 regression
  runs correctly exposed a one-component rebalance candidate in the top three and changed onsets
  under the tighter shock bounds; the offline candidate allocation and exact evidence assertions
  were updated, then the combined regression passed.
- `& '.\.venv\Scripts\python.exe' -m pytest -q`: PASS; 133 passed in 27.46 seconds with 78%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access.
- `& '.\.venv\Scripts\python.exe' -m ruff check .`: PASS; `All checks passed!`. The first full
  run found one import-order issue in the new test; Ruff applied only that mechanical ordering fix,
  and the final full run passed.
- `& '.\.venv\Scripts\python.exe' -m mypy src`: PASS; no issues in 13 source files.
- `& '.\.venv\Scripts\redteam.exe' run --experiment config/example_60_40.yaml --dataset
  tests/fixtures/offline-cache/manifests/correlation-break.json --mode offline --output
  artifacts/gate9-offline-fixture`: PASS with exit code 0 and `status=verified`;
  `candidate_slots_consumed=8`, `top_failures=3`, `replayed=3`, and `verified_failures=3`. Data
  SHA-256 remained `6fa7e4f25f08fa8de56b09b1ba54a29a0f183baa793568ad9b55e870fb772c4d`
  and experiment configuration SHA-256 remained
  `40886cbee4bffc70cf241991184bf525d8bafb8e25756ce6fa95fc1995f70e1f`.
- `git diff --check`: PASS (exit code 0); Git emitted only existing line-ending conversion warnings.

### Assumptions and decisions

- Gate 9 implements the reviewed Gate 9A table without adding a new strategy kind or pretending
  that fixed monthly 60/40 has volatility sizing. The retained volatility row is a typed search
  definition for a future strategy that explicitly proves that mechanism; it emits no runtime
  candidate today.
- Checked-in Gate 9 policies contain all four rows. The `hypotheses=()` default remains only for
  backward-compatible construction of earlier Gate 6 family-envelope tests; when rows are present,
  every candidate must match exactly one active ordered numeric template and supply a non-empty inert
  headline.
- Runtime applicability comes only from typed `StrategySpec` fields. Friction's positive-cost and
  positive-turnover requirements are contextual proposal validity checks, not invented strategy
  mechanisms or new engine metrics.
- Expected observables in tests are calculated from existing deterministic engine arrays and typed
  component/worst-window evidence. Policy classification never reads or trusts attacker prose.
- The accepted Gate 8 exact top scenarios are now `offline-r01-c06`, `offline-r01-c05`, and
  `offline-r01-c04`; their engine-sourced drawdown and rolling-loss onsets are 2024-03-11 and
  2024-03-07 respectively. No assertion was removed or weakened.
- Acceptance used the available Python 3.12.13 runtime while package metadata and static analysis
  remain targeted at Python 3.11. No backtest-engine refactor, new market calculation, cloud code,
  model SDK, broker, recommendation, or live-trading integration was added.

### Blockers

None.

## Gate 10 — Local packaging for two Microsoft Foundry Hosted Agents

**State:** Complete locally; no Azure resource was created, changed, or deployed

### Deliverables

- Added two isolated source-deployment applications, `apps/attacker-hosted` and
  `apps/defender-hosted`, over the shared tested `strategy_redteam` core. Both use the current
  `InvocationAgentServerHost`, exposing `GET /readiness`, `POST /invocations`, and a generated
  Invocations OpenAPI document without a retired initial-preview server package.
- Added strict attacker/defender request and response contracts. The attacker accepts an
  `ExperimentSpec` plus an immutable dataset reference, enforces the existing 3 x 8 / 24 / top-3
  bounds, and returns top `StressResult` values, complete top-scenario evidence, execution
  provenance, and immutable artifact references. The defender accepts the same experiment and
  dataset evidence plus those top scenarios, reloads the dataset independently, replays at most
  three scenarios, and returns `DefenderVerdict` values, a typed `FailureReport`, and its reference.
- Added the `DatasetStore` protocol, discriminated local-file and Azure Blob references,
  `LocalFileDatasetStore`, `AzureBlobDatasetStore`, and a router. Both implementations reuse one
  byte-level verifier for canonical manifest/Parquet bytes and exact identifiers/hashes. Blob
  reads are size-bounded and use only `DefaultAzureCredential`/token credentials; URLs containing
  credentials or SAS query strings are rejected.
- Added immutable local and Azure Blob artifact stores. Existing local outputs are never replaced;
  Blob uploads use `overwrite=False`. No account key, SAS token, password, secret, or connection
  string is accepted or declared.
- Added current Agent Framework `FoundryChatClient` adapters with structured Pydantic output,
  `store=False`, deterministic attacker seed propagation, and application child spans containing
  identifiers and bounded counts only. Model text still has no numerical authority.
- Added the unified root `azure.yaml` with an existing-project endpoint, two `azure.ai.agent`
  services, Invocations protocol `2.0.0`, current scalar source entry points, and the current
  supported `python_3_13` remote-build runtime. It declares no model deployment, infrastructure,
  identity block, or secret and was not submitted to Azure.
- Added `requirements-hosted.in`, a complete exact `requirements-hosted.lock`, per-application
  `.agentignore`, and `scripts/build_hosted_packages.py`. The deterministic builder creates two
  separate source trees and ZIPs with SHA-256 package manifests while excluding `.env`, `.azure`,
  caches, logs, run outputs, artifacts, and credential/secret-like file names.
- Added `tests/test_hosted_contracts.py` for both real local HTTP boundaries with the fixed Gate 8
  dataset and deterministic fake model clients, OpenAPI/health checks, independent replay,
  package determinism/exclusions, unified YAML assertions, Blob credential/hash behavior,
  traversal/SAS rejection, and artifact immutability.
- Added `docs/AZURE.md` with all consulted current official Microsoft Learn URLs and access date
  2026-08-23. It records the August 20, 2026 preview-backend retirement, current source runtime,
  unified YAML, protocol, managed-identity, sequential-orchestration, and tracing decisions.

### Validation

Performed on 2026-08-23 from the repository root. `$python` below was
`C:\Users\61450\AppData\Local\Temp\strategy-redteam-gate10-lock-38009d29f4fe41db9938c5b1cdc9c7c5\Scripts\python.exe`:

- `& $python scripts/build_hosted_packages.py`: PASS. It produced deterministic isolated ZIPs;
  final SHA-256 values were attacker
  `f498eeda76a1485bb93d6095c36b80ed538afab720ace431347a6f93f05c9ae7` and defender
  `e97e5796a47382d9d122b28d3dd09304d1ec61000892e1d512436f8c6f3e82b9`.
- For each application, a separate clean temporary environment was created with
  `& $basePython -m venv $venv`, followed by
  `& $venvPython -m pip install --disable-pip-version-check --quiet -r
  dist/hosted/<application>/requirements.lock` and an import of the packaged `main.py` with
  `PYTHONDONTWRITEBYTECODE=1`: PASS for attacker and defender on Python 3.12.13. The concrete
  environment suffixes were `strategy-redteam-attacker-clean-5b12aaa983844b12904b1b3c5f8550c3`
  and `strategy-redteam-defender-clean-931f86a4cd68472ab304378b9a487a39`.
- `& $basePython -m pip download --disable-pip-version-check --quiet --only-binary=:all:
  --implementation cp --python-version 3.13 --abi cp313 --platform manylinux_2_28_x86_64
  --platform manylinux2014_x86_64 --dest $wheelhouse -r requirements-hosted.lock`: PASS; all
  114 locked Linux x86_64 CPython 3.13 wheels resolved.
- `& $python -m pytest -q`: PASS; 140 passed in 40.16 seconds with 78% branch-aware package
  coverage and no warnings, skips, expected failures, placeholders, model calls, or network calls.
- `& $python -m ruff check .`: PASS; `All checks passed!`.
- `& $python -m mypy`: PASS; no issues in 16 source files.
- `& $python -m mypy apps/attacker-hosted/main.py`: PASS; no issues.
- `& $python -m mypy apps/defender-hosted/main.py`: PASS; no issues.
- `& $python -m mypy scripts/build_hosted_packages.py`: PASS; no issues.
- `git diff --check`: PASS (exit code 0); Git emitted only existing line-ending conversion
  warnings.

### Assumptions and decisions

- Current official source deployment supports Python 3.13/3.14, so `azure.yaml` uses
  `python_3_13`; source syntax, Ruff, mypy, and package metadata retain the repository's Python
  3.11 language floor. Local execution used the available Python 3.12.13, and target dependency
  resolution was separately verified for Linux x86_64 CPython 3.13.
- Source ZIP deployment is the selected current supported path. Docker is unavailable locally but
  is not a prerequisite for this mode, so no container build result is claimed.
- The unified YAML connects only to `${AZURE_EXISTING_FOUNDRY_PROJECT_ENDPOINT}`. A future approved
  deployment must grant each platform-created agent identity least-privilege Blob data-plane roles;
  Gate 10 assigned none.
- Agent Framework sequential orchestration was reviewed but intentionally remains in the later
  bounded client boundary defined by the specification: attacker once, then defender once. Gate 10
  packages only the two current agent units.
- The local dependency readiness check installed/verified current `azd` Foundry extensions only;
  no `az`, mutating `azd`, portal, deployment, provisioning, RBAC, or other Azure operation ran.

### Blockers

None.

## Gate 11 — Deploy and verify the attacker Hosted Agent

**State:** Complete on 2026-08-25; attacker runtime acceptance passed and the defender was not
deployed

### Deployment evidence

- Deployment used clean `main` at
  `3395dc9767cde89f4f1672c7206ed5af18e2945a`. The index and working tree were clean before
  packaging and deployment. Source bytes did not change, so the full test suite was not rerun as
  directed for this final deployment.
- `& '.\.venv\Scripts\python.exe' scripts/build_hosted_packages.py`: PASS. The audited attacker
  ZIP contained 19 files, was 79,342 bytes, and had SHA-256
  `6d33517a9bec715f541b151bfcf8677963f05832d42c777bc95d68a9fcfd1f1a`. Every payload file mapped
  to clean `HEAD` or deterministic package metadata; forbidden and defender path counts were zero.
- `azd deploy attacker-hosted -e gate11-attacker --no-prompt --timeout 1200`: PASS with exit code
  0. It ran once from `2026-08-24T22:16:02.1686444Z` through
  `2026-08-24T22:17:44.8342651Z` and created only version 3 of
  `strategy-redteam-attacker`.
- Version 3 reached `active`. Its endpoint is
  `https://zcxie-test-5455-resource.services.ai.azure.com/api/projects/zcxie-test-5455/agents/strategy-redteam-attacker/endpoint/protocols/invocations?api-version=v1`.
  Its platform identity remained `42c5143f-b009-42bf-a417-1fc47e983792`.
- The downloaded version-3 code ZIP was 80,035 bytes with Azure content SHA-256
  `f98846a9390577607b4320ae5ea4266815051205a7ffc266e90a05a6317cea30`. Its 19 payload paths,
  sizes, and content hashes exactly matched the audited deterministic attacker payload. Outer ZIP
  metadata differed as expected; defender and forbidden path counts were zero.

### Runtime acceptance evidence

- Exactly one version-3 invocation was made for `gate11b-attacker-smoke-003`, with code version
  `3395dc9767cde89f4f1672c7206ed5af18e2945a`, seed `20260823`, and configured budgets
  `1/1/1/1`. The request was generated from the committed fixture manifest and
  `config/example_60_40.yaml`; request SHA-256 was
  `63dc4e0c8b9d8329343ff26032be1fb9cb86f9b3cdf955cc0060ce662d45f165`.
- The invocation returned HTTP 200 in 20.7 seconds. Session ID was
  `224985de71892b8700Mp31Kt6VnCfUlMTEaEpA4gIzJ2NTW4P1`, invocation ID was
  `inv_c29f7b05904e118700jnuuTFkHsWRDy9UUxj1DYaVW0hF1wS5R`, APIM request ID was
  `b6e9e94d-78fd-4229-9a0a-b79a9468f147`, and the 6,901-byte response SHA-256 was
  `4b903992e5b78832bd08a0ba6277443a1f5ac5f60434d6292db706ad18608130`.
- `AttackerHostedResponse` validation passed. The independently listed and downloaded prefix
  `strategy-redteam/attacker/gate11b-attacker-smoke-003/` contained exactly the required seven
  artifacts. Blob lengths matched downloaded lengths, and `verify_run_artifacts()` passed exact
  membership, indexed SHA-256 values, typed schemas, configuration/dataset/policy provenance,
  proposal/result alignment, ranking, and report notice. The dataset and manifest SHA-256 values
  remained `6fa7e4f25f08fa8de56b09b1ba54a29a0f183baa793568ad9b55e870fb772c4d` and
  `1791b732a491c3c381e2c07ee4b9a724e82a2b0bc4e0718d8e62ed685bc263bf`. The committed publish
  path constructs returned references from those same create-only uploaded bytes.
- Application Insights returned 48 correlated records on the first correctly encoded query.
  Operation `ebf02cf84d6b8f27efa825bbcffc093c` contains the version-3 invocation request, successful
  `strategy_redteam.attacker` custom span, managed-identity dataset Blob reads, one project-endpoint
  `/openai/v1/responses` dependency with HTTP 200, artifact Blob creates with HTTP 201, and the
  final `/invocations` HTTP 200. No correlated exception or failed dependency was present. Optional
  experimental GenAI tracing was disabled, but the committed required custom/model/Blob evidence
  is present.

### RBAC, cleanup, and resource delta

- The attacker identity has exactly `Storage Blob Data Reader` on the dataset container, assignment
  `de0d68ac-8dee-40d2-a944-fe2c0c8338f9`, and `Storage Blob Data Contributor` on the artifact
  container, assignment `592a3f99-bce9-496e-a733-b0eeabd492c1`. It has no other direct role.
- Temporary artifact-container Reader assignments
  `6a24674a-49bb-4054-81fe-af85b6f46643` and
  `5fde2df6-8436-4f8c-899b-b7f5f9b5b9a6` were deleted in `finally`; authoritative readback found
  zero remaining matches. The second was a verification-only attempt after a local evidence-script
  type-comparison error and never obtained data-plane access within its bounded retry.
- Before/after ARM inventory remained the same six existing resources: the Foundry account and
  project, Application Insights component and smart-detection action group, Log Analytics
  workspace, and `strt5455g11` storage account. Foundry inventory contains only
  `strategy-redteam-attacker`. No defender, ACR, tool, extra model, monitoring role, broader RBAC,
  or infrastructure resource was created. No destructive cleanup was run.

### Blockers

None. Gate 11 is closed; Gate 12 may begin only when explicitly requested.

## Repository tooling — Cost-aware Codex routing

**State:** Complete on 2026-08-26; this configuration did not start Gate 12

### Deliverables

- Added `.codex/config.toml` with a `gpt-5.6-terra`/low-reasoning project default, low verbosity,
  a one-subagent concurrency cap, and `gpt-5.6-luna`/low as the default spawned-agent profile.
- Added project-scoped `low_cost_scanner`, `standard_worker`, `deep_reviewer`, and `deep_worker`
  agent definitions. Their fixed tiers are Luna/low, Terra/medium, and Sol/high; the scanner and
  reviewer are read-only, and every profile forbids recursive delegation.
- Added durable routing rules to `AGENTS.md`. Simple tasks stay in the low-effort main thread;
  delegation is reserved for bounded context isolation or justified escalation, never numerical
  market-result authority, scope expansion, parallel work by default, or live-Azure authorization.

### Validation

Performed on 2026-08-26 from the repository root:

- The exact routing assertion below passed with `PASS: routing config and 4 profiles`:

  ```powershell
  & '.\.venv\Scripts\python.exe' -c "import tomllib,pathlib; p=pathlib.Path('.codex'); c=tomllib.loads((p/'config.toml').read_text(encoding='utf-8')); a=[tomllib.loads(x.read_text(encoding='utf-8')) for x in sorted((p/'agents').glob('*.toml'))]; assert (c['model'],c['model_reasoning_effort'],c['agents']['max_concurrent_threads_per_session'],[(x['name'],x['model'],x['model_reasoning_effort']) for x in a])==('gpt-5.6-terra','low',1,[('deep_reviewer','gpt-5.6-sol','high'),('deep_worker','gpt-5.6-sol','high'),('low_cost_scanner','gpt-5.6-luna','low'),('standard_worker','gpt-5.6-terra','medium')]); print('PASS: routing config and 4 profiles')"
  ```
- `git -c core.autocrlf=false diff --no-index --check -- NUL <file>` for each of the five new TOML
  files: PASS; no whitespace diagnostics were emitted.
- `git diff --check`: PASS with exit code 0; Git emitted only LF-to-CRLF warnings for `AGENTS.md`
  and `docs/STATUS.md`.
- `git status --short`: PASS; it showed modified `AGENTS.md`, modified `docs/STATUS.md`, and the new
  `.codex/` directory only.
- `rg -c "^## Cost-aware Codex routing$" AGENTS.md` and the corresponding status-heading count:
  PASS; each routing section occurs exactly once.
- `Get-Command codex -All`: PASS; it found the packaged desktop-app executable. Both
  `codex --version` and the approved retry using its absolute WindowsApps path were blocked by the
  package ACL with `Access is denied`, so the running task could not hot-load the new project
  configuration through the CLI.
- Pytest, Ruff, and mypy were not run because no Python source, package, schema, test, or runtime
  behavior changed.

### Assumptions and decisions

- This follows the current official OpenAI Docs guidance for project-scoped custom agents and
  explicit `model_reasoning_effort` settings. A user/client model selection has higher precedence.
- Terra/low remains the parent/router because reliable enforcement of the financial research and
  gate boundaries is more important than using Luna as the main classifier. Luna is limited to
  bounded read-only scans whose compact result is expected to save main-thread context.
- Subagents generally add total tokens. The one-agent cap, no-parallel default, concise handoff,
  and escalation triggers optimize total token use rather than merely choosing the cheapest model.
- The new defaults are expected to apply to new tasks or sessions opened in this trusted project;
  they do not retroactively change the model of this already-running task.

### Blockers

None. Direct CLI reload verification was unavailable because of the installed WindowsApps ACL;
TOML syntax and routing values passed deterministic local validation.

## Gate 12B — Provider-neutral model-provider selection and configuration

**State:** Complete locally on 2026-08-26; no Azure, Ollama, or other model invocation occurred

### Deliverables

- Added `strategy_redteam.model_provider`, a small configuration/factory layer with the recognized
  provider names `deterministic`, `foundry`, and `ollama`. It deliberately retains
  `ScenarioProposer` and `ReportWriter` as the only model-role contracts and introduces no generic
  LLM protocol.
- Added an explicit, serializable `model_provider` block to the existing offline YAML configuration.
  The checked-in local demo selects `deterministic`, preserving its exact model-free behavior.
- Routed offline attacker/defender client construction through the factory. Foundry construction is
  lazy and reads its existing environment configuration only when `foundry` is selected. The two
  Hosted Agent entry points now select providers centrally, retaining `foundry` as their default.
- `ollama` validates as a recognized selection but fails with a typed configuration error stating
  that its client is deferred to Gate 12C; no HTTP implementation or fallback was added.
- Added focused provider tests for valid/default selections, invalid values, Foundry factory
  continuity without network access, Ollama's deferred failure, and continued absence of provider
  SDK coupling from `services.py`.

### Validation

Performed on 2026-08-26 from the repository root using
`C:\Users\61450\OneDrive - The University of Melbourne\Documents\ChatGPT\Finance Red Team project\.venv\Scripts\python.exe`:

- `-m pytest -q tests/test_model_provider.py`: PASS; 7 passed in 6.65 seconds.
- `-m pytest -q tests/test_backtest.py tests/test_stress.py tests/test_attack.py tests/test_offline.py`:
  PASS; 53 passed in 15.27 seconds.
- `-m pytest -q`: PASS; 170 passed in 47.06 seconds with 78% branch-aware package coverage and no
  warnings, skips, expected failures, placeholders, model calls, or network access.
- `-m ruff check .`: PASS; `All checks passed!`.
- `-m mypy src`: PASS; `Success: no issues found in 17 source files`.
- `git diff --check`: PASS (exit code 0); Git emitted only existing LF-to-CRLF warnings.
- `git status --short`: PASS; it showed the pre-existing routing-document/configuration changes and
  the Gate 12B source, configuration, test, and status changes; nothing was reset, stashed, or
  removed.

### Assumptions and decisions

- `STRATEGY_REDTEAM_MODEL_PROVIDER` is the non-secret environment selector for hosted entry points;
  the existing offline YAML remains the explicit local configuration mechanism. Provider-specific
  settings remain isolated and are not read at module import time.
- Deterministic numerical modules, scenario semantics, budgets, no-look-ahead behavior, and artifact
  contracts were not changed. Foundry support remains optional behind the existing role contracts.
- Gate 12C and later gates remain unstarted; no telemetry or static-export work was added.

### Blockers

None.

## Gate 12C — Local Ollama adapters for existing model-role contracts

**State:** Complete locally on 2026-08-26; no live Ollama invocation occurred

### Deliverables

- Added `strategy_redteam.ollama_clients` with `OllamaScenarioProposer` and
  `OllamaReportWriter`, using only the existing `ScenarioProposer` and
  `ReportWriter` contracts. The official Ollama Python client is imported lazily.
- Ollama requests send each existing Pydantic response model's JSON schema through
  Ollama's `format` parameter. Returned text is parsed and Pydantic-validated as
  `AttackBatch` or `DefenderNarrativeBatch` before it can reach application code.
  Malformed, schema-invalid, and out-of-range data, as well as transport failures,
  raise a typed `OllamaProviderError`; there is no repair or provider fallback.
- Added non-secret Ollama-only environment configuration:
  `STRATEGY_REDTEAM_OLLAMA_MODEL` (required),
  `STRATEGY_REDTEAM_OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`),
  `STRATEGY_REDTEAM_OLLAMA_TIMEOUT_SECONDS` (default `30`), and
  `STRATEGY_REDTEAM_OLLAMA_TEMPERATURE` (default `0`). Foundry settings are not
  read for Ollama, and deterministic mode remains model-free.
- Updated provider selection and added the `ollama>=0.4` runtime dependency. No
  numerical engine, scenario semantics, bounded budgets, or deterministic replay
  behavior changed.

### Validation

Performed on 2026-08-26 from the repository root using `.venv\\Scripts\\python.exe`:

- `-m pytest -q tests/test_ollama_clients.py tests/test_model_provider.py`: PASS;
  13 passed in 7.49 seconds.
- `-m pytest -q tests/test_backtest.py tests/test_stress.py tests/test_attack.py
  tests/test_offline.py tests/test_model_provider.py tests/test_ollama_clients.py`:
  PASS; 66 passed in 19.09 seconds.
- Full suite was executed in three complete, non-overlapping groups because the
  local command-output bridge truncated the single-suite summary: 106 passed in
  8.78 seconds, 45 passed in 17.53 seconds, and 25 passed in 16.29 seconds
  (176 total; no failures).
- `-m ruff check .`: PASS; `All checks passed!`.
- `-m mypy src`: PASS; `Success: no issues found in 18 source files`.
- `git diff --check`: PASS (exit code 0); Git emitted only existing LF-to-CRLF
  warnings. `git status --short` showed pre-existing Gate 12B/routing changes plus
  the Gate 12C files; nothing was reset, stashed, or removed.
- Manual smoke test: NOT RUN. `ollama` was not available on PATH and
  `STRATEGY_REDTEAM_OLLAMA_MODEL` was absent. No model was downloaded and no
  network request was made. With a local server and model configured, select
  `STRATEGY_REDTEAM_MODEL_PROVIDER=ollama` and run the existing bounded local
  workflow; its first proposal validates through the adapter.

### Assumptions and decisions

- Ollama text/proposals are not deterministic. Immutable data, data hashes,
  strategy configuration, numeric scenario application, backtests, rule evaluation,
  and fixed-scenario defender replay remain deterministic and reproducible.
- The Ollama client is local-only with no automatic cloud or paid-provider fallback.

### Blockers

The optional real local Ollama smoke test is blocked by the absent local executable
and model configuration; automated acceptance is network-free and passed.

## Gate 12D — Portable structured run telemetry

**State:** Complete locally on 2026-08-26; full network-free acceptance passed.

### Deliverables

- Added `strategy_redteam.telemetry` schema `1.0`: a provider-neutral, canonical JSON run record
  containing immutable dataset manifest/hash, config/code/seed provenance, configured bounds,
  copied deterministic metrics and typed scenario evaluations, defender verdicts, limitations, and
  an ordered observable event stream.
- Integrated telemetry at the existing offline orchestration publication boundary. Each verified
  offline bundle now includes and hashes `telemetry.json`; bundle verification validates its schema
  and config/dataset/experiment continuity.
- Numerical values are copied from `AttackRun`, `ScenarioEvaluationRecord`, and `DefenseRun` only;
  telemetry performs no market calculation. It records only output that crossed typed scenario and
  narrative validation boundaries. It serializes no prompts, chain-of-thought, SDK response object,
  stdout/stderr, credentials, headers, connection strings, or environment metadata.

### Validation

- `python -m pytest -q tests/test_offline.py`: PASS; 6 passed.
- `python -m pytest -q tests/test_offline.py tests/test_attack.py tests/test_services.py tests/test_model_provider.py tests/test_ollama_clients.py`: PASS; 45 passed.
- The original `python -m pytest -q` failed during unrelated fixture setup because pytest could
  not scan its default Windows base temporary directory (`WinError 5` on
  `C:\Users\61450\AppData\Local\Temp\pytest-of-61450`). A repository-local write probe and
  `python -m pytest -q tests/test_contracts.py --basetemp=.pytest_tmp`: PASS; 50 passed.
  This is a test-runner environment issue, not a Gate 12D product regression.
- `python -m pytest -q tests/test_offline.py --basetemp=.pytest_tmp`: PASS; 6 passed.
- `python -m pytest -q --basetemp=.pytest_tmp`: PASS; 176 passed in 55.79s with 78%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access. `.pytest_tmp/` and temporary captured test output are Git-ignored.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 19 source files`.
- `git diff --check`: PASS (exit code 0; existing line-ending warnings only).

### Assumptions and blockers

- Existing provider configuration exposes a provider name but not a portable model identifier, so
  `model_identifier` remains optional and is absent for the current offline integration. Existing
  OpenTelemetry is unchanged. On Windows, use `--basetemp=.pytest_tmp` for reliable acceptance
  runs. No blocker.

## Gate 12E — Verified local-agent demo artifact workflow

**State:** Complete locally on 2026-08-26; one genuine local Ollama smoke run verified.

### Deliverables

- Added `strategy_redteam.demo` and `redteam demo run`. The command requires an explicit
  `model_provider.provider: ollama`, executes the existing bounded offline attacker → deterministic
  engine → defender replay workflow, verifies the run bundle, and writes an immutable canonical
  `demo-telemetry.json` under the requested demo artifact directory.
- The exported file is exactly the validated Gate 12D `RunTelemetry` from the genuine completed
  workflow; it contains no fabricated values or separate presentation metrics. It preserves typed
  scenarios, deterministic metrics/breach evidence, event ordering, defender verdicts, limitations,
  and dataset/config/seed/provider provenance already available to the telemetry schema.
- Added network-free fake/deterministic workflow tests. `README.md` documents the Ollama command;
  generated `artifacts/` remain ignored.

### Validation

- `python -m pytest -q tests/test_demo.py tests/test_offline.py`: PASS; 8 passed.
- `python -m pytest -q --basetemp=.pytest_tmp`: PASS; 178 passed in 62.23s with 78%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (exit code 0; existing line-ending warnings only).

### Assumptions and blockers

- The smoke run is an integration check, not the final recruiter-facing demo artifact.

## Gate 12F — Chart-ready deterministic performance evidence

**State:** Complete locally on 2026-08-26; network-free acceptance passed.

### Deliverables

- The deterministic `BacktestResult` already retained the authoritative daily equity curve. Added
  its existing engine-calculated drawdown series as a behaviour-preserving result field; no
  portfolio, return, drawdown, or stress formula changed.
- Added bounded typed `PerformanceChartPoint` evidence to each valid scenario evaluation. Each
  ordered point copies the engine's baseline equity, stressed equity, and stressed drawdown for the
  same market date. Gate 12D telemetry and Gate 12E demo export therefore include genuine
  chart-ready series and retain breach-onset dates present in the same series.
- No downsampling is applied: the fixed daily fixture paths remain far below the 10,000-point typed
  cap, and full engine ordering is preserved. Artifact hashes cover the augmented JSONL and
  telemetry content, so bundle verification detects chart-data tampering.

### Validation

- `python -m pytest -q tests/test_offline.py tests/test_attack.py`: PASS; 22 passed.
- `python -m pytest -q --basetemp=.pytest_tmp`: PASS; 178 passed in 63.51s with 78%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (exit code 0; existing line-ending warnings only).

### Assumptions and blockers

- Chart points are copied only after deterministic engine evaluation; invalid/rejected scenarios
  carry no chart data. Existing provider and model boundaries remain unchanged. No blocker.

## Gate 12E repair — Ollama date-only structured output

**State:** Complete locally on 2026-08-26; superseded by the verified smoke record below.

### Root cause and repair

- The strict authoritative `AttackBatch` contract correctly rejected Ollama timestamp strings for
  `evaluation_start` and `evaluation_end`. Its generated JSON Schema exposed those fields only as
  `type: string`, without a date format or pattern.
- The Ollama-only request path now appends explicit date-only instructions and sends a deep-copied
  Pydantic schema with `^\\d{4}-\\d{2}-\\d{2}$` patterns on only those two calendar fields. The
  authoritative Pydantic/domain schema is unchanged. Returned text still receives one strict
  `model_validate_json` validation with no timestamp coercion or retry.

### Validation

- `python -m pytest -q tests/test_ollama_clients.py tests/test_model_provider.py --basetemp=.pytest_tmp`:
  PASS; 14 passed.
- `python -m pytest -q --basetemp=.pytest_tmp`: PASS; 179 passed in 65.68s with 78%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (exit code 0; existing line-ending warnings only).

### Blockers

- Superseded by the verified local Ollama smoke record below.

## Gate 12E repair — Ollama grammar-compatible date correction

**State:** Superseded by the contextual-validity repair below.

### Root cause and repair

- The provider-local date regex schema mutation caused Ollama's grammar parser to reject the
  request before generation. The adapter again sends the unmodified `response_type.model_json_schema()`
  through `format`, and grounds the model with that same schema plus explicit date-only examples.
- The strict Pydantic contract is unchanged. If the first returned payload fails JSON/schema
  validation, the adapter requests one complete replacement with concise date guidance. A second
  invalid payload fails closed; no response is transformed or coerced.

### Validation

- `python -m pytest -q tests/test_ollama_clients.py tests/test_model_provider.py --basetemp=.pytest_tmp`:
  PASS; 16 passed.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.

### Prior Ollama smoke evidence

- The local Ollama workflow completed with `status=verified`. It used the immutable fixture dataset hash
  `6fa7e4f25f08fa8de56b09b1ba54a29a0f183baa793568ad9b55e870fb772c4d` and configuration hash
  `5729ebd028baeccc0e50daea8c66b1da84116171319ff8aafbe322000132030e`.
- The immutable exported telemetry artifact is
  `artifacts/demo/ollama-run-001/demo-telemetry.json`. The smoke configuration deliberately used
  `max_rounds=1`, `max_candidates_per_round=3`, `max_total_scenarios=3`, `top_k=3`, and
  `timeout_seconds=1500`; the separate Ollama HTTP timeout was 600 seconds.
- `verified_failures=0`: the run validated artifact integrity and provenance, but did not discover
  a qualifying failure. Subsequent telemetry inspection established that the returned batches were
  context-invalid, so this was not yet a genuine evaluated Ollama attack scenario or the final
  recruiter-facing demo artifact.

### Blockers

Superseded by the contextual-validity repair below.

## Gate 12E follow-up — Ollama AttackBatch contextual validity

**State:** Implemented locally on 2026-08-26; real smoke blocked by unavailable local Ollama.

### Root cause and repair

- The exact authoritative context check compares only `AttackBatch.experiment_id` and
  `AttackBatch.round_number` against the current `AttackerEvidenceSummary` request. Genuine
  run-002 returned structurally valid batches that failed that comparison before deterministic
  evaluation. Its exported telemetry retains the rejection but not the raw returned batch, so it
  cannot disambiguate which of those two protected fields differed; no other field is compared by
  this contract.
- The Ollama-only request now supplies both values as immutable, exact echo fields and validates
  them within its existing two-call structured-output boundary. A context-invalid first response
  receives one complete replacement request; a second failure becomes an honest typed rejection.
  The service-level comparison remains unchanged, and generated scenario values are never altered.
- Telemetry now records the configured non-secret Ollama model identifier (for example,
  `qwen3:4b`) for future runs.

### Validation

- `python -m pytest -q tests/test_ollama_clients.py tests/test_model_provider.py tests/test_services.py tests/test_offline.py --basetemp="$pytestTmp"`:
  PASS; 37 passed in 20.11s. Tests use fake clients only and cover protected-field mismatches,
  one correction, two-call failure closure, unchanged scenario payloads, provider selection, and a
  valid Ollama batch reaching engine metrics and chart points.
- `python -m pytest -q --basetemp="$pytestTmp"`: PASS; 186 passed in 64.84s with 78%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (exit code 0; existing line-ending warnings only).

### Blocker

- This shell has neither a discoverable `ollama` executable nor
  `STRATEGY_REDTEAM_OLLAMA_*` configuration. No model was downloaded and no run-003 artifact was
  fabricated. Run the prescribed bounded `max_rounds=1`, `max_candidates_per_round=3`,
  `max_total_scenarios=3`, `top_k=3` smoke once local Ollama is available, and confirm telemetry
  has a valid evaluation with a scenario, metrics, and non-empty chart points.

## Gate 12E diagnostic follow-up — Ollama structured-output classification

**State:** Complete locally on 2026-08-26; no real Ollama smoke was run from this shell.

### Repair

- Local-Ollama failures now carry only a bounded, provider-specific safe diagnostic into the
  existing typed rejection: `ollama_transport_failure`,
  `ollama_json_or_schema_validation_failure`, or `ollama_context_validation_failure`.
  Context failures additionally name only `experiment_id` and/or `round_number`; no raw generated
  response, exception text, or untrusted scenario content is retained.
- The two-request maximum is unchanged. A context correction explicitly restates each failed
  immutable value (`experiment_id MUST equal ...` and/or `round_number MUST equal ...`); a
  JSON/schema correction retains concise date/schema guidance. A second invalid response fails
  closed. Service-level context checks and all deterministic numerical behavior are unchanged.

### Validation

- `python -m pytest -q tests/test_ollama_clients.py --basetemp="$pytestTmp"`:
  PASS; 17 passed in 11.94s.
- `python -m pytest -q --basetemp="$pytestTmp"`: PASS; 190 passed in 64.94s with 78%
  branch-aware package coverage and no warnings, skips, expected failures, placeholders, model
  calls, or network access.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (exit code 0; existing line-ending warnings only).

### Blocker

- Per instruction, no real Ollama run was attempted from this shell because local Ollama is not
  available here. Run-003 should be re-run in the configured local environment to obtain its safe
  rejection category or a genuine evaluated scenario.

## Gate 12E repair — deterministic immutable AttackBatch context envelope

**State:** Implemented locally on 2026-08-26; real smoke deferred because Ollama is unavailable
in this shell.

- Ollama now validates a provider-local scenario payload and the application creates the final
  authoritative `AttackBatch` from its unchanged `scenarios` plus
  `AttackerEvidenceSummary.experiment_id` and `.round_number`. Legacy model-emitted envelope
  fields are tolerated only for compatibility and discarded; they cannot override provenance.
- Schema/date/scenario failures retain the single correction retry (two total requests). The
  public `AttackBatch` schema, service-level context check, all numerical behavior, and other
  providers remain unchanged.
- `python -m pytest -q tests/test_ollama_clients.py tests/test_model_provider.py --basetemp="$pytestTmp"`:
  PASS; 21 passed in 10.78s. `ruff`, `mypy`, and `git diff --check` passed.
- A real smoke was not run here, per instruction; no artifact was fabricated.

## Gate 12E diagnostic — Ollama scenario payload schema failures

**State:** Implemented locally on 2026-08-26; real Ollama run not attempted in this shell.

- When parsed JSON fails Pydantic validation, the Ollama adapter now records at most five safe
  `path=error_type` items (for example, `scenarios.0.evaluation_start=missing`) with the existing
  `ollama_json_or_schema_validation_failure` category. It never retains invalid values, raw JSON,
  prompts, or exception input.
- The one allowed correction request uses only those schema paths and static guidance: required
  fields, YYYY-MM-DD dates, allowed literals/enums, or generic schema conformance. A second
  invalid response still fails closed.
- Focused local tests: `python -m pytest -q tests/test_ollama_clients.py --basetemp="$pytestTmp"`:
  PASS; 15 passed in 9.45s. No real Ollama run was performed.

## Gate 12E repair — family-specific Ollama StressComponent schema

**State:** Implemented locally on 2026-08-26; real Ollama smoke not run in this shell.

- The Ollama-only scenario payload now exposes the domain's existing strict family-discriminated
  `StressComponent` output schema. Each family advertises only its required fields and rejects
  cross-family fields; canonical runtime validation is unchanged.
- Ollama instructions now explicitly give the evidence-summary dataset date window and fixed
  SPY/TLT symbol set, including that repeated symbols are invalid. Invalid dates and symbols remain
  rejected without rewriting.
- Focused Ollama tests passed before this schema-only adjustment; `ruff`, `mypy`, and
  `git diff --check` pass after it. No real run was attempted.

## Gate 12E diagnostic repair — Ollama chat failure classification

**State:** Implemented locally on 2026-08-26; real smoke not run in this shell.

- Chat failures now distinguish safe `ollama_connection_failure`, `ollama_timeout_failure`,
  `ollama_response_error` (status only), and `ollama_client_failure` (exception class only).
  No server body, prompt, response, or exception text is retained.
- Provider failures now bypass `StressScenario` parsing and become direct rejected evaluations with
  `scenario=null`, no metrics, and no chart points, preserving the safe provider detail.

## Acceptance environment record — fresh pytest base temporary directory

**State:** Recorded on 2026-08-26; documentation-only acceptance update.

- The repository-local persistent `.pytest_tmp` directory is locked by Windows/OneDrive. Reusing it
  causes pytest setup errors with `WinError 5`, and it could not be removed with PowerShell
  `Remove-Item -Recurse -Force`. This is an environment/filesystem issue, not a product
  regression. It was not repaired or deleted during this update.
- Full acceptance therefore used a new unique temporary directory outside the repository for the
  run:

  ```powershell
  $pytestTmp = Join-Path $env:TEMP ("strategy-redteam-" + [guid]::NewGuid().ToString("N"))
  python -m pytest -q --basetemp="$pytestTmp"
  ```

- Final suite result supplied for this record: **PASS; 181 passed.** No duration or additional
  pytest summary text was supplied, so none is asserted here.
- Current checks: `python -m ruff check .` — PASS; `All checks passed!`.
  `python -m mypy src` — PASS; `Success: no issues found in 20 source files`.
  `git diff --check` — PASS (exit code 0; existing line-ending warnings only).

## Later gates

**State:** Pending — not started. Their exact scopes and done conditions must be supplied or approved before work begins.

## Gate 12E follow-up — local provider admissibility context

**State:** In progress on 2026-08-26; no real Ollama smoke was run from this shell.

- Prompt-only policy guidance was insufficient for the genuine local model path.  The attacker
  service now derives a read-only return-dependent calendar (excluding the first close, which has
  no asset return) and the monthly strategy rebalance calendar from the validated dataset and
  strategy.  These identities, not prices or returns, are supplied in the bounded evidence summary.
- The Ollama adapter filters baseline-impossible friction rows before constructing its provider
  prompt, exposes the authoritative return/rebalance choices, and takes scenario identity away
  from the model.  It assigns deterministic per-round IDs of the form `ollama-rRR-cCC`; all model
  components, numerical values, and narrative fields otherwise pass unchanged into canonical
  `StressScenario` validation and the existing policy/engine path.
- Focused regression checks: `tests/test_model_provider.py tests/test_ollama_clients.py` — PASS;
  23 passed in 14.03s using a fresh `%TEMP%` basetemp.  `ruff`, `mypy`, and `git diff --check`
  passed.  A complete suite was run once and reached one unrelated boundary-test assertion caused
  solely by a provider name in a generic comment; that comment was removed.  A final clean full
  suite result remains to be recorded before this follow-up can be considered complete.

## Gate 12E repair — template-driven local proposal contract

**State:** Complete locally on 2026-08-26; no real Ollama smoke was run from this shell.

- The prior Ollama response contract was the canonical free-form `StressScenario`, so a model could
  still choose an unusable first return row, arbitrary component ordering, and a friction family
  despite prompt-only filtering.  The Ollama-only boundary now builds a deterministic Pydantic
  union from the runtime policy rows.  A zero-cost baseline removes the friction variant from the
  actual `format` schema, not merely from prose.
- Model proposals now select a template and provide only bounded substantive parameters.  Date
  parameters are indices into the verified `return_dates` list, which excludes dataset row zero.
  Rebalance proposals select an actual rebalance-target index and literal offset `-3`, `-2`, or
  `-1`; the adapter resolves that pair to the authoritative calendar date.  It builds the exact
  policy-owned ordered component template and assigns `ollama-rRR-cCC` IDs.  Numerical values,
  durations, offsets, hypothesis, and headline are copied unchanged; canonical schema and policy
  validation still run after conversion.

### Validation

- `python -m pytest -q --basetemp="$pytestTmp"`: PASS; **188 passed in 71.93s** (fresh external
  temporary directory; no network/model calls).
- Focused provider tests: PASS; 15 passed in 14.26s.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

### Remaining risk

- The dynamic local schema is network-free tested only.  A bounded real Ollama smoke remains the
  required next integration check; none was run here and no artifact was fabricated.

## Gate 12E repair — Ollama dynamic-schema HTTP 400 compatibility

**State:** Complete locally on 2026-08-26; real smoke remains required.

- The 400 was introduced by sending Pydantic's runtime-generated union schema directly as Ollama
  `format`.  That form can contain dynamic union/ref/literal machinery not present in the earlier
  accepted static request.  The provider now sends a conservative flat object/array schema with
  only properties, required fields, scalar types, and string enums.  The dynamic Pydantic template
  model remains the strict post-response validator.
- Runtime template filtering remains structural through the `template` string enum; zero-cost
  friction is omitted.  Return rows remain integer selectors into the row-zero-excluded calendar.
  Rebalance offsets are lossless string choices `minus_3`, `minus_2`, `minus_1`, mapped only after
  validation to `-3`, `-2`, `-1` against authoritative rebalance targets.  IDs and canonical
  component ordering remain application-owned; substantive values are unchanged.
- The new focused schema test asserts no `$defs`, `$ref`, `anyOf`, `oneOf`, or discriminator is
  emitted, while retaining `think=False` and the two-call bound.  Telemetry remains status-only for
  HTTP failures.

### Validation

- `python -m pytest -q --basetemp="$pytestTmp"`: PASS; **189 passed in 66.54s**.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

## Gate 12E repair — bounded two-stage Ollama proposal generation

**State:** Complete locally on 2026-08-27; a real local smoke is still required.

- The accepted flat one-call schema exposed a cross-template parameter superset, allowing Qwen to
  mix gap, friction, and inflation fields.  The Ollama proposer now makes exactly two requests:
  stage one selects candidate position, a runtime-applicable template key, and only legal context
  selectors; stage two receives an exact flat schema with deterministic `candidate_NN` properties
  containing only that selected template's substantive fields.
- Both wire schemas use only flat objects/arrays, scalar types, required fields, and string enums;
  neither uses a union, discriminator, `$ref`, or `$defs`.  Invalid stage-one output fails closed
  after one request; invalid stage-two output fails closed after the second.  No correction call can
  create a third request.
- Zero-cost friction remains absent from the stage-one template enum.  Return-row selectors exclude
  row zero.  Rebalance context uses authoritative target selectors plus `minus_3`, `minus_2`, or
  `minus_1`, resolved losslessly after validation.  IDs and component order remain application
  owned; parameters, durations, offsets, and narrative are copied unchanged into canonical checks.

### Validation

- `python -m pytest -q --basetemp="$pytestTmp"`: PASS; **190 passed in 71.36s**.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

## Gate 12E redesign — application-owned fixed Ollama candidate slots

**State:** Implementation and network-free verification complete on 2026-08-27; Gate 12E
acceptance remains pending a real local Ollama smoke with at least one valid deterministic
evaluation.

- Root cause of the genuine 12-selection failure: the first-stage local schema exposed a
  model-owned variable-length `selections` array and `candidate_index`.  The local model could
  therefore emit more entries than the small run's bounded capacity; strict validation correctly
  rejected indexes above three.
- The provider now derives `candidate_count = min(max_candidates, remaining_scenarios)` from the
  authoritative attacker evidence before either request.  A small 3/3 run emits exactly
  `candidate_01`, `candidate_02`, and `candidate_03`; neither `candidate_index` nor `selections`
  exists in either wire contract.
- Stage one is a flat object with those fixed candidate properties only.  Each property contains
  exactly a `template_key` enum over runtime-applicable policy templates.  It has no numerical
  parameters, dates, offsets, narratives, IDs, or arrays.  Zero-cost transaction friction,
  inactive rows, and policy-disallowed rows are absent structurally.
- Stage two has the identical application-owned slot keys.  Each slot has only the fields for its
  stage-one template, with `additionalProperties: false`; it cannot select a template, emit an ID,
  or mix fields from another template.  Return choices use `row_NNN` enums over legal return rows
  (row zero excluded).  Rebalance choices use `rebalance_NNN` plus exactly `minus_3`, `minus_2`,
  or `minus_1`; the provider maps these losslessly to the verified calendars.
- Exactly two local requests are possible: template selection then selected-template payload.  An
  invalid stage one stops after one and invalid stage two after two; there is no correction or
  per-candidate call.  Canonical IDs remain application-owned as `ollama-rRR-cCC`.  All
  model-owned numerical values, semantic selector choices, and narrative are passed unchanged
  except for authoritative selector-to-index/date resolution before canonical validation.

### Validation

- `python -m pytest -q --basetemp="$pytestTmp"`: PASS; **192 passed in 77.18s** using a fresh
  external `%TEMP%` directory.  New network-free checks cover the 3-slot regression, no
  selections/index surface, extra-slot rejection without truncation, row-zero exclusion, exact
  selected-template stage-two fields, and end-to-end deterministic evaluation with metrics and
  chart points.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

### Remaining risk

- The exact fixed-slot schemas are verified only with fake clients.  A bounded real local Ollama
  smoke is still required to verify its grammar implementation and to demonstrate that at least
  one policy-valid proposal reaches the deterministic engine.  No smoke was run here and no
  artifact was fabricated.

## Gate 12E redesign — Ollama JSON mode with strict local validation

**State:** Implementation and network-free verification complete on 2026-08-27; Gate 12E remains
pending a successful real local smoke.

- The latest real fixed-slot run failed before validation with `ollama_response_error: status=500`.
  The provider no longer sends any runtime JSON Schema to local Ollama, removing its grammar/schema
  compiler from this path.  Both bounded proposer calls now send exactly `format="json"`, retain
  `think=False`, the configured temperature, model, and timeout, and receive deterministic textual
  JSON contracts instead.
- Stage one locally parses and strictly validates the fixed application-owned candidate slots, each
  containing only a runtime-applicable `template_key`; extra/missing slots, unavailable templates,
  candidate IDs/indexes, and arbitrary fields fail closed after call one.  Stage two locally parses
  the same slots and validates the exact selected-template payload through the provider-local strict
  Pydantic model before canonical conversion.  Mixed-template fields, unknown selectors, wrong
  types, and extras fail closed after call two.
- The removed wire-schema helpers generated the prior flat/dynamic JSON Schema objects.  The
  provider retains strict local models, canonical policy/domain validation, semantic `row_NNN` and
  `rebalance_NNN` selector maps, authoritative component ordering and IDs, and lossless selector
  resolution.  Numerical stress values and narrative remain unchanged model-owned values.
- Safe diagnostics now distinguish JSON parse, stage-one validation, and stage-two validation
  failures in addition to the existing response/timeout categories, exposing at most bounded
  paths and error types rather than raw model output or prompts.

### Validation

- `python -m pytest -q --basetemp="$pytestTmp"`: PASS; **192 passed in 44.51s**, using a fresh
  external `%TEMP%` directory.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

### Remaining risk

- JSON mode avoids the observed schema grammar failures but has not been exercised against the
  local Ollama server from this shell.  The next bounded real smoke must prove a valid proposal
  reaches deterministic evaluation; no artifact was fabricated here.

## Gate 12E repair — isolated local stage inputs

**State:** Implementation complete locally on 2026-08-27; real smoke remains required.

- Before this repair, both calls passed `AttackerEvidenceSummary.model_dump_json()` as the Ollama
  user message.  That canonical shape included `schema_version`, `experiment_id`, and
  `round_number`, plausibly causing run-019 JSON mode output to mirror provenance fields rather
  than fixed candidate slots.
- Stage one now receives only `_Stage1SelectionContext`: `allowed_template_keys`, fixed
  `candidate_slots`, and `transaction_cost_bps`.  It contains no canonical provenance, scenario,
  telemetry, or prior-result shape.  The final system instruction is the required fixed-slot
  output contract.  Provenance-shaped output is regression-tested to fail closed as
  `ollama_stage1_validation_failure` after one call.
- Stage two also no longer receives canonical evidence: `_Stage2ParameterContext` contains only
  validated selected templates and bounded return/rebalance selector keys.  Its validation and
  two-call JSON-mode behaviour are unchanged.

### Validation

- Full suite before the final test-only formatting correction: **193 passed in 71.58s** using a
  fresh external `%TEMP%` basetemp.  Focused provider tests then passed: **20 passed in 25.85s**.
- Final `python -m ruff check .`: PASS; `All checks passed!`.
- Final `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- Final `git diff --check`: PASS (existing line-ending warnings only).

### Remaining risk

- A bounded real smoke must confirm that the smaller non-provenance contexts cause Qwen to return
  the fixed candidate slots and reach deterministic evaluation.  No real run occurred here.

## Gate 12E catalog refactor — Phases 1, 2A, and 2B

**State:** Complete locally on 2026-08-27; Gate 12E remains incomplete.

- Phase 1 extracts deterministic candidate construction and represents the resulting immutable
  `AttackCatalog` with deterministic `atk_NNN` keys, preserving the canonical scenario values.
- Phase 2A extracts the sole provider-neutral `AttackValidationContext` construction path used by
  normal attack execution and catalog prevalidation.
- Phase 2B builds the deterministic, policy-aware catalog before model exposure in
  `AttackerService`, validates every entry with the active policy and authoritative context, and
  threads the same immutable object service -> adapter -> proposer.  Policy-inapplicable entries,
  including zero-cost friction, inactive/disallowed families, a first-row return-dependent gap,
  and illegal rebalance timing, are excluded.  Malformed/internal-invalid generated candidates
  fail closed rather than entering the catalog.
- Deterministic and Foundry providers are unchanged.  Ollama still uses the existing two-stage
  numerical-generation path; catalog-key selection is Phase 3 and has not been implemented.  No
  real catalog-selection Ollama smoke was run.

### Validation

- Focused `tests/test_services.py tests/test_attack.py tests/test_ollama_clients.py`: PASS;
  **53 passed in 25.97s** (exit 0).
- `python -m pytest -q --basetemp="$pytestTmp"`: PASS; **200 passed in 60.77s** using a fresh
  external `%TEMP%` directory.  The final pytest summary was recorded; no project `.pytest_tmp`
  directory was reused.
- `python -m ruff check .`: PASS; `All checks passed!`.
- `python -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

### Remaining risk

- Phase 3 must replace the current Ollama numerical proposal path with bounded catalog-key
  selection, then a genuine bounded local Ollama smoke must confirm a valid deterministic
  evaluation.  Gate 12E must not be accepted before that work and smoke complete.

## Gate 12E catalog refactor — Phase 3A

**State:** Implemented and network-free verified locally on 2026-08-27; Phase 3 and Gate 12E
remain incomplete.

- The active `OllamaScenarioProposer` path now requires the prevalidated non-empty
  `AttackCatalog`. It derives fixed `choice_NN` slots from the application-owned candidate and
  remaining-scenario bounds, capped by catalog size, and makes exactly one JSON-mode,
  `think=False` Ollama request.
- The model sees finite plain-text, read-only catalog summaries and can return only exact unique
  catalog keys. Unknown, missing, extra, duplicate, and numerical fields fail closed with no
  repair or retry. Missing or empty catalogs fail closed before any model call.
- Selected entries are resolved application-side, copied unchanged except for application-owned
  `ollama-rRR-cCC` scenario IDs, and then pass through the existing canonical attack flow. The
  legacy two-stage numerical helpers remain present but are no longer called by production Ollama
  proposal execution. Deterministic and Foundry providers were not changed.

### Validation

- `.venv\\Scripts\\python.exe -m pytest -q tests/test_ollama_clients.py tests/test_services.py tests/test_attack.py --basetemp="$pytestTmp"`:
  **PASS; 42 passed in 17.18s** (fresh external temporary directory; no network/model calls).
- `.venv\\Scripts\\python.exe -m ruff check .`: **PASS; All checks passed!**
- `.venv\\Scripts\\python.exe -m mypy src`: **PASS; Success: no issues found in 20 source files.**
- `git diff --check`: **PASS** (exit code 0; existing line-ending warnings only).

### Remaining risk

- Phase 3B must remove obsolete two-stage Ollama helpers and tests only after the new selection
  path has a bounded real local Ollama smoke. No real Ollama call was made for Phase 3A.

## Gate 12E catalog refactor — Phase 3B cleanup

**State:** Implemented and statically/network-free verified locally on 2026-08-27; Gate 12E
remains incomplete pending real local Ollama acceptance smoke.

- Phases 1, 2A, and 2B remain complete. Phase 3 catalog-key selection is implemented: Ollama no
  longer generates numerical stress parameters, dates, selectors, component order, or scenario
  IDs. A valid proposal makes one bounded JSON-mode (`format="json"`, `think=False`) selection
  call and resolves only strict `choice_NN` keys against the prevalidated deterministic catalog.
- Removed the obsolete two-stage template/numerical-generation models, prompts, selector mapping,
  dynamic payload/schema generation, correction/retry loop, and Stage 1/Stage 2 diagnostics. The
  local selection boundary remains strict and fail-closed; selected scenario values are copied
  unchanged except for application-owned final IDs.
- Deterministic and Foundry providers are unchanged.

### Validation

- `.venv\\Scripts\\python.exe -m pytest -q --basetemp="$pytestTmp"`: **PASS; 189 passed in
  74.14s** (fresh external temporary directory; no network/model calls).
- `.venv\\Scripts\\python.exe -m ruff check .`: **PASS; All checks passed!**
- `.venv\\Scripts\\python.exe -m mypy src`: **PASS; Success: no issues found in 20 source files.**
- `git diff --check`: **PASS** (exit code 0; existing line-ending warnings only).

### Remaining risk

- A bounded real local Ollama smoke must still show a valid catalog-key selection reaches
  deterministic evaluation. Gate 12E must not be accepted until that smoke passes.

## Gate 12E defender narrative boundary repair — run-022

**State:** Implemented and network-free verified locally on 2026-08-27; Gate 12E remains
incomplete pending a fresh real local Ollama smoke and artifact export.

- The Phase 3 catalog-key attacker path remains implemented. Genuine run-022 progressed to
  defender narrative generation, where Ollama returned a schema-invalid verified claim without a
  typed mechanism. The `DefenderNarrativeBatch` validator remains strict and unchanged.
- Provider-neutral `ApplicationBoundaryError` failures from a report writer now fail closed at the
  service narrative boundary: no causal assessment is accepted, one fixed safe rejection is
  recorded, and deterministic replay/verdict/report evidence continues. No model response,
  validation payload, prompt, or exception content is retained in the rejection.
- Deterministic and Foundry report-writer valid-output behavior is unchanged. The numerical
  engine, attack metrics, replay evidence, and catalog construction are unchanged.

### Validation

- Focused `.venv\\Scripts\\python.exe -m pytest -q tests/test_services.py tests/test_ollama_clients.py tests/test_offline.py --basetemp="$pytestTmp"`:
  **PASS; 32 passed in 27.74s** (fresh external temporary directory; no network/model calls).
- `.venv\\Scripts\\python.exe -m pytest -q --basetemp="$pytestTmp"`: **PASS; 190 passed in
  74.85s** (fresh external temporary directory; no network/model calls).
- `.venv\\Scripts\\python.exe -m ruff check .`: **PASS; All checks passed!**
- `.venv\\Scripts\\python.exe -m mypy src`: **PASS; Success: no issues found in 20 source files.**
- `git diff --check`: **PASS** (exit code 0; existing line-ending warnings only).

### Remaining risk

- Rerun the real local Ollama smoke using a fresh output directory. Gate 12E remains unaccepted
  until it produces at least one valid evaluated scenario with metrics and chart points and
  completes artifact export.

## Gate 12E catalog runtime-admissibility repair — run-023

**State:** Implemented and network-free verified locally on 2026-08-27; Gate 12E remains
incomplete pending a fresh real local Ollama smoke.

- Genuine run-023 proved the Phase 3 catalog selection path reaches deterministic evaluation: a
  selected scenario was valid with metrics and 81 chart points, produced three breaches, and the
  CLI reported one verified failure. The same run exposed a catalog/runtime validation gap when a
  catalog entry changed the first asset-return row and was later rejected as invalid data.
- The source check is the backtest return-frame validator's first-row-zero requirement. Catalog
  admission previously performed only canonical and policy/context validation, so it did not run
  the stress transform followed by that authoritative return-frame check.
- A shared provider-neutral runtime-admissibility preflight now performs the existing stress
  transform and existing backtest return validation. Both catalog admission and normal scenario
  evaluation use it; first-row-invalid candidates are omitted without repair. Deterministic and
  Foundry behavior, catalog-key selection, prompts, and numerical semantics remain unchanged.

### Validation

- Focused `.venv\\Scripts\\python.exe -m pytest -q tests/test_services.py tests/test_attack.py tests/test_ollama_clients.py --basetemp="$pytestTmp"`:
  **PASS; 45 passed** (fresh external temporary directory; no network/model calls).
- `.venv\\Scripts\\python.exe -m pytest -q --basetemp="$pytestTmp"`: **PASS; 192 passed in
  96.77s** (fresh external temporary directory; no network/model calls).
- `.venv\\Scripts\\python.exe -m ruff check .`: **PASS; All checks passed!**
- `.venv\\Scripts\\python.exe -m mypy src`: **PASS; Success: no issues found in 20 source files.**
- `git diff --check`: **PASS** (exit code 0; existing line-ending warnings only).

### Remaining risk

- A fresh real local Ollama smoke must confirm that no selected catalog entry is immediately
  rejected under the repaired invariant before Gate 12E can close.

## Gate 12E — local Ollama acceptance

**State:** Complete on 2026-08-27.

- Genuine local acceptance run: `artifacts/demo/ollama-run-024`, provider `ollama`, model
  `qwen3:4b`. Ollama acts only as a bounded adversarial catalog selector: the valid path makes one
  JSON-mode call with `format="json"` and `think=False`.
- Python owns deterministic candidate construction, all numerical stress values, policy/runtime
  admissibility, dates, component order, final IDs, and deterministic evaluation. Ollama cannot
  generate raw numerical stress parameters.
- The selected `ollama-r01-c01` was valid with metrics and 81 chart points, triggered three
  risk-limit breaches (maximum normalized excess `1.198637511934236`), and defender replay
  reproduced it. The CLI recorded `status=verified` and `verified_failures=1`.
- The run completed its replay and verification events. It contains no immediate first-dataset-row
  invalid-data rejection; the run-023 catalog/runtime validation gap was repaired before this run.
  Earlier failed runs remain debugging evidence, not acceptance evidence.

### Next gate

- Gate 13A is unblocked: **Next.js frontend foundation / Verified Run Replay**. It is not
  implemented in this gate.

## Gate 13A — Next.js frontend foundation / Verified Run Replay

**State:** Complete on 2026-08-27.

- Added an isolated `frontend/` Next.js 16.3.3 App Router project using TypeScript, React
  19.2.4, ESLint 9.39.1, and no state-management or charting dependency. The replay is
  static and read-only: it neither invokes models nor evaluates strategies nor mutates artifacts.
- `frontend/scripts/sync-demo-telemetry.mjs` copies
  `artifacts/demo/ollama-run-024/demo-telemetry.json` byte-for-byte to the frontend fixture.
  The checked-in fixture hash matched the authoritative artifact:
  `898fc94cb4ace7fd7a486538686ec48b6b9d1190063f7970fdd1959d0f1ae1d9`.
- A small explicit TypeScript parser validates the required run, evaluation, chart-point, and
  event fields before the page receives them. It fails closed for malformed telemetry, non-finite
  numeric values, non-contiguous event sequences, and absent valid evaluations; rejected
  scenarios cannot be selected as replay evidence.
- The static replay page renders provider/model, reproduced verdict, truncated hash provenance,
  `ollama-r01-c01` evaluation summary, an inline-SVG baseline/stressed equity chart, stressed
  drawdown, ordered canonical event timeline, and the deterministic-evaluation versus defender-
  replay boundary. Displayed telemetry values are 3 breaches, maximum normalized excess
  `1.1986`, and 81 chart points.

### Validation

- `frontend`: `pnpm test`: PASS; **3 passed** (fixture parse/evidence fields, malformed telemetry,
  and rejected-scenario selection).
- `frontend`: `pnpm lint`: PASS.
- `frontend`: `pnpm build`: PASS; static `/` replay route generated with Next.js 16.3.3.
- `.venv\\Scripts\\python.exe -m pytest -q --basetemp="$pytestTmp"`: PASS; **192 passed** using
  a fresh external temporary directory (approximately 80 seconds of test execution).
- `.venv\\Scripts\\python.exe -m ruff check .`: PASS; `All checks passed!`.
- `.venv\\Scripts\\python.exe -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warning for `.gitignore` only).

### Next gate

- Gate 13B remains: **richer dashboard / interaction / polish**. No deployment is claimed.

## Gate 13B — Verified Run Replay dashboard interaction and analytical clarity

**State:** Complete on 2026-08-27.

- The existing read-only static replay is now structured as a quantitative-analysis dashboard:
  run overview, key results, a focused performance chart, canonical selected attack, breach
  analysis, defender verification chain, usable provenance, and phase-labelled event timeline.
- Extended the fail-closed TypeScript telemetry parser only for fields displayed in this gate:
  canonical scenario components (family/date/numeric shocks) and defender verdict/replay metric
  delta. All number values still originate in validated telemetry; no model narrative is used.
- Added an accessible client-side Equity/Drawdown segmented control. The inline SVG retains raw
  telemetry points without smoothing; hover or keyboard focus exposes exact point values. No
  charting, state-management, UI, or animation dependency was added.
- The selected attack is rendered as the canonical `one_day_gap` component and its recorded
  numeric shocks, alongside the explicit boundary that Ollama selected a prevalidated attack and
  Python owns its numerical scenario and deterministic evaluation.
- Breach analysis renders exactly the three canonical `risk_limit_breached` events. The defender
  flow shows selection -> deterministic evaluation -> breach -> replay -> `reproduced`, with
  telemetry replay metric delta `0.0`. Timeline event order remains canonical by sequence and
  highlights breach, replay completion, verification completion, and run completion.

### Validation

- `frontend`: `pnpm test`: PASS; **3 passed**. Added assertions for `reproduced`, zero replay
  delta, exact scenario shock, and exactly three risk-limit breach events; existing malformed and
  rejected-evaluation checks remain.
- `frontend`: `pnpm lint`: PASS.
- `frontend`: `pnpm build`: PASS; Next.js generated the static `/` replay route.
- `.venv\\Scripts\\python.exe -m pytest -q --basetemp="$pytestTmp"`: PASS; **192 passed** using
  a fresh external temporary directory (approximately 75 seconds of test execution).
- `.venv\\Scripts\\python.exe -m ruff check .`: PASS; `All checks passed!`.
- `.venv\\Scripts\\python.exe -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

### Next gate

- Gate 14 remains: **deployment + recruiter-facing documentation**. No deployment is claimed.

## Gate 14A — deployment readiness and recruiter-facing documentation

**State:** Complete on 2026-08-27. No public deployment occurred.

- Replaced the root README with a concise recruiter-facing overview of the research-only product,
  deterministic/LLM authority split, architecture, verified run-024 result, technology, local
  commands, reproducibility controls, and current gate status.
- Added `docs/ARCHITECTURE.md` to record provider responsibilities, typed trust boundaries,
  actual bounds, the one-call valid Ollama catalog-selection path, and defender verification.
- Added `docs/DEPLOYMENT.md` with Vercel as the preferred frontend target and Azure Static Web
  Apps as the fallback. The standalone replay deploys from `frontend/` with Node 22, pnpm, and no
  environment variables; it has no Python, Ollama, credentials, or local-path runtime dependency.
- Updated frontend metadata and viewport configuration and ignored Vercel local deployment state.
  The existing Next.js deployment mode was retained; no static-export or visual redesign was made.
- The tracked production fixture remains `frontend/src/fixtures/demo-telemetry.json`; it is synced
  byte-for-byte from the run-024 artifact. The lockfile remains tracked. A tracked-file scan found
  no runtime absolute user path or credential material; historical status records retain prior
  local command paths and test fixtures retain synthetic private-key marker bytes.

### Validation

- `frontend` with the desktop runtime Node path: `pnpm test`: PASS; **3 passed**.
- `frontend` with the desktop runtime Node path: `pnpm lint`: PASS.
- `frontend` with the desktop runtime Node path: `pnpm build`: PASS; Next.js 16.3.3 completed the
  optimised production build.
- `.venv\Scripts\python.exe -m pytest -q --basetemp="$pytestTmp"`: PASS; **192 passed** using a
  fresh external temporary directory.
- `.venv\Scripts\python.exe -m ruff check .`: PASS; `All checks passed!`.
- `.venv\Scripts\python.exe -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

### Next gate

- Gate 14B — actual public deployment and live verification.

## Gate 14B — public Vercel deployment and live verification

**State:** Complete on 2026-08-27.

- Public Vercel deployment succeeded at <https://strategy-redteam-lab.vercel.app/>. The verified
  production route is `/`; it serves the static, read-only Next.js replay from `frontend/`.
- Live verification confirmed the run-024 replay renders provider `ollama`, model `qwen3:4b`,
  valid `ollama-r01-c01`, three breaches, maximum normalized excess `1.198637511934236`, 81 chart
  points, canonical `one_day_gap` evidence, defender replay completion, `reproduced` verdict,
  replay delta `0.0`, and the ordered telemetry timeline.
- The deployment has no environment variables and no runtime Python backend, Ollama process, or
  model credentials. It is not a live trading system and does not alter evaluation artifacts.
- No Vercel local state, account/project/team identifiers, tokens, GitHub tokens, API secrets, or
  absolute user filesystem paths were introduced by this gate.

### Validation

- Live production `/` route: PASS; visible replay evidence matched the recorded run-024 values and
  defender verification chain.
- `frontend` with the desktop runtime Node path: `pnpm test`: PASS; **3 passed**.
- `frontend` with the desktop runtime Node path: `pnpm lint`: PASS.
- `frontend` with the desktop runtime Node path: `pnpm build`: PASS; Next.js 16.3.3 generated the
  static `/` route.
- `python -m ruff check .`: unavailable in the ambient Python environment (`ruff` is not
  installed); the repository virtual environment command `.venv\Scripts\python.exe -m ruff check
  .`: PASS; `All checks passed!`.
- `python -m mypy src`: likewise run through the repository virtual environment:
  `.venv\Scripts\python.exe -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS (existing line-ending warnings only).

### Next gate

- Gate 15 — clean-clone / end-to-end release acceptance.

## Gate 15 repair checkpoint — canonical manifest LF checkout

**State:** Gate 15 remains pending on 2026-08-27.

- The failed Windows clean-clone offline workflow was traced to Git converting the canonical
  `tests/fixtures/offline-cache/manifests/correlation-break.json` checkout from LF to CRLF. Its
  Git index blob was already canonical LF; no immutable manifest content was changed.
- Added the narrow checkout rule
  `tests/fixtures/offline-cache/manifests/*.json text eol=lf` in `.gitattributes`. It applies only
  to the canonical immutable manifest-fixture directory. Effective attributes for the manifest
  are `text: set` and `eol: lf`; the current repository reports `i/lf w/lf`.
- Focused data/offline tests and the full backend suite retain canonical-manifest validation; no
  parser, hash, runtime normalisation, numerical-engine, provider, or frontend behaviour changed.

### Validation

- `tests/test_data.py tests/test_offline.py`: PASS; **21 passed in 15.08s**.
- `.venv\Scripts\python.exe -m pytest -q --basetemp="$pytestTmp"`: PASS; **192 passed in
  79.78s** using a fresh external temporary directory.
- `.venv\Scripts\python.exe -m ruff check .`: PASS; `All checks passed!`.
- `.venv\Scripts\python.exe -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS.

### Remaining release check

- Full Gate 15 acceptance must be rerun from a new published commit so a fresh clone receives the
  committed `.gitattributes` rule and proves `w/lf` checkout plus the documented offline workflow.

## Gate 15 repair checkpoint — byte-sensitive prompt Markdown

**State:** Gate 15 remains pending on 2026-08-27.

- Windows clean-clone LF preservation was extended to the fixed root-level `prompts/*.md` templates
  consumed by the shared raw-byte prompt loader. Their Git blobs were already canonical LF; no
  prompt content or application behaviour changed.

### Validation

- `tests/test_services.py::test_successful_verified_failure_uses_compact_prompts_and_engine_numbers`:
  PASS; **1 passed in 13.50s**.
- `.venv\Scripts\python.exe -m pytest -q --basetemp="$pytestTmp"`: PASS; **192 passed in
  88.80s** using a fresh external temporary directory.
- `.venv\Scripts\python.exe -m ruff check .`: PASS; `All checks passed!`.
- `.venv\Scripts\python.exe -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS.

### Remaining release check

- Full Gate 15 acceptance must be rerun from a new published commit.

## Gate 15 repair checkpoint — release telemetry provenance

**State:** Gate 15 remains pending on 2026-08-27.

- The accepted authoritative run-024 telemetry at
  `artifacts/demo/ollama-run-024/demo-telemetry.json` is now release-tracked; all other generated
  artifact paths remain ignored. The tracked frontend fixture
  `frontend/src/fixtures/demo-telemetry.json` remains byte-identical to that source.
- Both byte-sensitive JSON files are forced to LF checkout. The accepted telemetry evidence was
  inspected without regeneration and retains its recorded `ollama` / `qwen3:4b`, valid
  `ollama-r01-c01`, three-breach, `reproduced`, zero-delta result.

### Validation

- `pnpm sync:fixture`: PASS; source and frontend fixture SHA-256 both
  `898fc94cb4ace7fd7a486538686ec48b6b9d1190063f7970fdd1959d0f1ae1d9` and byte-identical.
- `frontend`: `pnpm test`: PASS; **3 passed**. `pnpm lint`: PASS. `pnpm build`: PASS; static `/`
  route generated.
- `.venv\Scripts\python.exe -m pytest -q --basetemp="$pytestTmp"`: PASS; **192 passed in
  95.90s** using a fresh external temporary directory.
- `.venv\Scripts\python.exe -m ruff check .`: PASS; `All checks passed!`.
- `.venv\Scripts\python.exe -m mypy src`: PASS; `Success: no issues found in 20 source files`.
- `git diff --check`: PASS.

### Remaining release check

- Complete Gate 15 acceptance must be rerun from a new published clone.

## Gate 15 — clean-clone end-to-end release acceptance

**State:** Complete on 2026-08-27.

**Gate 15 COMPLETE — clean-clone release acceptance passed.**

- Release candidate `ebd5397b8d6f6e8939e5f53fdaf71ef42dac6b26` was frozen with clean
  current-repository and fresh-clone Git state.
- Windows LF contracts were verified in the fresh clone for the canonical manifest, both fixed
  prompt sources, the release-tracked run-024 telemetry, and the frontend replay fixture
  (`i/lf w/lf` for each).
- A fresh Python 3.12.13 environment installed `.[dev,hosted]`; the full suite passed with
  **192 passed in 99.72s**, Ruff passed, and mypy reported no issues in 20 source files.
- The documented bounded offline workflow completed with verified artifacts, populated data and
  configuration SHA-256 values, and successful artifact/schema verification; no network data or
  model provider was used.
- The release-tracked run-024 evidence retained the recorded reproduced, zero-delta, valid
  three-breach result. Its SHA-256 and the frontend fixture SHA-256 both equal
  `898fc94cb4ace7fd7a486538686ec48b6b9d1190063f7970fdd1959d0f1ae1d9`; byte equality and
  `pnpm sync:fixture` provenance checks passed without tracked changes.
- Fresh frontend installation, tests (**3 passed**), lint, and production build passed; Next.js
  produced the static `/` route. No runtime Python, Ollama, model credentials, or environment
  variables are required by the replay.
- Clean-clone Git status and `git diff --check` passed. The tracked-content audit found no local
  build state, deployment metadata, credentials/tokens, or active user-specific runtime paths.
- Read-only production smoke at <https://strategy-redteam-lab.vercel.app/> returned HTTP 200 and
  contained the expected lab title, `ollama-r01-c01`, `qwen3:4b`, and `reproduced` evidence.

### Next recommended phase

- Recruiter-facing frontend polish and final portfolio/repository review.
