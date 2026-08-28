import type { VerifiedRunId } from "./verified-runs.ts";

export type RunContext = Readonly<{
  schemaVersion: "1.0";
  verifiedRunId: VerifiedRunId;
  configSha256: string;
  strategy: "60% SPY / 40% TLT";
  rebalancing: "Monthly";
  period: "02 Jan – 23 Apr 2024";
  startingValue: "1.00 normalized";
  modelRole: "Attack selection only";
}>;

const contexts: readonly RunContext[] = [
  { schemaVersion: "1.0", verifiedRunId: "run-024", configSha256: "5729ebd028baeccc0e50daea8c66b1da84116171319ff8aafbe322000132030e", strategy: "60% SPY / 40% TLT", rebalancing: "Monthly", period: "02 Jan – 23 Apr 2024", startingValue: "1.00 normalized", modelRole: "Attack selection only" },
  { schemaVersion: "1.0", verifiedRunId: "run-025-moderate", configSha256: "79bf4a039ceb5cb93211833f95975cf35c523d79b6d8dba4bb17b2aabc7940a6", strategy: "60% SPY / 40% TLT", rebalancing: "Monthly", period: "02 Jan – 23 Apr 2024", startingValue: "1.00 normalized", modelRole: "Attack selection only" },
];

export function runContext(verifiedRunId: VerifiedRunId, configSha256: string): RunContext {
  const context = contexts.find((item) => item.verifiedRunId === verifiedRunId && item.configSha256 === configSha256);
  if (!context) throw new Error("No versioned frontend run context matches verified telemetry provenance.");
  return context;
}
