# Strategy Red-Team Lab

> Research only; not investment advice. This repository stress-tests a fixed strategy and does
> not recommend portfolios, forecast returns, or place trades.

## Local setup

Use Python 3.11 from the repository root:

```powershell
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev,hosted]'
```

The local run is deterministic and offline. It uses no Azure service or model client.

## Offline demo

Run the checked-in correlation-break fixture:

```powershell
& '.\.venv\Scripts\redteam.exe' run `
  --experiment config\example_60_40.yaml `
  --dataset tests\fixtures\offline-cache\manifests\correlation-break.json `
  --mode offline `
  --output artifacts\offline-fixture
```

The output directory is immutable and must not already exist. A successful command prints
`status=verified`; any schema, dataset hash, defender replay, or artifact-integrity failure returns
a nonzero exit code. Review the independently replayed report—not the attacker draft:

```powershell
Get-Content artifacts\offline-fixture\failure_report.md
```

The fixture has a known switch from negatively correlated, lower-volatility SPY/TLT returns to
positively correlated, higher-volatility returns. The report must connect the explicit stress,
both sleeves' linked contributions, a named rule and onset date, and reproduction from the same
dataset/config hashes. A statement such as “the Sharpe ratio fell” is not sufficient.

For a separate manual real-data example, first download SPY/TLT into the ignored immutable cache:

```powershell
& '.\.venv\Scripts\redteam.exe' data download `
  --start 2020-01-02 `
  --end 2024-12-31
```

Copy the emitted manifest path into `--dataset` and choose a new output directory:

```powershell
& '.\.venv\Scripts\redteam.exe' run `
  --experiment config\example_60_40.yaml `
  --dataset '<emitted .data-cache manifest path>' `
  --mode offline `
  --output artifacts\offline-spy-tlt
```

The download is an explicit manual network action. Tests use only the fixed local fixture and do
not assert live market values.

## Verified local-Ollama demo telemetry

Set `model_provider.provider: ollama` in a copy of the experiment YAML and configure the local
model with `STRATEGY_REDTEAM_OLLAMA_MODEL`. Then run one bounded, immutable demo bundle:

```powershell
& '.\.venv\Scripts\redteam.exe' demo run --experiment <ollama-experiment.yaml> --dataset tests\fixtures\offline-cache\manifests\correlation-break.json --output artifacts\demo\ollama-run-001
```

It runs the real attacker, deterministic engine, and independent defender before exporting the
canonical `artifacts\demo\ollama-run-001\demo-telemetry.json`. The JSON validates as Gate 12D
`RunTelemetry`; it contains only typed, verified evidence. Ollama proposals are not deterministic;
the dataset/config hashes, seed, validated parameters, and engine results are recorded.
