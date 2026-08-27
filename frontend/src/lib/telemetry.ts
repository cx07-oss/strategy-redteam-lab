export type ChartPoint = Readonly<{
  date: string;
  baselineEquity: number;
  stressedEquity: number;
  stressedDrawdown: number;
}>;

export type Evaluation = Readonly<{
  roundNumber: number;
  scenario: Readonly<{
    scenarioId: string;
    components: readonly ScenarioComponent[];
  }>;
  result: Readonly<{
    scenarioId: string;
    status: "valid" | "rejected";
    breachCount: number | null;
    maximumNormalizedExcess: number | null;
    rejectionCode: string | null;
    rejectionDetail: string | null;
    metrics: Record<string, unknown> | null;
  }>;
  chartPoints: readonly ChartPoint[];
}>;

export type ScenarioComponent = Readonly<{
  family: string;
  date: string | null;
  shocks: Readonly<Record<string, number>> | null;
}>;

export type DefenderVerdict = Readonly<{
  scenarioId: string;
  verdict: string;
  maxMetricDelta: number;
}>;

export type TelemetryEvent = Readonly<{
  sequence: number;
  eventType: string;
  roundNumber: number | null;
  scenarioId: string | null;
}>;

export type RunTelemetry = Readonly<{
  provider: string;
  modelIdentifier: string | null;
  verificationVerdict: string | null;
  dataSha256: string;
  configSha256: string;
  events: readonly TelemetryEvent[];
  evaluations: readonly Evaluation[];
  defenderVerdicts: readonly DefenderVerdict[];
}>;

type JsonObject = Record<string, unknown>;

function fail(path: string, message: string): never {
  throw new Error(`Malformed telemetry at ${path}: ${message}`);
}

function object(value: unknown, path: string): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail(path, "expected object");
  return value as JsonObject;
}

function array(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) fail(path, "expected array");
  return value;
}

function text(value: unknown, path: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || value.length === 0) fail(path, "expected non-empty string");
  return value;
}

function number(value: unknown, path: string, nullable = false): number | null {
  if (nullable && value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) fail(path, "expected finite number");
  return value;
}

function integer(value: unknown, path: string, nullable = false): number | null {
  const parsed = number(value, path, nullable);
  if (parsed !== null && !Number.isInteger(parsed)) fail(path, "expected integer");
  return parsed;
}

function numericRecord(value: unknown, path: string): Readonly<Record<string, number>> | null {
  if (value === null) return null;
  const candidate = object(value, path);
  return Object.fromEntries(Object.entries(candidate).map(([key, item]) => [key, number(item, `${path}.${key}`)!]));
}

function parseChartPoint(value: unknown, index: number): ChartPoint {
  const point = object(value, `evaluations[].chart_points[${index}]`);
  return {
    date: text(point.date, "chart point.date")!,
    baselineEquity: number(point.baseline_equity, "chart point.baseline_equity")!,
    stressedEquity: number(point.stressed_equity, "chart point.stressed_equity")!,
    stressedDrawdown: number(point.stressed_drawdown, "chart point.stressed_drawdown")!,
  };
}

function parseEvaluation(value: unknown, index: number): Evaluation {
  const evaluation = object(value, `evaluations[${index}]`);
  const result = object(evaluation.result, `evaluations[${index}].result`);
  const status = text(result.status, "result.status");
  if (status !== "valid" && status !== "rejected") fail("result.status", "expected valid or rejected");
  const metrics = result.metrics;
  if (metrics !== null && (typeof metrics !== "object" || Array.isArray(metrics))) fail("result.metrics", "expected object or null");
  const scenario = object(evaluation.scenario, "evaluation.scenario");
  const components = array(scenario.components, "scenario.components").map((value, componentIndex) => {
    const component = object(value, `scenario.components[${componentIndex}]`);
    return {
      family: text(component.family, "scenario.component.family")!,
      date: text(component.date, "scenario.component.date", true),
      shocks: numericRecord(component.shocks, "scenario.component.shocks"),
    };
  });
  return {
    roundNumber: integer(evaluation.round_number, "evaluation.round_number")!,
    scenario: { scenarioId: text(scenario.scenario_id, "scenario.scenario_id")!, components },
    result: {
      scenarioId: text(result.scenario_id, "result.scenario_id")!,
      status,
      breachCount: integer(result.breach_count, "result.breach_count", true),
      maximumNormalizedExcess: number(result.maximum_normalized_excess, "result.maximum_normalized_excess", true),
      rejectionCode: text(result.rejection_code, "result.rejection_code", true),
      rejectionDetail: text(result.rejection_detail, "result.rejection_detail", true),
      metrics: metrics === null ? null : (metrics as JsonObject),
    },
    chartPoints: array(evaluation.chart_points, "evaluation.chart_points").map(parseChartPoint),
  };
}

export function parseRunTelemetry(value: unknown): RunTelemetry {
  const run = object(value, "run");
  const events = array(run.events, "events").map((value, index) => {
    const event = object(value, `events[${index}]`);
    return {
      sequence: integer(event.sequence, "event.sequence")!,
      eventType: text(event.event_type, "event.event_type")!,
      roundNumber: integer(event.round_number, "event.round_number", true),
      scenarioId: text(event.scenario_id, "event.scenario_id", true),
    };
  }).sort((left, right) => left.sequence - right.sequence);
  if (events.some((event, index) => event.sequence !== index + 1)) fail("events", "sequence must be contiguous from one");
  const defenderVerdicts = array(run.defender_verdicts, "defender_verdicts").map((value, index) => {
    const verdict = object(value, `defender_verdicts[${index}]`);
    return {
      scenarioId: text(verdict.scenario_id, "defender_verdict.scenario_id")!,
      verdict: text(verdict.verdict, "defender_verdict.verdict")!,
      maxMetricDelta: number(verdict.max_metric_delta, "defender_verdict.max_metric_delta")!,
    };
  });
  return {
    provider: text(run.provider, "provider")!,
    modelIdentifier: text(run.model_identifier, "model_identifier", true),
    verificationVerdict: text(run.verification_verdict, "verification_verdict", true),
    dataSha256: text(object(run.dataset_manifest, "dataset_manifest").sha256, "dataset_manifest.sha256")!,
    configSha256: text(run.config_sha256, "config_sha256")!,
    events,
    evaluations: array(run.evaluations, "evaluations").map(parseEvaluation),
    defenderVerdicts,
  };
}

export function selectValidEvaluation(run: RunTelemetry): Evaluation {
  const evaluation = run.evaluations.find((candidate) => candidate.result.status === "valid");
  if (!evaluation) throw new Error("Telemetry contains no valid evaluation for replay.");
  return evaluation;
}
