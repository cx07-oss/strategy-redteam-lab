import type { ChartPoint } from "@/lib/telemetry";

type Props = { points: readonly ChartPoint[] };

function pathFor(points: readonly ChartPoint[], field: "baselineEquity" | "stressedEquity", min: number, max: number) {
  return points.map((point, index) => {
    const x = (index / (points.length - 1)) * 100;
    const y = 100 - ((point[field] - min) / (max - min || 1)) * 100;
    return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

export function PerformanceChart({ points }: Props) {
  const values = points.flatMap((point) => [point.baselineEquity, point.stressedEquity]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  return <section className="chart-panel" aria-labelledby="performance-title">
    <div className="section-heading"><div><p className="eyebrow">Deterministic evidence</p><h2 id="performance-title">Strategy performance</h2></div><div className="legend"><span className="baseline" />Baseline equity <span className="stressed" />Stressed equity</div></div>
    <svg viewBox="0 0 100 100" role="img" aria-label="Baseline and stressed equity across the verified scenario" preserveAspectRatio="none">
      <path className="grid" d="M0 25H100M0 50H100M0 75H100" />
      <path className="line baseline-line" d={pathFor(points, "baselineEquity", min, max)} />
      <path className="line stressed-line" d={pathFor(points, "stressedEquity", min, max)} />
    </svg>
    <div className="axis"><span>{points[0]?.date}</span><span>{points.at(-1)?.date}</span></div>
  </section>;
}
