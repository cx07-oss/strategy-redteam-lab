# MVP 3 AI evaluation

The fixed, network-free evaluation set exercises the product AI boundary rather than model prose
quality. `tests/test_product.py` covers:

| Check | Expected behavior |
| --- | --- |
| Strict schema | Unknown, missing, non-finite, or family-mismatched fields fail validation |
| Supported family | Only correlation, volatility, and transaction-cost hypotheses execute |
| Uniqueness | Duplicate hypothesis IDs reject the entire batch |
| Reproducibility | Same dataset/config/seed produces the same engine verdict and evidence |
| Unsupported input | An out-of-dataset stress receives `unsupported`, without repair |
| Malformed provider output | Bounded deterministic hypotheses replace malformed JSON |
| Verifier authority | `reproduced`/`rejected` derives only from engine metric degradation |

The canonical deterministic provider requires no model call. LOCAL and LIVE providers share the
same strict JSON boundary and deterministic fallback; neither is invoked by CI or public demo
mode. The evaluation is intentionally small and does not claim semantic model quality or future
market likelihood.
