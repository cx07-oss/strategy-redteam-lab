import fixture from "../fixtures/canonical-product-fixture.js";

export type MetricSet = Readonly<{
  totalReturn: number;
  cagr: number | null;
  annualizedVolatility: number | null;
  sharpeRatio: number | null;
  sortinoRatio: number | null;
  maximumDrawdown: number | null;
}>;

export type CanonicalProduct = Readonly<{
  experimentId: string;
  strategyId: string;
  seed: number;
  manifest: Readonly<{
    datasetId: string; provider: string; startDate: string; endDate: string;
    rowCount: number; sha256: string; retrievedAt: string; adjustmentPolicy: string;
  }>;
  performance: MetricSet;
  grossReturn: number;
  netReturn: number;
  totalCost: number;
  turnover: number;
  benchmarkReturn: number;
  benchmarkExcess: number;
  oos: MetricSet;
  equity: readonly Readonly<{ date: string; strategy: number; benchmark: number; drawdown: number }>[];
  folds: readonly Readonly<{ testStart: string; testEnd: string; totalReturn: number }>[];
  regimes: readonly Readonly<{ regime: number; count: number; strategyReturn: number; benchmarkReturn: number; volatility: number | null; drawdown: number | null }>[];
  assignments: readonly Readonly<{ date: string; regime: number }>[];
  surface: readonly Readonly<{ volatility: number; correlation: number; result: number }>[];
  findings: readonly Readonly<{ id: string; title: string; rationale: string; family: string; status: string; degradation: number | null; evidence: string }>[];
  events: readonly Readonly<{ id: string; label: string; start: string; end: string }>[];
  provider: string;
  softwareVersion: string;
}>;

type Json = Record<string, unknown>;
const obj = (value: unknown, path: string): Json => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`Malformed canonical product at ${path}`);
  return value as Json;
};
const arr = (value: unknown, path: string): readonly unknown[] => {
  if (!Array.isArray(value)) throw new Error(`Malformed canonical product at ${path}`);
  return value;
};
const str = (value: unknown, path: string): string => {
  if (typeof value !== "string" || !value) throw new Error(`Malformed canonical product at ${path}`);
  return value;
};
const num = (value: unknown, path: string): number => {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`Malformed canonical product at ${path}`);
  return value;
};
const nullable = (value: unknown, path: string): number | null => value === null ? null : num(value, path);

function metrics(value: unknown, path: string): MetricSet {
  const item = obj(value, path);
  return {
    totalReturn: num(item.total_return, `${path}.total_return`),
    cagr: nullable(item.cagr, `${path}.cagr`),
    annualizedVolatility: nullable(item.annualized_volatility, `${path}.annualized_volatility`),
    sharpeRatio: nullable(item.sharpe_ratio, `${path}.sharpe_ratio`),
    sortinoRatio: nullable(item.sortino_ratio, `${path}.sortino_ratio`),
    maximumDrawdown: nullable(item.maximum_drawdown, `${path}.maximum_drawdown`),
  };
}

export function parseCanonicalProduct(value: unknown): CanonicalProduct {
  const root = obj(value, "root");
  const research = obj(root.research, "research");
  const manifest = obj(research.data_manifest, "research.data_manifest");
  const costs = obj(research.costs, "research.costs");
  const benchmark = obj(research.benchmark, "research.benchmark");
  const provider = obj(root.ai_provider, "ai_provider");
  return {
    experimentId: str(research.experiment_id, "research.experiment_id"),
    strategyId: "monthly-60-40",
    seed: num(research.seed, "research.seed"),
    manifest: {
      datasetId: str(manifest.dataset_id, "manifest.dataset_id"), provider: str(manifest.provider, "manifest.provider"),
      startDate: str(manifest.start_date, "manifest.start_date"), endDate: str(manifest.end_date, "manifest.end_date"),
      rowCount: num(manifest.row_count, "manifest.row_count"), sha256: str(manifest.sha256, "manifest.sha256"),
      retrievedAt: str(manifest.retrieved_at, "manifest.retrieved_at"), adjustmentPolicy: str(manifest.adjustment_policy, "manifest.adjustment_policy"),
    },
    performance: metrics(research.performance, "research.performance"),
    grossReturn: num(costs.gross_return, "costs.gross_return"), netReturn: num(costs.net_return, "costs.net_return"),
    totalCost: num(costs.total_trading_cost, "costs.total_trading_cost"), turnover: num(costs.turnover, "costs.turnover"),
    benchmarkReturn: num(benchmark.benchmark_return, "benchmark.benchmark_return"), benchmarkExcess: num(benchmark.excess_return, "benchmark.excess_return"),
    oos: metrics(research.walk_forward_out_of_sample, "research.walk_forward_out_of_sample"),
    equity: arr(research.equity_curve, "equity_curve").map((raw) => { const x = obj(raw, "equity_curve[]"); return { date: str(x.date, "equity.date"), strategy: num(x.strategy_equity, "equity.strategy"), benchmark: num(x.benchmark_equity, "equity.benchmark"), drawdown: num(x.drawdown, "equity.drawdown") }; }),
    folds: arr(research.walk_forward, "walk_forward").map((raw) => { const x = obj(raw, "walk_forward[]"); const p = obj(x.performance, "walk_forward.performance"); return { testStart: str(x.test_start, "fold.test_start"), testEnd: str(x.test_end, "fold.test_end"), totalReturn: num(p.total_return, "fold.total_return") }; }),
    regimes: arr(research.regime_summaries, "regime_summaries").map((raw) => { const x = obj(raw, "regime_summaries[]"); return { regime: num(x.regime, "regime.id"), count: num(x.observation_count, "regime.count"), strategyReturn: num(x.strategy_return, "regime.return"), benchmarkReturn: num(x.benchmark_return, "regime.benchmark"), volatility: nullable(x.volatility, "regime.volatility"), drawdown: nullable(x.maximum_drawdown, "regime.drawdown") }; }),
    assignments: arr(research.regime_assignments, "regime_assignments").map((raw) => { const x = obj(raw, "regime_assignments[]"); return { date: str(x.date, "assignment.date"), regime: num(x.regime, "assignment.regime") }; }),
    surface: arr(research.stress_surface, "stress_surface").map((raw) => { const x = obj(raw, "stress_surface[]"); return { volatility: num(x.volatility_multiplier, "surface.volatility"), correlation: num(x.correlation_shift, "surface.correlation"), result: num(x.result, "surface.result") }; }),
    findings: arr(root.ai_findings, "ai_findings").map((raw) => { const x = obj(raw, "ai_findings[]"); const h = obj(x.hypothesis, "finding.hypothesis"); return { id: str(h.hypothesis_id, "hypothesis.id"), title: str(h.title, "hypothesis.title"), rationale: str(h.rationale, "hypothesis.rationale"), family: str(h.supported_stress_family, "hypothesis.family"), status: str(x.verification_status, "finding.status"), degradation: nullable(x.observed_degradation, "finding.degradation"), evidence: str(x.evidence, "finding.evidence") }; }),
    events: arr(root.historical_events, "historical_events").map((raw) => { const x = obj(raw, "historical_events[]"); return { id: str(x.event_id, "event.id"), label: str(x.label, "event.label"), start: str(x.start_date, "event.start"), end: str(x.end_date, "event.end") }; }),
    provider: str(provider.provider_identifier, "provider.identifier"),
    softwareVersion: str(root.software_version, "software_version"),
  };
}

export const canonicalProduct = parseCanonicalProduct(fixture);
