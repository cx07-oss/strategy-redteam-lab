import type { Evaluation } from "./telemetry.ts";

export function stressImpact(evaluation: Evaluation): number {
  const stressDate = evaluation.scenario.components.find((component) => component.date !== null)?.date;
  const point = evaluation.chartPoints.find((candidate) => candidate.date === stressDate);
  if (!point) throw new Error("Validated evaluation has no chart point for its selected stress date.");
  return point.stressedEquity / point.baselineEquity - 1;
}

export function maximumStressedDrawdown(evaluation: Evaluation): number {
  if (evaluation.chartPoints.length === 0) throw new Error("Validated evaluation has no chart points.");
  return Math.max(...evaluation.chartPoints.map((point) => point.stressedDrawdown));
}
