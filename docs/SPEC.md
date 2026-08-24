# Trading-Strategy Red-Team Lab Specification

**Status:** Scope frozen for Gate 0

**Date:** 2026-08-23

**Audience:** Researchers and engineers evaluating strategy fragility

## 1. Purpose

Build a research-only system that tries to break a trading strategy and explains the conditions, timing, and mechanism of failure. The system evaluates structured stress scenarios with deterministic code, independently replays the strongest results, and preserves enough provenance to reproduce or reject every claim.

The MVP target is a transparent monthly rebalanced 60/40 portfolio of SPY and TLT. A CSV adapter also accepts externally generated daily SPY/TLT weights so a strategy produced elsewhere can later be tested without executing external code.

This specification adopts the boundary in [ADR-001](ADR-001-agent-boundary.md): agents propose and audit; deterministic code owns all numerical market results.

## 2. Goals

- Reproduce a baseline strategy without look-ahead bias on an immutable daily adjusted-price dataset.
- Evaluate a small, typed, bounded set of adversarial scenarios.
- Identify the failed assumption or relationship, numeric stress, breach onset, worst window, loss propagation, and breached rule.
- Have an independent defender reload the evidence and replay the top results.
- Preserve dataset, configuration, code, seed, scenario, agent I/O, engine output, and tracing provenance.
- Support local deterministic execution first and a two-agent Microsoft Foundry deployment boundary later.

## 3. Non-goals

- Generating, optimizing, ranking, or recommending investment strategies.
- Investment advice, return forecasts, portfolio suitability claims, or claims of future market likelihood.
- Broker connectivity, order generation, execution, paper trading, or live trading.
- Intraday data, options, derivatives, tax modelling, or market-impact modelling in the MVP.
- Letting a model calculate performance, mutate engine evidence, execute narrative content, or choose an unbounded search.
- Building the cloud resources or production Python during Gate 0.
- Using the retiring Microsoft Foundry visual Workflow designer.

## 4. System boundary

The final cloud design has four logical parts:

1. **Deterministic Python engine.** Loads a verified immutable dataset, applies strategy timing and scenario transforms, evaluates failure rules, ranks results, and emits typed evidence.
2. **Attacker Hosted Agent.** Proposes schema-valid scenario batches and runs a bounded loop against the engine. It may use prior engine results to propose the next batch but cannot change calculated values.
3. **Defender Hosted Agent.** Independently reloads the same dataset, verifies provenance, replays up to the top three scenarios through the engine, rejects invalid evidence, and writes a report from verified outputs.
4. **Bounded client orchestrator.** Invokes the attacker once and defender once, propagates experiment and trace identifiers, enforces the overall timeout, and saves artifacts.

Azure Blob Storage holds immutable datasets, manifests, run artifacts, and reports. Each Hosted Agent receives a managed identity and only the Blob permissions required for its role. Application Insights receives Foundry telemetry and custom OpenTelemetry traces. Agent endpoint lifecycle, identity, scaling, and session facilities do not replace application-owned validation, retries, budgets, or stopping rules.

## 5. Technology and repository conventions

- Python 3.11 with a `src` package layout.
- Pydantic v2 for all boundary schemas; pandas/NumPy for vectorized numerical work.
- pytest, Ruff, and mypy for acceptance; Typer for an eventual local CLI.
- yfinance may be used only by an explicit ingestion command or manual smoke test. Tests and evidence replay use immutable local fixtures/blobs and never make network calls.
- Configuration is explicit and serializable. No result may depend on ambient time, unordered iteration, hidden model state, or an unrecorded random seed.

## 6. Data policy and manifest

### 6.1 Dataset

The MVP consumes one canonical, tabular daily dataset with a UTC-normalized market date and adjusted prices for SPY and TLT. Rows are unique, strictly increasing by date, aligned to a common trading calendar, and contain finite positive prices. Returns are derived only after validation. Missing prices, duplicate dates, non-monotonic dates, or non-finite values are typed validation failures; they are not filled silently.

Ingestion writes a content-addressed, immutable object before evaluation. Reusing a provider response under the same mutable filename is not acceptable evidence. A live download is a separate manual action, never part of a test or automatic replay.

### 6.2 Manifest

Every dataset has a canonical manifest with at least:

- schema version and dataset identifier;
- provider and provider-specific source identifiers;
- ordered symbols;
- inclusive first and last market dates;
- adjustment policy, including treatment of splits and distributions;
- trading-calendar/alignment policy;
- row count and column contract;
- retrieval timestamp in UTC;
- media type and byte length; and
- SHA-256 of the exact immutable data bytes.

The manifest itself is serialized canonically and hashed; that manifest-file hash is stored by the run/artifact index rather than inside the self-hashed document. The engine verifies the data-byte hash, manifest-file hash, and manifest/data agreement before calculating any result. A mismatch stops the run.

### 6.3 External-weight CSV

The MVP adapter accepts `date`, `SPY`, and `TLT` columns. Dates must match the evaluated trading dates, weights must be finite, and each weight must be in `[0, 1]` with each row summing to `1` within a configured numerical tolerance. Dates cannot be duplicated or silently filled. Values are neither normalized nor clipped. This deliberately narrow contract can be broadened only by a later specification decision.

