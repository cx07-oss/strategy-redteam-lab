import type { ChartPoint } from "@/lib/telemetry";

export function DrawdownChart({ points }: { points: readonly ChartPoint[] }) {
  const area = points.map((point, index) => `${(index / (points.length - 1)) * 100},${100 - point.stressedDrawdown * 100 / Math.max(...points.map((item) => item.stressedDrawdown), 0.01)}`).join(" ");
  return <section className="drawdown" aria-labelledby="drawdown-title"><p className="eyebrow">Risk path</p><h2 id="drawdown-title">Stressed drawdown</h2><svg viewBox="0 0 100 100" role="img" aria-label="Stressed drawdown across the verified scenario" preserveAspectRatio="none"><polyline points={area} /></svg></section>;
}
