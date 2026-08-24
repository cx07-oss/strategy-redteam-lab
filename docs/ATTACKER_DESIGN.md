# Attacker Hypothesis Policy

**Policy version:** 1.0

**Machine policy:** `config/attack-policy-v1.yaml` with `schema_version: "1.0"`

**Scope:** bounded hypothesis selection for the fixed monthly 60/40 SPY/TLT strategy. This is a
research and adversarial-testing policy, not investment advice. It adds no transform, metric,
agent, model call, orchestration, or cloud resource. The deterministic Python engine remains the
only authority for dates, weights, returns, costs, metrics, and failure evidence.

## Four-hypothesis table

| Hypothesis | Mechanism | Variables the attacker may change | Allowed range | Expected observable | Invalid if |
| --- | --- | --- | --- | --- | --- |
| Inflation correlation break | Positive stock-bond correlation, higher volatility, and simultaneous losses remove 60/40 diversification. | Correlation target; correlation/volatility duration; volatility multiplier; SPY and TLT cumulative shocks; shock duration. | Correlation `[0.25, 0.90]`; multiplier `[1.25, 3.00]`; correlation/volatility duration integer `[20, 126]` rows; SPY shock `[-0.25, -0.05]`; TLT shock `[-0.20, -0.04]`; shock duration integer `[5, 20]` rows. The proposed 60-row maximum is reduced to Gate 6's tighter 20-row policy maximum. | The correlation component's achieved SPY/TLT correlation is within `ExperimentSpec.numeric_tolerance` of `target_correlation`; both engine-linked asset loss contributions are negative; at least one configured failure rule breaches. | A transform is singular or ill-conditioned; a value is non-finite or outside its applicable bounds; the correlation/volatility window has fewer than 20 or more than 126 observed rows; the shock has fewer than 5 or more than 20 observed rows; SPY or TLT is absent. |
| Rebalance timing gap | A loss occurs while holdings have drifted away from target weights shortly before a predetermined rebalance. | Rebalance-relative date offset; SPY/TLT one-day gap. | Offset is one of `{-3, -2, -1}` observed trading rows; SPY gap `[-0.15, -0.03]`; TLT gap `[-0.12, -0.02]`; at least one sleeve is shocked. | Pre-gap effective weights differ from the fixed `0.60/0.40` target by more than `ExperimentSpec.numeric_tolerance` in at least one sleeve; the stale-weight gross return is at least `0.0025` (25 bps) below the target-weight counterfactual; every configured breach, including an empty set, is recorded. | A rebalance date uses future returns, an end-of-sample label, a failure label, or defender output; the resolved observed date is absent; the gap occurs on or after the rebalance; a shock is outside its symbol-specific bounds. |
| Volatility regime jump | A volatility-scaled strategy reacts slowly after a sudden volatility increase. | Lookback window; multiplier; stress duration. | Only for a strategy explicitly declaring a volatility-sizing rule; lookback integer `[20, 60]` observed rows; multiplier `[1.50, 3.00]`; duration integer `[5, 20]` observed rows. The proposed multiplier `4.00` and duration 60 are reduced to Gate 6's tighter maxima. | Realized volatility rises before the next sizing decision; weights later change according to the declared sizing rule; every configured breach, including an empty set, is recorded. | The fixed monthly 60/40 strategy is used; strategy metadata does not prove a volatility-sizing rule; the baseline window has zero variance or fewer than 20 observed rows. |
| Trading-friction break | Rebalancing during stressed liquidity makes turnover materially costly. | Transaction-cost multiplier; evaluation window. | Multiplier `[2.00, 5.00]`. The proposed maximum `20.00` is reduced to Gate 6's tighter `5.00`; the resulting cost remains inside Gate 5's `[0, 10000)` bps engine interval. | The incremental transaction-cost contribution is at least `0.005` (50 bps) and at least `10%` of absolute scenario loss; every configured breach, including an empty set, is recorded. | Baseline `ExperimentSpec.transaction_cost_bps` is zero; no trade occurs in the inclusive evaluation window; the resulting cost is outside `[0, 10000)` bps. |

1. All ranges are inclusive unless explicitly stated otherwise.
2. Durations and offsets are integer observed-market rows, not calendar days.
3. Scenario components execute sequentially in their listed order.
4. The attacker may propose no more than the existing Gate 6 budget: 3 rounds, 8 candidates per
   round, and 24 candidates total.
