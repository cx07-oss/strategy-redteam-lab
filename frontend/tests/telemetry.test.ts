import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import fixture from "../src/fixtures/demo-telemetry.json" with { type: "json" };
import run025Fixture from "../src/fixtures/demo-telemetry-run-025.json" with { type: "json" };
import { defaultVerifiedRun, verifiedRuns } from "../src/lib/verified-runs.ts";
import { finalPortfolioImpact, maximumStressedDrawdown, stressImpact } from "../src/lib/presentation-metrics.ts";
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

test("verified scenario library exposes only accepted entries across multiple implemented families", () => {
  assert.equal(defaultVerifiedRun.id, "run-024");
  assert.deepEqual(verifiedRuns.map((entry) => entry.evaluation.result.scenarioId), ["ollama-r01-c01", "ollama-r01-c02", "offline-r01-c01", "offline-r01-c01", "offline-r01-c01"]);
  assert.equal(new Set(verifiedRuns.map((entry) => entry.id)).size, verifiedRuns.length);
  assert.deepEqual(new Set(verifiedRuns.map((entry) => entry.family)), new Set(["one_day_gap", "sustained_cumulative_shock", "volatility_multiplier", "correlation_target"]));
  assert.equal(verifiedRuns.some((entry) => entry.evaluation.result.status === "rejected"), false);
  const moderate = verifiedRuns[1]!;
  assert.equal(moderate.displayName, "Moderate One-Day Gap");
  assert.equal(moderate.evaluation.result.breachCount, 3);
  assert.equal(moderate.evaluation.scenario.components[0]?.shocks?.SPY, -0.1212);
  assert.equal(moderate.evaluation.scenario.components[0]?.shocks?.TLT, -0.096);
  assert.equal(moderate.run.defenderVerdicts.find((verdict) => verdict.scenarioId === "ollama-r01-c02")?.verdict, "reproduced");
  assert.equal(moderate.run.evaluations.filter((item) => item.result.status === "rejected").length, 4);
  assert.equal(verifiedRuns.find((entry) => entry.id === "library-sustained")?.origin, "Deterministic stress library");
});

test("presentation derivations use validated run-024 chart evidence", () => {
  const run = parseRunTelemetry(fixture);
  const evaluation = selectValidEvaluation(run);
  assert.ok(Math.abs(stressImpact(evaluation) - -0.1245) < 0.0002);
  assert.ok(Math.abs(maximumStressedDrawdown(evaluation) - 0.2326) < 0.0002);
  assert.ok(finalPortfolioImpact(evaluation) < 0);
  for (const entry of verifiedRuns) {
    assert.ok(Number.isFinite(stressImpact(entry.evaluation)));
    assert.ok(Number.isFinite(maximumStressedDrawdown(entry.evaluation)));
    assert.ok(Number.isFinite(finalPortfolioImpact(entry.evaluation)));
  }
  assert.throws(() => selectValidEvaluation(parseRunTelemetry({ ...run025Fixture, evaluations: [] })), /no valid evaluation/);
});

test("every release telemetry fixture is byte-identical to its tracked authoritative source", () => {
  const pairs = [
    ["../../artifacts/demo/ollama-run-024/demo-telemetry.json", "../src/fixtures/demo-telemetry.json"],
    ["../../artifacts/demo/ollama-run-025/demo-telemetry.json", "../src/fixtures/demo-telemetry-run-025.json"],
    ["../../artifacts/demo/stress-library-sustained/demo-telemetry.json", "../src/fixtures/demo-telemetry-stress-library-sustained.json"],
    ["../../artifacts/demo/stress-library-volatility/demo-telemetry.json", "../src/fixtures/demo-telemetry-stress-library-volatility.json"],
    ["../../artifacts/demo/stress-library-correlation/demo-telemetry.json", "../src/fixtures/demo-telemetry-stress-library-correlation.json"],
  ] as const;
  for (const [source, fixturePath] of pairs) {
    const sourceBytes = readFileSync(new URL(source, import.meta.url));
    const fixtureBytes = readFileSync(new URL(fixturePath, import.meta.url));
    assert.deepEqual(fixtureBytes, sourceBytes);
    assert.equal(createHash("sha256").update(fixtureBytes).digest("hex"), createHash("sha256").update(sourceBytes).digest("hex"));
  }
});

test("each verified run has provenance-bound context and the product navigation defaults to overview", () => {
  for (const entry of verifiedRuns) assert.equal(runContext(entry.id, entry.run.configSha256).strategy, "60% SPY / 40% TLT");
  assert.throws(() => runContext("run-024", "not-a-hash"), /no versioned frontend run context/i);
  assert.deepEqual(replayViews.map((view) => view.id), ["overview", "method", "evidence"]);
});
