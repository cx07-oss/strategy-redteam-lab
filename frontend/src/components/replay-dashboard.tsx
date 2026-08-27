"use client";

import { useState } from "react";
import type { ChartPoint, Evaluation, RunTelemetry } from "@/lib/telemetry";

type View = "equity" | "drawdown";

const eventPhase = (eventType: string) => {
  if (eventType.startsWith("run_")) return "Run";
  if (eventType.startsWith("scenario_")) return "Attack";
  if (eventType.startsWith("engine_")) return "Evaluation";
  if (eventType === "risk_limit_breached") return "Breach";
  if (eventType.startsWith("defender_")) return "Defender";
  return "Verification";
};

const label = (eventType: string) => eventType.replaceAll("_", " ");
const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-6)}`;
const emphasis = new Set(["risk_limit_breached", "defender_replay_completed", "verification_completed", "run_completed"]);

function linePath(points: readonly ChartPoint[], values: readonly number[], min: number, max: number) {
  return points.map((_, index) => {
    const x = (index / (points.length - 1)) * 100;
    const y = 100 - ((values[index] - min) / (max - min || 1)) * 100;
    return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

function ReplayChart({ points }: { points: readonly ChartPoint[] }) {
  const [view, setView] = useState<View>("equity");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const baseline = points.map((point) => point.baselineEquity);
  const stressed = points.map((point) => point.stressedEquity);
  const drawdowns = points.map((point) => point.stressedDrawdown);
  const values = view === "equity" ? [...baseline, ...stressed] : drawdowns;
  const active = activeIndex === null ? null : points[activeIndex];
  const chartLabel = view === "equity" ? "Baseline and stressed equity" : "Stressed drawdown";
  return <section className="chart-panel" aria-labelledby="performance-title">
    <div className="section-heading"><div><p className="eyebrow">Deterministic evidence</p><h2 id="performance-title">Strategy performance</h2></div><div className="segment" role="group" aria-label="Chart measure">
      <button className={view === "equity" ? "active" : ""} onClick={() => setView("equity")} type="button">Equity</button>
      <button className={view === "drawdown" ? "active" : ""} onClick={() => setView("drawdown")} type="button">Drawdown</button>
    </div></div>
    <div className="legend">{view === "equity" ? <><span className="baseline" />Baseline equity <span className="stressed" />Stressed equity</> : <><span className="drawdown-key" />Stressed drawdown</>}</div>
    <svg viewBox="0 0 100 100" role="img" aria-label={`${chartLabel} across ${points.length} telemetry points`} preserveAspectRatio="none" onMouseLeave={() => setActiveIndex(null)}>
      <path className="grid" d="M0 25H100M0 50H100M0 75H100" />
      {view === "equity" ? <><path className="line baseline-line" d={linePath(points, baseline, Math.min(...values), Math.max(...values))} /><path className="line stressed-line" d={linePath(points, stressed, Math.min(...values), Math.max(...values))} /></> : <path className="line drawdown-line" d={linePath(points, drawdowns, Math.min(...values), Math.max(...values))} />}
      {active && <circle className="chart-marker" cx={(activeIndex! / (points.length - 1)) * 100} cy={100 - (((view === "equity" ? active.stressedEquity : active.stressedDrawdown) - Math.min(...values)) / (Math.max(...values) - Math.min(...values) || 1)) * 100} r="1.4" />}
      {points.map((point, index) => <circle className="hit-area" key={point.date} cx={(index / (points.length - 1)) * 100} cy="50" r="3" onFocus={() => setActiveIndex(index)} onMouseEnter={() => setActiveIndex(index)} tabIndex={0}><title>{point.date}</title></circle>)}
    </svg>
    <div className="axis"><span>{points[0]?.date}</span><span>{points.at(-1)?.date}</span></div>
    <p className="chart-summary">{active ? `${active.date}: baseline ${active.baselineEquity.toFixed(4)}, stressed ${active.stressedEquity.toFixed(4)}, drawdown ${(active.stressedDrawdown * 100).toFixed(2)}%.` : "Hover or tab through chart points for exact telemetry values."}</p>
  </section>;
}

function CopyHash({ label: hashLabel, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { await navigator.clipboard?.writeText(value); setCopied(true); };
  return <div title={value}><span>{hashLabel}</span><code>{shortHash(value)}</code><button type="button" onClick={copy}>{copied ? "Copied" : "Copy"}</button></div>;
}

export function ReplayDashboard({ run, evaluation }: { run: RunTelemetry; evaluation: Evaluation }) {
  const breachEvents = run.events.filter((event) => event.eventType === "risk_limit_breached");
  const replay = run.defenderVerdicts.find((verdict) => verdict.scenarioId === evaluation.result.scenarioId);
  return <main><header><div><p className="eyebrow">Research-only evaluation interface</p><h1>Trading Strategy Red-Team Lab</h1><p className="subtitle">Verified Run Replay · {evaluation.result.scenarioId} · round {evaluation.roundNumber}</p></div><div className="status"><span className="status-dot" />Verified: defender replay {run.verificationVerdict}</div></header>
    <section className="metadata" aria-label="Run overview"><div><span>Provider / model</span><strong>{run.provider} · {run.modelIdentifier}</strong></div><div><span>Verification verdict</span><strong>{run.verificationVerdict}</strong></div><div><span>Scenario / status</span><strong>{evaluation.result.scenarioId} · {evaluation.result.status}</strong></div><div><span>Run framing</span><strong>Read-only research replay</strong></div></section>
    <section className="summary"><div><p className="eyebrow">Key results</p><h2>Selected deterministic evaluation</h2><p>Model selection is separate from numerical evaluation.</p></div><dl><div><dt>Risk-limit breaches</dt><dd>{evaluation.result.breachCount}</dd></div><div><dt>Max normalized excess</dt><dd>{evaluation.result.maximumNormalizedExcess?.toFixed(4)}</dd></div><div><dt>Chart points</dt><dd>{evaluation.chartPoints.length}</dd></div><div><dt>Replay delta</dt><dd>{replay?.maxMetricDelta.toFixed(1) ?? "—"}</dd></div></dl></section>
    <ReplayChart points={evaluation.chartPoints} />
    <section className="analysis-grid"><section className="attack" aria-labelledby="attack-title"><p className="eyebrow">Selected attack</p><h2 id="attack-title">Canonical scenario</h2><p>Ollama selected a prevalidated attack. Python owns the numerical scenario and deterministic evaluation.</p><ul>{evaluation.scenario.components.map((component, index) => <li key={`${component.family}-${index}`}><strong>{component.family.replaceAll("_", " ")}</strong>{component.date && <span>Date: {component.date}</span>}{component.shocks && <span>Shocks: {Object.entries(component.shocks).map(([symbol, shock]) => `${symbol} ${(shock * 100).toFixed(2)}%`).join(" · ")}</span>}</li>)}</ul></section>
      <section className="breaches" aria-labelledby="breach-title"><p className="eyebrow">Breach analysis</p><h2 id="breach-title">{breachEvents.length} verified risk-limit breach events</h2><p>Only canonical telemetry events are shown; no breach magnitude or date is inferred here.</p><ol>{breachEvents.map((event) => <li key={event.sequence}><span>{String(event.sequence).padStart(2, "0")}</span><strong>{event.eventType}</strong><small>{event.scenarioId}</small></li>)}</ol></section></section>
    <section className="verification"><div><p className="eyebrow">Defender verification</p><h2>Verification chain</h2><div className="verification-flow"><span>Attack selected</span><b>→</b><span>Deterministic evaluation</span><b>→</b><span>Breach detected</span><b>→</b><span>Defender replay</span><b>→</b><strong>{run.verificationVerdict}</strong></div><p>The model selected from prevalidated candidates. Deterministic Python generated market metrics; the defender replay checked the result independently.</p></div><div className="provenance"><p>Provenance</p><CopyHash label="Config SHA256" value={run.configSha256} /><CopyHash label="Data SHA256" value={run.dataSha256} /><p>Fixture: <code>artifacts/demo/ollama-run-024/demo-telemetry.json</code></p></div></section>
    <section className="timeline"><p className="eyebrow">Ordered evidence</p><h2>Event timeline</h2><ol>{run.events.map((event) => <li className={emphasis.has(event.eventType) ? "highlight" : ""} key={event.sequence}><span>{String(event.sequence).padStart(2, "0")}</span><em>{eventPhase(event.eventType)}</em><strong>{label(event.eventType)}</strong><small>{event.scenarioId ?? "run"}{event.roundNumber ? ` · round ${event.roundNumber}` : ""}</small></li>)}</ol></section>
  </main>;
}
