import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { canonicalProduct, parseCanonicalProduct } from "../src/lib/canonical-product.ts";

test("canonical historical product evidence remains exact and complete", () => {
  assert.equal(canonicalProduct.manifest.provider, "yfinance");
  assert.equal(canonicalProduct.manifest.rowCount, 4780);
  assert.equal(canonicalProduct.manifest.startDate, "2007-01-03");
  assert.equal(canonicalProduct.manifest.endDate, "2025-12-31");
  assert.equal(canonicalProduct.netReturn, 3.548109205592861);
  assert.equal(canonicalProduct.performance.sharpeRatio, 0.7682501948324615);
  assert.deepEqual(canonicalProduct.regimes.map((item) => item.count), [559, 530, 101, 722]);
  assert.deepEqual(canonicalProduct.findings.map((item) => item.status), ["reproduced", "reproduced", "reproduced"]);
  assert.equal(canonicalProduct.equity.at(-1)?.date, "2025-12-31");
});

test("canonical parser and product UX fail safely", () => {
  assert.throws(() => parseCanonicalProduct({ research: null }), /Malformed canonical product/);
  const source = readFileSync(new URL("../src/components/product-app.tsx", import.meta.url), "utf8");
  assert.match(source, /API unavailable/);
  assert.match(source, /No experiments match/);
  assert.match(source, /Execution is disabled in public demo mode/);
  assert.match(source, /Comparison requires a connected API/);
  assert.match(source, /Loading persisted experiment/);
});
