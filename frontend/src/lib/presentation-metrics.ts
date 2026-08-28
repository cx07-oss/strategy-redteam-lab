import type { Evaluation } from "./telemetry.ts";

export function stressImpact(evaluation: Evaluation): number {
  const stressDate = evaluation.scenario.components.find((component) => component.date !== null || component.startDate !== null);
  const date = stressDate?.date ?? stressDate?.startDate;
  const point = evaluation.chartPoints.find((candidate) => candidate.date === date);
  if (!point) throw new Error("Validated evaluation has no chart point for its selected stress date.");
  return point.stressedEquity / point.baselineEquity - 1;
}

export function finalPortfolioImpact(evaluation: Evaluation): number {
  const point = evaluation.chartPoints.at(-1);
  if (!point) throw new Error("Validated evaluation has no chart points.");
  return point.stressedEquity / point.baselineEquity - 1;
}

export function maximumStressedDrawdown(evaluation: Evaluation): number {
  if (evaluation.chartPoints.length === 0) throw new Error("Validated evaluation has no chart points.");
  return Math.max(...evaluation.chartPoints.map((point) => point.stressedDrawdown));
}
