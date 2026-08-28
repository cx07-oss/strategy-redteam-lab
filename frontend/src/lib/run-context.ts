import type { VerifiedRunId } from "./verified-runs.ts";

export type RunContext = Readonly<{
  schemaVersion: "1.0";
  verifiedRunId: VerifiedRunId;
  configSha256: string;
  strategy: "60% SPY / 40% TLT";
  rebalancing: "Monthly";
  period: "02 Jan – 23 Apr 2024";
  startingValue: "1.00 normalized";
  modelRole: "Attack selection only" | "No model call";
}>;

const contexts: readonly RunContext[] = [
  { schemaVersion: "1.0", verifiedRunId: "run-024", configSha256: "5729ebd028baeccc0e50daea8c66b1da84116171319ff8aafbe322000132030e", strategy: "60% SPY / 40% TLT", rebalancing: "Monthly", period: "02 Jan – 23 Apr 2024", startingValue: "1.00 normalized", modelRole: "Attack selection only" },
  { schemaVersion: "1.0", verifiedRunId: "run-025-moderate", configSha256: "79bf4a039ceb5cb93211833f95975cf35c523d79b6d8dba4bb17b2aabc7940a6", strategy: "60% SPY / 40% TLT", rebalancing: "Monthly", period: "02 Jan – 23 Apr 2024", startingValue: "1.00 normalized", modelRole: "Attack selection only" },
  { schemaVersion: "1.0", verifiedRunId: "library-sustained", configSha256: "39d4e23d46c4b847ba528ff6af6d54a87ebf070e1b480928d3dc2b93662c25ec", strategy: "60% SPY / 40% TLT", rebalancing: "Monthly", period: "02 Jan – 23 Apr 2024", startingValue: "1.00 normalized", modelRole: "No model call" },
  { schemaVersion: "1.0", verifiedRunId: "library-volatility", configSha256: "20f8f247c852a4627e3a9940013d01f84bcfc630217e822369844d5c8f9c559f", strategy: "60% SPY / 40% TLT", rebalancing: "Monthly", period: "02 Jan – 23 Apr 2024", startingValue: "1.00 normalized", modelRole: "No model call" },
  { schemaVersion: "1.0", verifiedRunId: "library-correlation", configSha256: "9683575cf81c69889ee622c6fff7c24746336075a3c5216c24c0edfb12eef31d", strategy: "60% SPY / 40% TLT", rebalancing: "Monthly", period: "02 Jan – 23 Apr 2024", startingValue: "1.00 normalized", modelRole: "No model call" },
];

export function runContext(verifiedRunId: VerifiedRunId, configSha256: string): RunContext {
  const context = contexts.find((item) => item.verifiedRunId === verifiedRunId && item.configSha256 === configSha256);
  if (!context) throw new Error("No versioned frontend run context matches verified telemetry provenance.");
  return context;
}
