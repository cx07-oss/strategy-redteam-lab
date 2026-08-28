import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const fixtures = [
  ["../../artifacts/demo/ollama-run-024/demo-telemetry.json", "../src/fixtures/demo-telemetry.json"],
  ["../../artifacts/demo/ollama-run-025/demo-telemetry.json", "../src/fixtures/demo-telemetry-run-025.json"],
  ["../../artifacts/demo/stress-library-sustained/demo-telemetry.json", "../src/fixtures/demo-telemetry-stress-library-sustained.json"],
  ["../../artifacts/demo/stress-library-volatility/demo-telemetry.json", "../src/fixtures/demo-telemetry-stress-library-volatility.json"],
  ["../../artifacts/demo/stress-library-correlation/demo-telemetry.json", "../src/fixtures/demo-telemetry-stress-library-correlation.json"],
];

for (const [sourcePath, destinationPath] of fixtures) {
  const source = resolve(import.meta.dirname, sourcePath);
  const destination = resolve(import.meta.dirname, destinationPath);
  await mkdir(dirname(destination), { recursive: true });
  await copyFile(source, destination);
}
