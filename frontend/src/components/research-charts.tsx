"use client";

import { useState } from "react";
import type { CanonicalProduct } from "@/lib/canonical-product";

const pct = (value: number) => `${(value * 100).toFixed(1)}%`;

export function EquityChart({ product }: { product: CanonicalProduct }) {
  const [focus, setFocus] = useState(product.equity.length - 1);
  const points = product.equity;
  const values = points.flatMap((point) => [point.strategy, point.benchmark]);
  const min = Math.min(...values), max = Math.max(...values);
  const path = (field: "strategy" | "benchmark") => points.map((point, index) => {
    const x = index / (points.length - 1) * 100;
    const y = 96 - (point[field] - min) / (max - min) * 90;
    return `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const selected = points[Math.max(0, focus)]!;
  return <section className="panel chart-panel">
    <div className="panel-head"><div><span className="eyebrow">Growth of 1.00</span><h2>Equity vs benchmark</h2></div><div className="legend"><i className="strategy-key" />Strategy <i className="benchmark-key" />Benchmark</div></div>
    <div className="chart-tooltip"><strong>{selected.date}</strong><span>Strategy {selected.strategy.toFixed(3)}</span><span>Benchmark {selected.benchmark.toFixed(3)}</span></div>
    <svg className="equity-chart" viewBox="0 0 100 100" role="img" aria-label="Strategy and benchmark equity curve" onPointerMove={(event) => { const bounds = event.currentTarget.getBoundingClientRect(); setFocus(Math.round((event.clientX - bounds.left) / bounds.width * (points.length - 1))); }}>
      <path className="grid" d="M0 25H100M0 50H100M0 75H100" />
      <path className="benchmark-line" d={path("benchmark")} /><path className="strategy-line" d={path("strategy")} />
    </svg>
    <div className="axis"><span>{points[0]?.date}</span><span>normalized equity</span><span>{points.at(-1)?.date}</span></div>
  </section>;
}

export function WalkForwardChart({ product }: { product: CanonicalProduct }) {
  const max = Math.max(...product.folds.map((fold) => Math.abs(fold.totalReturn)), .01);
  return <section className="panel"><div className="panel-head"><div><span className="eyebrow">Strictly later test windows</span><h2>Walk-forward OOS returns</h2></div></div><div className="bar-chart">{product.folds.map((fold) => <div className="bar-item" key={fold.testStart} title={`${fold.testStart} to ${fold.testEnd}: ${pct(fold.totalReturn)}`}><div className={fold.totalReturn >= 0 ? "bar positive" : "bar negative"} style={{ height: `${Math.max(3, Math.abs(fold.totalReturn) / max * 100)}%` }} /><small>{fold.testStart.slice(0, 4)}</small></div>)}</div><p className="caption">Each bar is an engine replay on an expanding training prefix followed by an untouched test period.</p></section>;
}

export function RegimeTimeline({ product }: { product: CanonicalProduct }) {
  return <section className="panel"><div className="panel-head"><div><span className="eyebrow">Unsupervised GMM</span><h2>Regime timeline</h2></div><span className="muted">Labels are numeric, not event targets</span></div><div className="regime-strip" aria-label="Data-driven regime assignments">{product.assignments.map((item) => <i key={item.date} className={`regime-${item.regime}`} title={`${item.date}: regime ${item.regime}`} />)}</div><div className="event-row">{product.events.map((event) => <span key={event.id}><b>{event.label}</b>{event.start} – {event.end}</span>)}</div></section>;
}

export function StressHeatmap({ product }: { product: CanonicalProduct }) {
  const worst = Math.min(...product.surface.map((point) => point.result));
  const best = Math.max(...product.surface.map((point) => point.result));
  return <section className="panel"><div className="panel-head"><div><span className="eyebrow">Existing deterministic engine grid</span><h2>Stress surface</h2></div><span className="muted">cell = total net return</span></div><div className="heatmap">{product.surface.map((point) => { const strength = (point.result - worst) / (best - worst || 1); return <div key={`${point.volatility}-${point.correlation}`} style={{ background: `rgba(89, 155, 125, ${.12 + strength * .35})` }}><strong>{pct(point.result)}</strong><span>{point.volatility.toFixed(1)}× vol · {point.correlation >= 0 ? "+" : ""}{point.correlation.toFixed(2)} corr</span></div>; })}</div><p className="caption">This bounded grid improved terminal return on the observed path; it is not presented as a failure. The AI hypotheses below are verified separately.</p></section>;
}
