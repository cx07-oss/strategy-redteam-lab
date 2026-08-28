import assert from "node:assert/strict";
import test from "node:test";
import fixture from "../src/fixtures/demo-telemetry.json" with { type: "json" };
import run025Fixture from "../src/fixtures/demo-telemetry-run-025.json" with { type: "json" };
import { defaultVerifiedRun, verifiedRuns } from "../src/lib/verified-runs.ts";
import { maximumStressedDrawdown, stressImpact } from "../src/lib/presentation-metrics.ts";
import { replayViews } from "../src/lib/replay-navigation.ts";
import { runContext } from "../src/lib/run-context.ts";
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

test("verified scenario bank exposes only the default and accepted moderate replay", () => {
  assert.equal(defaultVerifiedRun.id, "run-024");
  assert.deepEqual(verifiedRuns.map((entry) => entry.evaluation.result.scenarioId), ["ollama-r01-c01", "ollama-r01-c02"]);
  assert.equal(verifiedRuns.some((entry) => entry.evaluation.result.status === "rejected"), false);
  const moderate = verifiedRuns[1]!;
  assert.equal(moderate.label, "Moderate one-day gap");
  assert.equal(moderate.evaluation.result.breachCount, 3);
  assert.equal(moderate.evaluation.scenario.components[0]?.shocks?.SPY, -0.1212);
  assert.equal(moderate.evaluation.scenario.components[0]?.shocks?.TLT, -0.096);
  assert.equal(moderate.run.defenderVerdicts.find((verdict) => verdict.scenarioId === "ollama-r01-c02")?.verdict, "reproduced");
  assert.equal(moderate.run.evaluations.filter((item) => item.result.status === "rejected").length, 4);
});

test("presentation derivations use validated run-024 chart evidence", () => {
  const run = parseRunTelemetry(fixture);
  const evaluation = selectValidEvaluation(run);
  assert.ok(Math.abs(stressImpact(evaluation) - -0.1245) < 0.0002);
  assert.ok(Math.abs(maximumStressedDrawdown(evaluation) - 0.2326) < 0.0002);
  assert.throws(() => selectValidEvaluation(parseRunTelemetry({ ...run025Fixture, evaluations: [] })), /no valid evaluation/);
});

test("each verified run has provenance-bound context and the product navigation defaults to overview", () => {
  for (const entry of verifiedRuns) assert.equal(runContext(entry.id, entry.run.configSha256).strategy, "60% SPY / 40% TLT");
  assert.throws(() => runContext("run-024", "not-a-hash"), /no versioned frontend run context/i);
  assert.deepEqual(replayViews.map((view) => view.id), ["overview", "method", "evidence"]);
});
