# ADR-001: Agents Propose and Audit; Deterministic Code Owns Numerical Results

**Status:** Accepted

**Date:** 2026-08-23

## Context

The system uses language-model agents to search for plausible strategy-breaking conditions and explain verified evidence. Language-model output is probabilistic, can be malformed, can contain unsafe instructions, and is not an auditable numerical-computing substrate. A red-team report also needs reproducible dates, losses, thresholds, provenance, and replay results.

Combining proposal, calculation, and judgement inside one agent would make it difficult to distinguish a real strategy failure from invented evidence, prompt injection, numerical drift, or a changed dataset. Conversely, removing agents entirely would lose useful hypothesis generation and adversarial explanation.

## Decision

Use a strict authority boundary:

- The **attacker agent** proposes only bounded, structured scenarios. Its output is untrusted until it passes a versioned typed schema.
- The **deterministic engine** alone loads verified data, applies strategy/scenario semantics, calculates metrics and breach timing, ranks scenarios, and emits numerical evidence.
- The **defender agent** independently requests deterministic replay of the strongest scenarios, verifies provenance and comparisons, rejects invalid evidence, and writes conclusions grounded only in reproduced engine output.
- A **bounded client orchestrator** invokes attacker once and defender once, propagates experiment/trace IDs, enforces stopping rules, and persists artifacts. It does not create an open-ended agent dialogue.

Synthetic narrative, including a headline, is metadata only. It cannot supply executable code, commands, file paths, URLs to retrieve, tool instructions, or implicit numerical assumptions. Its complete operational meaning must be present in explicit numeric scenario fields.

## Consequences

### Benefits

- Results are repeatable from immutable bytes, canonical configuration, code version, and seed.
- Defender disagreement is observable as a provenance, validation, or replay verdict rather than hidden in prose.
- Prompt injection and malformed proposals are contained at a typed trust boundary.
- Agent prompts and models can evolve without changing the numerical contract.

### Costs and limitations

- Schemas, canonical serialization, data manifests, deterministic transforms, and comparison tolerances require explicit implementation and testing.
- Agents cannot improvise a new numerical stress operator without a separately reviewed engine/schema change.
- Reproduction verifies the implemented model and inputs, not whether a stress is probable or whether historical results predict future performance.
- Independent replay using the same engine version detects altered evidence and non-determinism but does not constitute independent model validation.

## Alternatives considered

### Let one agent propose and calculate

Rejected because numerical claims would not be reliably reproducible, typed, or attributable to exact data and code.

### Let attacker and defender negotiate until agreement

Rejected because it is unbounded, can reward persuasive prose over evidence, and obscures stopping and cost controls.

### Use the Foundry visual Workflow designer

Rejected for this project because the selected preview is scheduled to retire on 1 December 2026. Two code-based Hosted Agents plus a small explicit orchestrator make ownership and bounds inspectable.

### Use deterministic code only

Rejected as the complete design because bounded agents add useful adversarial hypothesis generation and evidence-focused explanation. Deterministic code nevertheless remains the sole numerical authority.

## Related documents

- [System specification](SPEC.md)
- [Project status](STATUS.md)
- [Repository instructions](../AGENTS.md)
