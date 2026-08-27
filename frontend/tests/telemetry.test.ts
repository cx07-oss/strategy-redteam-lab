import assert from "node:assert/strict";
import test from "node:test";
import fixture from "../src/fixtures/demo-telemetry.json" with { type: "json" };
import { parseRunTelemetry, selectValidEvaluation } from "../src/lib/telemetry.ts";

test("run-024 fixture parses and exposes verified deterministic evidence", () => {
  const run = parseRunTelemetry(fixture);
  const evaluation = selectValidEvaluation(run);
  assert.equal(evaluation.result.scenarioId, "ollama-r01-c01");
  assert.equal(evaluation.result.breachCount, 3);
  assert.equal(evaluation.result.maximumNormalizedExcess, 1.198637511934236);
  assert.equal(evaluation.chartPoints.length, 81);
  assert.equal(run.verificationVerdict, "reproduced");
  assert.equal(run.defenderVerdicts[0]?.maxMetricDelta, 0);
  assert.equal(evaluation.scenario.components[0]?.shocks?.SPY, -0.1356);
  assert.deepEqual(run.events.map((event) => event.sequence), [...Array(11)].map((_, index) => index + 1));
  assert.equal(run.events.filter((event) => event.eventType === "risk_limit_breached").length, 3);
});

test("malformed telemetry fails instead of inventing values", () => {
  assert.throws(() => parseRunTelemetry({ provider: "ollama" }), /Malformed telemetry/);
});

test("rejected scenarios never become the selected valid evaluation", () => {
  const run = parseRunTelemetry(fixture);
  const rejectedOnly = { ...run, evaluations: [{ ...run.evaluations[0], result: { ...run.evaluations[0].result, status: "rejected" as const } }] };
  assert.throws(() => selectValidEvaluation(rejectedOnly), /no valid evaluation/);
});
