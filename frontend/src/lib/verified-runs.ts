import run024Fixture from "../fixtures/demo-telemetry.json" with { type: "json" };
import run025Fixture from "../fixtures/demo-telemetry-run-025.json" with { type: "json" };
import { parseRunTelemetry, selectVerifiedEvaluation, type Evaluation, type RunTelemetry } from "./telemetry.ts";

export type VerifiedRunId = "run-024" | "run-025-moderate";
export type VerifiedRun = Readonly<{ id: VerifiedRunId; label: string; isDefault: boolean; run: RunTelemetry; evaluation: Evaluation }>;

const run024 = parseRunTelemetry(run024Fixture);
const run025 = parseRunTelemetry(run025Fixture);

export const verifiedRuns: readonly VerifiedRun[] = [
  { id: "run-024", label: "Severe one-day gap", isDefault: true, run: run024, evaluation: selectVerifiedEvaluation(run024, "ollama-r01-c01") },
  { id: "run-025-moderate", label: "Moderate one-day gap", isDefault: false, run: run025, evaluation: selectVerifiedEvaluation(run025, "ollama-r01-c02") },
];

export const defaultVerifiedRun = verifiedRuns[0]!;