5. The attacker must not enumerate the full Cartesian product of every permitted value.
6. Out-of-range proposals are rejected, never clamped.
7. The narrative cannot alter returns, weights, dates, or costs.
8. Every narrative claim must map to explicit structured components.
9. Failure to produce the expected observable is a valid negative result, not an invalid scenario.
10. The attacker cannot choose dates using future performance, later failure labels, or defender
    results.
11. Semantic duplicate detection ignores narrative wording and uses the ordered numeric components
    and evaluation window.
12. Unsupported hypotheses, including volatility-sizing failure against fixed 60/40, are labelled
    `not_applicable`.

## Applicability and outcomes

For the fixed `monthly_60_40` strategy, inflation correlation break, rebalance timing gap, and
trading-friction break are applicable. Volatility regime jump is `not_applicable` because
`StrategySpec(kind="monthly_60_40")` has a calendar-based monthly reset and no volatility lookback,
volatility target, sizing threshold, or volatility-triggered weight decision. The existing
`volatility_multiplier` transform may still be an ordered component of the inflation correlation
break; its existence does not create a volatility-sizing rule.

The three outcomes are disjoint:

- **Invalid proposal:** schema, `AttackPolicy`, hypothesis-specific, transform-preflight, budget,
  duplicate, date-resolution, or numerical-conditioning validation fails. It is not evaluated as
  market evidence. If emitted as a candidate, it consumes the existing Gate 6 candidate slot and
  receives a typed rejection; it is never repaired or clamped.
- **Applicable but unsuccessful hypothesis:** the scenario is valid, is deterministically
  evaluated, and does not produce every expected observable. It remains a valid negative result
  and must not be discarded. No breach is represented by an empty `StressResult.breaches` and
  `breach_count == 0`, not by rejection.
- **Not-applicable hypothesis:** the strategy lacks the mechanism named by the hypothesis. No
  `StressScenario` is constructed, no unknown `not_applicable` field is added to a Pydantic model,
  and the table records the literal policy label `not_applicable`. This Gate 9A label consumes no
  candidate slot because no proposal is emitted.

## Numeric translation and field resolution

> Every synthetic headline must include inert narrative and a complete numeric translation using
> existing `StressComponent` fields. Numeric transformations are determined exclusively by the
> structured components. Unknown fields, omitted required values, and out-of-policy values are
> rejected.

For every applicable table hypothesis, Gate 9A requires non-empty `StressScenario.hypothesis` and
`StressScenario.headline` plus one to eight ordered `StressScenario.components`. The existing
Pydantic contract requires `hypothesis` but permits `headline=None`; Gate 9A does not expand that
validator. A later attacker implementing this table must supply the headline, while the numeric
translation remains exclusively in `components`. Text, including numbers written in either
narrative field, has no effect on evaluation.

Every numeric object must pass, without modification and in this order:

1. the existing Pydantic `StressScenario` and nested `StressComponent` contracts;
2. the Gate 6 `AttackPolicy` loaded from `config/attack-policy-v1.yaml`;
3. Gate 5 transform preflight against the immutable return frame; and
4. existing duration, eight-component, return-domain, variance, and correlation-conditioning
   checks.

The existing engine then applies `components[0]`, `components[1]`, and later components
left-to-right. No component is sorted or reordered. Each component consumes the private output of
its predecessor. The array order is part of the Gate 6 semantic hash.

### Common scenario fields

| Policy concept | Existing field and deterministic resolution | Unit and boundary |
| --- | --- | --- |
| Policy version | `StressScenario.schema_version`, every `StressComponent.schema_version`, and `AttackPolicy.schema_version` are exactly `"1.0"`. | Exact string, no coercion. |
| Evaluation window | `StressScenario.evaluation_start` and `evaluation_end`. Both are observed market dates; endpoints are inclusive. | ISO `YYYY-MM-DD`; start is on or before end. |
| Narrative | `StressScenario.hypothesis` and `headline`. | Inert text; `hypothesis` is 1-4000 characters and `headline` is 1-500 characters when present. |
| Numeric translation | `StressScenario.components`. | Ordered list of 1-8 unique `StressComponent` objects. |
| Symbols | `StressComponent.shocks` keys or `symbols`, depending on family. | Only `SPY` and `TLT`; no missing table-required sleeve. |

All return shocks are incremental simple-return fractions. For example, `-0.15` means a negative
15% incremental shock, and the transform multiplies observed gross return by `1 - 0.15`. The schema
requires each shock to be finite and strictly greater than `-1`; the checked-in policy adds tighter
inclusive ranges. A transformed return at or below `-1` is rejected by Gate 5.

One basis point is `0.0001` of return and 50 bps is `0.005`. Correlations and multipliers are
dimensionless.