## 7. Strategy and execution timing

For adjusted price `P[t]`, the asset simple return for market date `t` is `P[t] / P[t-1] - 1`. The portfolio return earned on `t` uses weights fixed at the preceding close: `r_portfolio[t] = sum(weights[t-1] * asset_returns[t])`. Thus information first available at the close of `t` can affect only the return on `t+1` or later.

The built-in strategy chooses target weights of 0.60 SPY and 0.40 TLT at the initial dataset close and at the first observed market close of each new calendar month. This schedule is prefix-invariant: extending a dataset cannot retrospectively label an earlier row as a rebalance decision. Each choice becomes effective for the next observed market return. The first dataset row therefore earns no portfolio return. The MVP assumes frictionless rebalancing, no fees, no tax, no slippage, and no cash yield; these are report limitations, not implicit claims of realism.

External weights use the same one-row lag. A weight stamped with date `t` is information available at that close and first earns the return on the next market row. No adapter may backfill a missing decision.

## 8. Typed schemas and trust boundary

All cross-component payloads use versioned Pydantic v2 models with forbidden unknown fields. Identifiers are opaque data, not paths or commands. At minimum the implementation will define:

- `DatasetManifest`: the fields in Section 6.2, including the data-byte hash; the enclosing artifact reference supplies the manifest-file hash.
- `ExperimentConfig`: dataset identifier/hash, strategy configuration, failure thresholds, hard budgets, seed, timeout, code version, and numeric tolerance.
- `ScenarioProposal`: stable scenario ID, one discriminated numeric scenario payload, optional untrusted narrative metadata, proposer/round metadata, and schema version.
- `ScenarioResult`: input/config/data hashes, validity verdict or typed rejection, deterministic metrics, breach events, ranking fields, timing, and engine version.
- `DefenceVerdict`: scenario ID, provenance checks, replay metrics, comparison result, rejection reasons, and `reproduced`, `not_reproduced`, or `invalid_evidence` verdict.
- `FailureReport`: experiment provenance, baseline summary, verified scenarios, causal explanation tied to numeric engine evidence, limitations, and defender verdicts.

Scenario validation rejects unknown fields, unsupported symbols, bad dates/windows, non-finite values, out-of-domain values, oversized collections, duplicate IDs, or budget violations. It never clips or repairs them. Narrative text is stored and rendered only as escaped/untrusted metadata; it is never interpreted as code, a URL to fetch, a filesystem path, or a tool instruction.

## 9. Scenario semantics

Every scenario declares an evaluation window inside the immutable dataset. Date transforms operate on asset log gross returns so stressed prices cannot cross zero. Unless stated otherwise, a numeric shock is incremental to the observed return path and the engine retains both baseline and stressed series.

### 9.1 Historical window

Selects an inclusive, contiguous historical date range from the verified dataset and evaluates the unchanged strategy and failure rules on that regime. Both endpoints must be observed market dates and the window must be long enough for configured rolling metrics.

### 9.2 One-day gap

Declares one observed market date and an explicit simple-return shock by symbol, each strictly greater than `-1`. On that date, stressed gross return equals observed gross return multiplied by `1 + shock`. Omitted symbols receive no incremental shock.

### 9.3 Sustained cumulative shock

Declares an observed start date, bounded number of consecutive market rows, and a cumulative simple-return shock by symbol, each strictly greater than `-1`. For `n` rows the engine applies the constant daily log increment `log(1 + cumulative_shock) / n`; the incremental gross effect over the full interval is therefore exactly `1 + cumulative_shock`.

### 9.4 Volatility multiplier

Declares a valid window, symbols, and a finite multiplier greater than zero. For each selected symbol, the engine multiplies demeaned observed log returns in that window by the multiplier and then restores the original mean. A zero-variance source window is rejected. This preserves the selected window's log-return mean while changing dispersion deterministically.

### 9.5 Correlation target

Declares a valid window and a finite SPY/TLT target correlation strictly between `-1` and `1`. The engine standardizes window log returns, whitens them with the unique symmetric inverse square root of the observed correlation matrix, and colors them with the unique symmetric square root of the target correlation matrix. It then restores each asset's observed mean and standard deviation. A singular/ill-conditioned source matrix, invalid target matrix, or zero-variance series is rejected rather than approximated or clipped.

### 9.6 Synthetic headline

A synthetic headline is untrusted narrative metadata attached to an ordered, schema-bounded list of the numeric transforms above. The numeric list is the complete executable meaning. Components are applied in recorded order; conflicting components or a list over the configured component cap are rejected. The headline itself has no numerical or operational effect.

## 10. Failure rules and timing

Thresholds are positive configuration values. All rolling windows contain exactly 20 observed portfolio-return rows and use no future rows.

