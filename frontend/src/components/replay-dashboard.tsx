"use client";

import { useState } from "react";
import type { ChartPoint, Evaluation, RunTelemetry } from "@/lib/telemetry";

type View = "equity" | "drawdown";
const chart = { width: 760, height: 330, left: 58, right: 24, top: 24, bottom: 46 };
const eventPhase = (type: string) => type.startsWith("run_") ? "Run" : type.startsWith("scenario_") ? "Attack" : type.startsWith("engine_") ? "Evaluation" : type === "risk_limit_breached" ? "Breach" : type.startsWith("defender_") ? "Defender" : "Verification";
const label = (type: string) => type.replaceAll("_", " ");
const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-6)}`;
const emphasis = new Set(["risk_limit_breached", "defender_replay_completed", "verification_completed", "run_completed"]);
const formatPercent = (value: number) => `${(value * 100).toFixed(2)}%`;
const x = (index: number, count: number) => chart.left + (index / (count - 1 || 1)) * (chart.width - chart.left - chart.right);
const y = (value: number, min: number, max: number) => chart.top + (1 - (value - min) / (max - min || 1)) * (chart.height - chart.top - chart.bottom);

function linePath(points: readonly ChartPoint[], values: readonly number[], min: number, max: number) {
  return points.map((_, index) => `${index ? "L" : "M"}${x(index, points.length).toFixed(2)} ${y(values[index]!, min, max).toFixed(2)}`).join(" ");
}

function ReplayChart({ points, attackDate }: { points: readonly ChartPoint[]; attackDate: string | null }) {
  const [view, setView] = useState<View>("equity");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const baseline = points.map((point) => point.baselineEquity);
  const stressed = points.map((point) => point.stressedEquity);
  const drawdowns = points.map((point) => point.stressedDrawdown);
  const values = view === "equity" ? [...baseline, ...stressed] : drawdowns;
  const min = Math.min(...values), max = Math.max(...values);
  const active = activeIndex === null ? null : points[activeIndex];
  const attackIndex = points.findIndex((point) => point.date === attackDate);
  const move = (offset: number) => setActiveIndex((current) => Math.max(0, Math.min(points.length - 1, (current ?? 0) + offset)));
  return <section className="chart-panel" aria-labelledby="performance-title">
    <div className="section-heading"><div><p className="eyebrow">Deterministic evidence</p><h2 id="performance-title">Performance replay</h2><p>Exact historical points from the verified telemetry artifact.</p></div><div className="segment" role="group" aria-label="Chart measure"><button aria-pressed={view === "equity"} className={view === "equity" ? "active" : ""} onClick={() => setView("equity")} type="button">Equity</button><button aria-pressed={view === "drawdown"} className={view === "drawdown" ? "active" : ""} onClick={() => setView("drawdown")} type="button">Drawdown</button></div></div>
    <div className="chart-legend">{view === "equity" ? <><span className="baseline-key" />Baseline <span className="stressed-key" />Stressed</> : <><span className="drawdown-key" />Stressed drawdown</>}{attackIndex >= 0 && <><span className="attack-key" />Selected stress event</>}</div>
    <div className="chart-wrap"><svg viewBox={`0 0 ${chart.width} ${chart.height}`} role="img" aria-label={`${view === "equity" ? "Baseline and stressed equity" : "Stressed drawdown"} across ${points.length} telemetry points. Use left and right arrow keys to inspect individual points.`} tabIndex={0} onBlur={() => setActiveIndex(null)} onKeyDown={(event) => { if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); } if (event.key === "ArrowRight") { event.preventDefault(); move(1); } if (event.key === "Home") { event.preventDefault(); setActiveIndex(0); } if (event.key === "End") { event.preventDefault(); setActiveIndex(points.length - 1); } }} onMouseLeave={() => setActiveIndex(null)}>
      {[0, .5, 1].map((ratio) => { const tick = max - (max - min) * ratio; return <g key={ratio}><line className="grid" x1={chart.left} x2={chart.width - chart.right} y1={y(tick, min, max)} y2={y(tick, min, max)} /><text className="axis-label" x={chart.left - 10} y={y(tick, min, max) + 4} textAnchor="end">{view === "equity" ? tick.toFixed(2) : formatPercent(tick)}</text></g>; })}
      {attackIndex >= 0 && <g className="attack-marker"><line x1={x(attackIndex, points.length)} x2={x(attackIndex, points.length)} y1={chart.top} y2={chart.height - chart.bottom} /><text x={x(attackIndex, points.length)} y={chart.top - 8} textAnchor="middle">Selected stress · {attackDate}</text></g>}
      {view === "equity" ? <><path className="line baseline-line" d={linePath(points, baseline, min, max)} /><path className="line stressed-line" d={linePath(points, stressed, min, max)} /></> : <path className="line drawdown-line" d={linePath(points, drawdowns, min, max)} />}
      {active && <g className="active-point"><line x1={x(activeIndex!, points.length)} x2={x(activeIndex!, points.length)} y1={chart.top} y2={chart.height - chart.bottom} /><circle cx={x(activeIndex!, points.length)} cy={y(view === "equity" ? active.stressedEquity : active.stressedDrawdown, min, max)} r="5" /></g>}
      {points.map((point, index) => <rect aria-hidden="true" className="hit-area" height={chart.height - chart.top - chart.bottom} key={point.date} onMouseEnter={() => setActiveIndex(index)} width={(chart.width - chart.left - chart.right) / points.length + 3} x={x(index, points.length) - 2} y={chart.top} />)}
      <text className="axis-label" x={chart.left} y={chart.height - 15}>{points[0]?.date}</text><text className="axis-label" x={chart.width - chart.right} y={chart.height - 15} textAnchor="end">{points.at(-1)?.date}</text></svg>{active && <div className="chart-tooltip" role="status"><strong>{active.date}</strong><span>Baseline {active.baselineEquity.toFixed(4)}</span><span>Stressed {active.stressedEquity.toFixed(4)}</span><span>Drawdown {formatPercent(active.stressedDrawdown)}</span></div>}</div>
    <p className="chart-summary">Hover the chart, or focus it and use arrow keys, to inspect exact telemetry values. The vertical marker is the canonical selected stress event—not a breach-date inference.</p>
  </section>;
}

function CopyHash({ label: hashLabel, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return <div className="hash" title={value}><span>{hashLabel}</span><code>{shortHash(value)}</code><button type="button" onClick={async () => { await navigator.clipboard?.writeText(value); setCopied(true); }}>{copied ? "Copied" : "Copy"}</button></div>;
}

export function ReplayDashboard({ run, evaluation }: { run: RunTelemetry; evaluation: Evaluation }) {
  const breaches = run.events.filter((event) => event.eventType === "risk_limit_breached");
  const replay = run.defenderVerdicts.find((verdict) => verdict.scenarioId === evaluation.result.scenarioId);
  const component = evaluation.scenario.components[0], attackDate = component?.date ?? null;
  return <main><header className="hero"><div><p className="eyebrow">Research-only quantitative evaluation</p><h1>Trading Strategy Red-Team Lab</h1><p className="hero-copy">Bounded LLM attack selection. Deterministic numerical evaluation. Independent defender replay.</p><div className="hero-tags"><span>Verified Run Replay</span><span>{run.modelIdentifier}</span><span>{evaluation.result.scenarioId}</span><span>{evaluation.result.status}</span><span>research-only</span></div></div><div className="hero-actions"><div className="status"><span className="status-dot" />Verified: {run.verificationVerdict}</div><a href="https://github.com/cx07-oss/strategy-redteam-lab" rel="noreferrer" target="_blank">View repository <span aria-hidden="true">↗</span></a></div></header>
    <section className="result-strip" aria-labelledby="results-title"><div><p className="eyebrow">Verified result</p><h2 id="results-title">Selected deterministic evaluation</h2><p>The model chose a bounded canonical attack; it did not calculate this result.</p></div><dl><div><dt>Risk-limit breaches</dt><dd>{evaluation.result.breachCount}</dd><small>verified events</small></div><div><dt>Max normalized excess</dt><dd>{evaluation.result.maximumNormalizedExcess?.toFixed(4)}</dd><small>highest recorded rule excess</small></div><div><dt>Chart points</dt><dd>{evaluation.chartPoints.length}</dd><small>immutable telemetry samples</small></div><div><dt>Replay delta</dt><dd>{replay?.maxMetricDelta.toFixed(1) ?? "—"}</dd><small>engine versus defender replay</small></div></dl></section>
    <section className="trust-strip" aria-label="Verification trust boundary"><div><strong>Ollama</strong><span>selects prevalidated attack</span></div><b>→</b><div><strong>Deterministic Python</strong><span>evaluates numerical scenario</span></div><b>→</b><div><strong>Risk limits</strong><span>detect failure</span></div><b>→</b><div><strong>Defender</strong><span>independently replays</span></div><b>→</b><div className="trust-result"><strong>Reproduced</strong><span>verified replay result</span></div></section>
    <ReplayChart attackDate={attackDate} points={evaluation.chartPoints} />
    <section className="analysis-grid"><section className="attack" aria-labelledby="attack-title"><p className="eyebrow">Selected attack</p><h2 id="attack-title">Prevalidated canonical scenario selected by Ollama</h2><div className="attack-facts">{component?.date && <div><span>Date</span><strong>{component.date}</strong></div>}{component?.shocks && Object.entries(component.shocks).map(([symbol, shock]) => <div key={symbol}><span>{symbol} shock</span><strong>{formatPercent(shock)}</strong></div>)}</div><p>Python constructs and evaluates the numerical scenario from the selected attack key.</p></section><section className="breaches" aria-labelledby="breach-title"><p className="eyebrow">Breach analysis</p><h2 id="breach-title">{breaches.length} verified risk-limit breach events</h2><p>Event types and ordering are shown exactly as recorded; no dates, magnitudes, or risk types are inferred.</p><ol>{breaches.map((event) => <li key={event.sequence}><span>{String(event.sequence).padStart(2, "0")}</span><div><strong>{label(event.eventType)}</strong><small>{event.scenarioId}</small></div></li>)}</ol></section></section>
    <section className="verification"><div><p className="eyebrow">Defender verification</p><h2>Reproduced <span>· replay delta {replay?.maxMetricDelta.toFixed(1) ?? "—"}</span></h2><p>The independent replay reproduced the deterministic engine result using the recorded evidence. This confirms replay agreement, not financial correctness.</p></div><div className="provenance"><p className="eyebrow">Release provenance</p><CopyHash label="Config SHA256" value={run.configSha256} /><CopyHash label="Data SHA256" value={run.dataSha256} /><p>Verified fixture <code>ollama-run-024/demo-telemetry.json</code></p></div></section>
    <section className="timeline"><div className="section-heading"><div><p className="eyebrow">Ordered evidence</p><h2>Canonical event timeline</h2></div><p>11 recorded events</p></div><ol>{run.events.map((event) => <li className={emphasis.has(event.eventType) ? "highlight" : ""} key={event.sequence}><span>{String(event.sequence).padStart(2, "0")}</span><div><em>{eventPhase(event.eventType)}</em><strong>{label(event.eventType)}</strong><small>{event.scenarioId ?? "run"}{event.roundNumber ? ` · round ${event.roundNumber}` : ""}</small></div></li>)}</ol></section>
  </main>;
}