### Inflation correlation break

The policy variables resolve to three existing components in this exact order:

1. `StressComponent(family="volatility_multiplier")`: the shared correlation/volatility window
   resolves to inclusive `start_date` and `end_date`, `symbols` is exactly `["SPY", "TLT"]`, and
   `volatility_multiplier` is `[1.25, 3.00]`.
2. `StressComponent(family="correlation_target")`: it uses the same inclusive `start_date` and
   `end_date`; `target_correlation` is `[0.25, 0.90]`.
3. `StressComponent(family="sustained_cumulative_shock")`: `start_date` is an observed date,
   `duration_rows` is `[5, 20]`, and `shocks` contains both symbol-specific values.

The correlation/volatility duration has no schema field of its own. Before component construction,
the resolver chooses two observed dates whose inclusive row count is an integer in `[20, 126]` and
writes those dates to both components. The shock duration maps directly to `duration_rows`; Gate 6's
20-row maximum is tighter than the proposed 60-row maximum.

The expected correlation is the correlation component's immediate
`ComponentTransformSummary.post_transform_summary.spy_tlt_correlation`, before the later sustained
shock. It must differ from `target_correlation` by no more than
`ExperimentSpec.numeric_tolerance`. Negative SPY and TLT contributions are read only from the
engine's `WorstWindowEvidence.asset_return_contributions` for a breached window.

#### Valid JSON

```json
{
  "schema_version": "1.0",
  "scenario_id": "h1-inflation-valid",
  "evaluation_start": "2024-01-02",
  "evaluation_end": "2024-04-23",
  "components": [
    {
      "schema_version": "1.0",
      "family": "volatility_multiplier",
      "start_date": "2024-01-03",
      "end_date": "2024-02-27",
      "symbols": ["SPY", "TLT"],
      "volatility_multiplier": 2.0
    },
    {
      "schema_version": "1.0",
      "family": "correlation_target",
      "start_date": "2024-01-03",
      "end_date": "2024-02-27",
      "target_correlation": 0.75
    },
    {
      "schema_version": "1.0",
      "family": "sustained_cumulative_shock",
      "start_date": "2024-02-28",
      "duration_rows": 20,
      "shocks": {"SPY": -0.15, "TLT": -0.10}
    }
  ],
  "hypothesis": "Inflation pressure makes both sleeves volatile, positively correlated, and loss-making.",
  "headline": "Inflation shock removes stock-bond diversification"
}
```

This is a valid proposal, not a claim that the expected observable will occur.

#### Invalid JSON

```json
{
  "schema_version": "1.0",
  "scenario_id": "h1-inflation-invalid-duration",
  "evaluation_start": "2024-01-02",
  "evaluation_end": "2024-04-23",
  "components": [
    {
      "schema_version": "1.0",
      "family": "volatility_multiplier",
      "start_date": "2024-01-03",
      "end_date": "2024-02-27",
      "symbols": ["SPY", "TLT"],
      "volatility_multiplier": 2.0
    },
    {
      "schema_version": "1.0",
      "family": "correlation_target",
      "start_date": "2024-01-03",
      "end_date": "2024-02-27",
      "target_correlation": 0.75
    },
    {
      "schema_version": "1.0",
      "family": "sustained_cumulative_shock",
      "start_date": "2024-02-28",
      "duration_rows": 21,
      "shocks": {"SPY": -0.15, "TLT": -0.10}
    }
  ],
  "hypothesis": "Inflation pressure makes both sleeves volatile, positively correlated, and loss-making.",
  "headline": "Inflation shock exceeds the bounded stress duration"
}
```

The Pydantic scenario contract accepts 21 because its engine-wide cap is 252. `AttackPolicy`
rejects it for the intended reason: `duration_rows is outside the policy range` `[5, 20]`. It is
not clamped to 20.

### Rebalance timing gap

No `date_offset` field is introduced. Let `R` be a built-in rebalance decision date obtained only
from `FixedMonthly6040Strategy.rebalance_dates`: the initial observed close or the first observed
market close of a later calendar month. For offset `k` in `{-3, -2, -1}`, resolve
`StressComponent.date = market_dates[index(R) + k]`. The resolved row must exist, be strictly
before `R`, and fall inside the inclusive scenario evaluation window. The resolver uses only the
immutable market calendar and declared strategy schedule; it cannot inspect returns or failure
evidence.

The SPY and TLT one-day gaps map to `StressComponent(family="one_day_gap").shocks`. At least one
key is present. The one-day transform is applied on the resolved observed date.

