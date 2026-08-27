import fixture from "@/fixtures/demo-telemetry.json";
import { DrawdownChart } from "@/components/drawdown-chart";
import { PerformanceChart } from "@/components/performance-chart";
import { parseRunTelemetry, selectValidEvaluation } from "@/lib/telemetry";

const run = parseRunTelemetry(fixture);
const evaluation = selectValidEvaluation(run);
const label = (eventType: string) => eventType.replaceAll("_", " ");
const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-6)}`;

export default function ReplayPage() {
  return <main><header><div><p className="eyebrow">Research-only evaluation interface</p><h1>Trading Strategy Red-Team Lab</h1><p className="subtitle">Verified Run Replay</p></div><div className="status"><span className="status-dot" />Verified: defender replay reproduced</div></header>
    <section className="metadata" aria-label="Run status"><div><span>Provider / model</span><strong>{run.provider} · {run.modelIdentifier}</strong></div><div><span>Verification verdict</span><strong>{run.verificationVerdict}</strong></div><div title={run.configSha256}><span>Config SHA256</span><strong>{shortHash(run.configSha256)}</strong></div><div title={run.dataSha256}><span>Data SHA256</span><strong>{shortHash(run.dataSha256)}</strong></div></section>
    <section className="summary"><div><p className="eyebrow">Selected valid evaluation</p><h2>{evaluation.result.scenarioId}</h2><p>Round {evaluation.roundNumber} · {evaluation.result.status}</p></div><dl><div><dt>Risk-limit breaches</dt><dd>{evaluation.result.breachCount}</dd></div><div><dt>Max normalized excess</dt><dd>{evaluation.result.maximumNormalizedExcess?.toFixed(4)}</dd></div><div><dt>Chart points</dt><dd>{evaluation.chartPoints.length}</dd></div></dl></section>
    <div className="charts"><PerformanceChart points={evaluation.chartPoints} /><DrawdownChart points={evaluation.chartPoints} /></div>
    <section className="verification"><div><p className="eyebrow">Verification boundary</p><h2>What this replay establishes</h2><p>Deterministic Python produced the evaluation and risk-limit breaches. The defender independently replayed that evidence; the model did not calculate or verify market metrics.</p></div><div className="provenance"><p>Fixture provenance</p><code>artifacts/demo/ollama-run-024/demo-telemetry.json</code><p>Scenario: <strong>{evaluation.scenario}</strong> · {evaluation.chartPoints.length} chart points</p></div></section>
    <section className="timeline"><p className="eyebrow">Ordered evidence</p><h2>Event timeline</h2><ol>{run.events.map((event) => <li key={event.sequence}><span>{String(event.sequence).padStart(2, "0")}</span><strong>{label(event.eventType)}</strong><small>{event.scenarioId ?? "run"}{event.roundNumber ? ` · round ${event.roundNumber}` : ""}</small></li>)}</ol></section>
  </main>;
}
