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

## Later gates

**State:** Pending — not started. Their exact scopes and done conditions must be supplied or approved before work begins.