For the expected observable on gap date `d`, the deterministic engine uses the stressed return
vector and its existing backtest arrays:

- stale weights are `BacktestResult.effective_weights.loc[d]`, which were fixed at the preceding
  close under the one-row execution lag;
- `pre_gap_weights_differ` is true when at least one absolute difference from `0.60/0.40` exceeds
  `ExperimentSpec.numeric_tolerance`;
- `r_stale` is `BacktestResult.gross_portfolio_returns.loc[d]`;
- `r_target = 0.60 * asset_returns.loc[d, "SPY"] + 0.40 * asset_returns.loc[d, "TLT"]`; and
- the 25-bps condition is `r_target - r_stale >= 0.0025`.

These quantities are deterministic engine values. An attacker may not calculate or assert them.
Gate 9A documents the observable but adds no counterfactual field or production calculation.

The examples use predetermined rebalance date `2024-02-01` and offset `-1`, deterministically
resolved to observed date `2024-01-31` before JSON construction.

#### Valid JSON

```json
{
  "schema_version": "1.0",
  "scenario_id": "h2-rebalance-valid",
  "evaluation_start": "2024-01-02",
  "evaluation_end": "2024-04-23",
  "components": [
    {
      "schema_version": "1.0",
      "family": "one_day_gap",
      "date": "2024-01-31",
      "shocks": {"SPY": -0.10, "TLT": -0.04}
    }
  ],
  "hypothesis": "A pre-rebalance joint gap tests loss amplification from drifted holdings.",
  "headline": "Joint loss arrives one observed row before the February rebalance"
}
```

This is valid even if the engine later shows less than 25 bps of stale-weight amplification. That
outcome is applicable but unsuccessful, not invalid.

#### Invalid JSON

```json
{
  "schema_version": "1.0",
  "scenario_id": "h2-rebalance-invalid-gap",
  "evaluation_start": "2024-01-02",
  "evaluation_end": "2024-04-23",
  "components": [
    {
      "schema_version": "1.0",
      "family": "one_day_gap",
      "date": "2024-01-31",
      "shocks": {"SPY": -0.16, "TLT": -0.04}
    }
  ],
  "hypothesis": "A pre-rebalance joint gap tests loss amplification from drifted holdings.",
  "headline": "Equity gap exceeds the rebalance-timing policy"
}
```

The Pydantic component accepts `-0.16` because it is strictly greater than `-1`. `AttackPolicy`
rejects it for the intended reason: `at least one shock is outside the policy range`
`[-0.15, -0.02]`. The hypothesis-specific SPY range is narrower still at `[-0.15, -0.03]`.

### Volatility regime jump

This row is `not_applicable` to fixed monthly 60/40 and therefore has no valid or invalid scenario
JSON example. Creating a `volatility_multiplier` component would stress asset returns, but it would
not test the stated mechanism because the strategy has no volatility-sizing decision. Labelling
such a result as a volatility-sizing failure would be an unsupported causal claim.

For a later strategy that explicitly declares volatility sizing, `multiplier` would map directly
to `volatility_multiplier`, and stress duration would resolve to inclusive observed `start_date`
and `end_date` values containing 5-20 rows. Lookback would resolve from the sizing decision date to
the preceding 20-60 observed rows in that strategy's declared sizing rule; it is not a new
`StressComponent` field. No such strategy contract or sizing rule is approved in Gate 9A, so these
resolutions are not used here.

### Trading-friction break

The attacker may change only
`StressComponent(family="transaction_cost_multiplier").transaction_cost_multiplier`. The base
rate comes from `ExperimentSpec.transaction_cost_bps`; it is not duplicated in the component.
Gate 5 records the base and resulting rate in
`ComponentTransformSummary.transaction_cost_bps_before` and `transaction_cost_bps_after`:

`transaction_cost_bps_after = transaction_cost_bps_before * transaction_cost_multiplier`.

The multiplier is inclusive `[2.00, 5.00]`, while the resulting rate is finite in Gate 5's
half-open interval `[0, 10000)` bps. A zero baseline or no positive
`BacktestResult.turnover` inside the inclusive evaluation window is hypothesis-invalid even though
the family-level Pydantic model cannot inspect experiment or replay context.

For the same inclusive evaluation rows, the deterministic engine defines:

- `incremental_cost_contribution = sum(transaction_costs_after - transaction_costs_before)`;
- `scenario_return = product(1 + stressed_portfolio_return) - 1`;
- `absolute_scenario_loss = abs(scenario_return)` when `scenario_return < 0`; and
- `cost_share = incremental_cost_contribution / absolute_scenario_loss`.