- **Maximum drawdown:** breach when stressed wealth falls more than the configured fraction below its running high-water mark. Breach onset is the first violating trough date; the worst window runs from its preceding peak through the deepest trough.
- **Rolling 20-day loss:** breach when the compounded stressed return over a trailing 20-row window is less than the negative configured loss limit. Onset is the first violating window end; the worst window is the most negative valid 20-row interval.
- **Realized-volatility multiple:** breach when annualized sample standard deviation of stressed daily returns over a trailing 20-row window exceeds the configured multiple of the corresponding unstressed strategy window, using `sqrt(252)`. A zero or unavailable baseline denominator makes that window non-evaluable and must be disclosed, not replaced.

For each rule, the engine records observed value, threshold, normalized excess, onset, worst-window endpoints, and affected positions. Normalized excess is zero without a breach and otherwise the observed adverse magnitude divided by its limit minus one. Results rank deterministically by: breach count descending, maximum normalized excess descending, total normalized excess descending, worst portfolio-loss magnitude descending, then scenario ID ascending. If fewer than three valid scenarios exist, all valid scenarios are sent to defence.

## 11. Attack responsibility and budgets

The attacker may propose at most eight candidates per round and may use only validated engine summaries to inform a later round. The engine validates, deduplicates, evaluates, and ranks candidates. Invalid proposals consume their candidate slot and produce a typed rejection. Duplicate scenario IDs or equivalent canonical numeric payloads are rejected.

Hard limits are:

| Limit | Value |
|---|---:|
| `MAX_ROUNDS` | 3 |
| `MAX_CANDIDATES_PER_ROUND` | 8 |
| `MAX_TOTAL_SCENARIOS` | 24 |
| `TOP_K` | 3 |

A deterministic seed and configurable wall-clock timeout are mandatory. Reaching a budget or timeout ends the attack cleanly with a recorded stop reason. Retries are explicitly capped and count against the overall timeout. Computation is vectorized across dates; Python iteration is permitted only across the bounded candidate collection.

## 12. Defence responsibility

The defender receives immutable references, hashes, versioned configuration, seed, canonical top-scenario payloads, and attacker evidence—not mutable in-memory series. For each of up to three scenarios it:

1. reloads the dataset independently and verifies data and manifest hashes;
2. verifies schema, code/config version, scenario identity, budgets, and provenance;
3. reruns the same deterministic engine from canonical inputs;
4. compares all material metrics and event dates using the configured tolerance; and
5. marks evidence `reproduced`, `not_reproduced`, or `invalid_evidence`, with reasons.

Only reproduced evidence may be described as a verified failure. The defender cannot repair evidence or substitute its own calculated prose values. A hash/version mismatch is invalid evidence, not a failed investment result.

## 13. Orchestration, provenance, and artifacts

One bounded client run creates an experiment ID and root trace ID, invokes the attacker Hosted Agent once, invokes the defender Hosted Agent once with the selected evidence, and writes final artifacts. The orchestrator does not host an open-ended agent conversation.

The immutable run bundle contains at least:

- dataset and manifest identifiers/hashes;
- canonical experiment configuration and hash;
- code version, dependency/environment summary, and deterministic seed;
- experiment, agent invocation, and trace/span IDs;
- sanitized agent inputs and raw structured outputs;
- validation errors, canonical scenarios, stop reason, and engine results;
- defender replay inputs, results, comparisons, and verdicts; and
- final report plus an artifact index containing hashes.

Secrets, tokens, credentials, and unrestricted raw environment dumps are excluded. Artifact writes are append-only/content-addressed for a completed run.

## 14. Report contract

The final report begins with a research-only/not-investment-advice notice and answers, for each verified scenario:

- What assumption or market relationship failed?
- Which explicit numeric stress caused it?
- When did failure begin, and what was the worst window?
- How did asset shocks propagate through effective positions into portfolio loss?
- Which configured failure rule was breached, by how much?
- Did defence reproduce it with the same dataset hash and code/config version?
- Which data, modelling, execution, and scenario limitations prevent broad interpretation?

Supporting metrics are evidence, not a single strategy score. Invalid and non-reproduced evidence is listed separately and cannot support the conclusions.

## 15. Acceptance criteria

### Gate 0

- Only `AGENTS.md`, `docs/SPEC.md`, `docs/STATUS.md`, `docs/ADR-001-agent-boundary.md`, and `.gitignore` are created.
- These files agree on scope, timing, authority, trust boundaries, budgets, and status and contain no implementation claims.
- Local Markdown links resolve, formatting checks available in the repository pass, and `git diff --check` passes.

### MVP implementation gates

Later gates remain pending and must define their exact work before editing. Collectively, the MVP is acceptable only when:

- repeated runs over the same fixture, config, code, and seed produce matching hashes and evidence;
- a timing test proves close-`t` information first affects return `t+1`;
- manifest or byte tampering is detected before calculation;
- each scenario family has deterministic success, boundary, and typed-rejection tests;
- all three failure rules have onset and worst-window tests;
- attacker budgets, timeout, deduplication, and typed validation are enforced;
- defender replay reproduces valid evidence and rejects altered data/config/results;
- the report answers every question in Section 14 using engine-sourced numbers;
- unit tests complete without network access, skips, expected failures, or placeholders; and
- `python -m pytest -q`, `python -m ruff check .`, `python -m mypy src`, `git diff --check`, and `git status --short` have recorded outcomes.
