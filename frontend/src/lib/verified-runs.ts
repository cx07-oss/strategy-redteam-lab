import run024Fixture from "../fixtures/demo-telemetry.json" with { type: "json" };
import run025Fixture from "../fixtures/demo-telemetry-run-025.json" with { type: "json" };
import sustainedFixture from "../fixtures/demo-telemetry-stress-library-sustained.json" with { type: "json" };
import volatilityFixture from "../fixtures/demo-telemetry-stress-library-volatility.json" with { type: "json" };
import correlationFixture from "../fixtures/demo-telemetry-stress-library-correlation.json" with { type: "json" };
import { parseRunTelemetry, selectVerifiedEvaluation, type Evaluation, type RunTelemetry } from "./telemetry.ts";

export type VerifiedRunId = "run-024" | "run-025-moderate" | "library-sustained" | "library-volatility" | "library-correlation";
export type ScenarioOrigin = "AI-selected" | "Deterministic stress library";
export type VerifiedRun = Readonly<{
  id: VerifiedRunId;
  displayName: string;
  shortDescription: string;
  family: string;
  origin: ScenarioOrigin;
  sourceArtifact: string;
  isDefault: boolean;
  run: RunTelemetry;
  evaluation: Evaluation;
}>;

const run024 = parseRunTelemetry(run024Fixture);
const run025 = parseRunTelemetry(run025Fixture);
const sustained = parseRunTelemetry(sustainedFixture);
const volatility = parseRunTelemetry(volatilityFixture);
const correlation = parseRunTelemetry(correlationFixture);

export const verifiedRuns: readonly VerifiedRun[] = [
  { id: "run-024", displayName: "One-Day Gap", shortDescription: "Abrupt pre-rebalance equity and bond gap.", family: "one_day_gap", origin: "AI-selected", sourceArtifact: "ollama-run-024/demo-telemetry.json", isDefault: true, run: run024, evaluation: selectVerifiedEvaluation(run024, "ollama-r01-c01") },
  { id: "run-025-moderate", displayName: "Moderate One-Day Gap", shortDescription: "A smaller independently reproduced gap control.", family: "one_day_gap", origin: "AI-selected", sourceArtifact: "ollama-run-025/demo-telemetry.json", isDefault: false, run: run025, evaluation: selectVerifiedEvaluation(run025, "ollama-r01-c02") },
  { id: "library-sustained", displayName: "Sustained Cumulative Shock", shortDescription: "Twenty trading days of cumulative sleeve stress.", family: "sustained_cumulative_shock", origin: "Deterministic stress library", sourceArtifact: "stress-library-sustained/demo-telemetry.json", isDefault: false, run: sustained, evaluation: selectVerifiedEvaluation(sustained, "offline-r01-c01") },
  { id: "library-volatility", displayName: "Volatility Spike", shortDescription: "A full-window increase in SPY/TLT log-return volatility.", family: "volatility_multiplier", origin: "Deterministic stress library", sourceArtifact: "stress-library-volatility/demo-telemetry.json", isDefault: false, run: volatility, evaluation: selectVerifiedEvaluation(volatility, "offline-r01-c01") },
  { id: "library-correlation", displayName: "Correlation Breakdown", shortDescription: "A full-window SPY/TLT correlation target transformation.", family: "correlation_target", origin: "Deterministic stress library", sourceArtifact: "stress-library-correlation/demo-telemetry.json", isDefault: false, run: correlation, evaluation: selectVerifiedEvaluation(correlation, "offline-r01-c01") },
];

export const defaultVerifiedRun = verifiedRuns[0]!;
