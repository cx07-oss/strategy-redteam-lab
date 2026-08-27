import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const source = resolve(import.meta.dirname, "../../artifacts/demo/ollama-run-024/demo-telemetry.json");
const destination = resolve(import.meta.dirname, "../src/fixtures/demo-telemetry.json");

await mkdir(dirname(destination), { recursive: true });
await copyFile(source, destination);
