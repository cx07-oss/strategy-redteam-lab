import fixture from "@/fixtures/demo-telemetry.json";
import { ReplayDashboard } from "@/components/replay-dashboard";
import { parseRunTelemetry, selectValidEvaluation } from "@/lib/telemetry";

const run = parseRunTelemetry(fixture);
const evaluation = selectValidEvaluation(run);
export default function ReplayPage() {
  return <ReplayDashboard run={run} evaluation={evaluation} />;
}