“Material transaction cost” means both
`incremental_cost_contribution >= 0.005` (at least 50 bps) and `cost_share >= 0.10` (at least 10%
of absolute scenario loss). Both boundaries are inclusive. A non-negative scenario return or a
zero loss makes the expected observable unsuccessful, not invalid. The deterministic engine alone
calculates the two input series and the compounded scenario return.

The valid example assumes a separately validated `ExperimentSpec.transaction_cost_bps` of `25.0`
bps and at least one positive-turnover row. The component raises the rate to `100.0` bps, which is
inside `[0, 10000)`. Passing validation does not assert that the two materiality thresholds occur.

#### Valid JSON

```json
{
  "schema_version": "1.0",
  "scenario_id": "h4-friction-valid",
  "evaluation_start": "2024-01-02",
  "evaluation_end": "2024-04-23",
  "components": [
    {
      "schema_version": "1.0",
      "family": "transaction_cost_multiplier",
      "transaction_cost_multiplier": 4.0
    }
  ],
  "hypothesis": "A fourfold execution-cost rate tests whether monthly turnover becomes loss-significant.",
  "headline": "Stressed liquidity raises execution costs from 25 to 100 basis points"
}
```

#### Invalid JSON

```json
{
  "schema_version": "1.0",
  "scenario_id": "h4-friction-invalid-multiplier",
  "evaluation_start": "2024-01-02",
  "evaluation_end": "2024-04-23",
  "components": [
    {
      "schema_version": "1.0",
      "family": "transaction_cost_multiplier",
      "transaction_cost_multiplier": 5.01
    }
  ],
  "hypothesis": "Execution costs test whether monthly turnover becomes loss-significant.",
  "headline": "Execution-cost multiplier exceeds the bounded policy"
}
```

The Pydantic component accepts `5.01` because it is finite and positive. `AttackPolicy` rejects it
for the intended reason: `transaction_cost_multiplier is outside the policy range` `[2.00, 5.00]`.

## Machine-policy envelope

`AttackPolicy` has one range per transform family, not per hypothesis or symbol. The checked-in
policy therefore stores the tight envelope needed by the table:

| Existing `AttackNumericRanges` field | Inclusive machine range | Table refinement |
| --- | ---: | --- |
| `one_day_gap_shock` | `[-0.15, -0.02]` | SPY maximum is `-0.03`; TLT minimum is `-0.12`. |
| `sustained_cumulative_shock` | `[-0.25, -0.04]` | SPY maximum is `-0.05`; TLT minimum is `-0.20`. |
| `sustained_duration_rows` | `[5, 20]` | Exact table range after applying the tighter Gate 6 maximum. |
| `volatility_multiplier` | `[1.25, 3.00]` | Exact applicable inflation-hypothesis range. |
| `target_correlation` | `[0.25, 0.90]` | Exact applicable inflation-hypothesis range. |
| `transaction_cost_multiplier` | `[2.00, 5.00]` | Exact table range after applying the tighter Gate 6 maximum. |

The hypothesis-specific table must also pass after the machine envelope. For example, a TLT
sustained shock of `-0.25` fits the shared machine envelope but violates TLT's table range and is
invalid; no value is clamped. Gate 9A does not expand `AttackPolicy` to encode per-symbol ranges,
offsets, strategy applicability, or expected observables.

## Timing, failures, and bounded search

Component dates and evaluation-window endpoints are inclusive observed dates. A sustained
`duration_rows = n` component affects exactly `n` consecutive observed rows starting at
`start_date`. Volatility and correlation windows include both `start_date` and `end_date`.

The strategy's target chosen at close `t` first becomes effective for the observed return at
`t+1`. The initial dataset row earns no return. Failure rules use only contemporaneous or trailing
data: maximum drawdown breaches strictly above its threshold, rolling loss uses exactly 20 earned
rows and breaches strictly below the negative threshold, and realized-volatility multiple uses
exactly 20 earned rows and breaches strictly above its threshold. An unavailable or zero baseline
volatility denominator is non-evaluable, not replaced.

The existing hard limits remain `MAX_ROUNDS=3`, `MAX_CANDIDATES_PER_ROUND=8`,
`MAX_TOTAL_SCENARIOS=24`, `TOP_K=3`, and at most eight components per scenario, plus the configured
positive wall-clock timeout. The attacker samples or selects bounded candidates; it does not run an
exhaustive Cartesian-product search, an unbounded loop, recursion, or an automatic keep-improving
cycle.
