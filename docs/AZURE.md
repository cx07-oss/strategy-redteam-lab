# Microsoft Foundry Hosted Agent packaging

Access date for every page below: **2026-08-23**. Only current official Microsoft Learn
documentation was used; no blog or legacy sample is a design authority for Gate 10.

## Consulted Microsoft Learn pages

- Hosted Agents concepts: <https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents>
- Hosted Agent runtime contract: <https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agent-contract>
- Deploy a Hosted Agent: <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent>
- Deploy a Hosted Agent from source: <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent-code>
- Author the unified `azure.yaml`: <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/author-azure-yaml>
- Unified `azure.yaml` reference: <https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/azure-yaml-reference>
- Migrate from the initial preview backend: <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/migrate-hosted-agent-preview>
- Agent Framework sequential orchestration: <https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/sequential>
- Trace Agent Framework in Foundry: <https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-framework>
- Agent Framework workflow observability: <https://learn.microsoft.com/en-us/agent-framework/workflows/observability>

## Gate 10 decisions derived from current documentation

- Both applications use the non-conversational Invocations protocol. The current
  `azure-ai-agentserver-invocations` host supplies `POST /invocations`, `GET /readiness`,
  HTTP/1.1 on port 8088, request/session headers, graceful shutdown, and OpenTelemetry.
- `azure.yaml` declares protocol version `2.0.0`, so the current multi-user request context
  is supported. Application state is not partitioned or persisted by user because both calls
  are stateless and all evidence identifiers are explicit in the typed payload.
- The unified root `azure.yaml` replaces `agent.manifest.yaml` plus `agent.yaml`. Environment
  variables are directly below each agent service; reserved `FOUNDRY_*` variables are not
  declared. The project service uses an explicit existing-project endpoint and declares no
  model deployment or other resource to provision.
- Current source deployment supports `python_3_13` and `python_3_14`. Gate 10 selects
  `python_3_13` with `remote_build`, while the code and tooling retain the repository's
  Python 3.11 language floor. The latest unified `azure.yaml` reference uses scalar
  `entryPoint: main.py`; that newer schema form takes precedence over an older array-shaped
  snippet on the source-deployment page.
- The initial preview backend was retired on **2026-08-20**. The packages
  `azure-ai-agentserver-agentframework` and `azure-ai-agentserver-langgraph` are not used.
  Model adapters use current `agent-framework-core`, `agent-framework-foundry`,
  `FoundryChatClient`, and `Agent`; the custom JSON endpoint uses the current Invocations host.
- `DefaultAzureCredential` is the only credential constructor. In Hosted Agents it resolves
  the platform-created dedicated agent managed identity. Blob URLs contain no SAS token,
  account key, password, or connection string. Before a future deployment, grant each agent
  identity only Blob Data Reader for datasets and Blob Data Contributor for its artifact
  destination. Gate 10 performs no role assignment.
- Agent Framework sequential orchestration was reviewed, including ordered participants and
  trace propagation, but it is intentionally not implemented in this gate. The specification
  keeps orchestration in the separate bounded client: invoke attacker once, then defender once.
- The Agent Server host creates protocol spans automatically when Azure Monitor or OTLP is
  configured. The applications add child spans containing only experiment/dataset identifiers
  and bounded counts. Sensitive-content tracing is not enabled, and raw prompts, model output,
  credentials, and daily price data are not attached to spans.

## Local packaging workflow

`python scripts/build_hosted_packages.py` creates two isolated deterministic source trees and
ZIP files under `dist/hosted/`. Each contains only `main.py`, the shared
`strategy_redteam` source, fixed prompts, the versioned attack policy, `.agentignore`, the full
locked dependency file, and a SHA-256 package manifest. `.env`, `.azure`, caches, run outputs,
logs, credential-like files, and secret-like files are excluded.

No `az`, `azd`, portal, provisioning, deployment, role-assignment, or other Azure mutation is
part of Gate 10.

## Gate 11 deployment and runtime acceptance

Gate 11 was accepted on **2026-08-25** against the existing East US 2 Foundry account
`zcxie-test-5455-resource`, project `zcxie-test-5455`, and `gpt-4.1-mini` deployment in subscription
`1d7a7fd7-0893-4ead-8a86-e671cc9e47b1`. Deployment used clean `main` commit
`3395dc9767cde89f4f1672c7206ed5af18e2945a` and only this command:

```powershell
azd deploy attacker-hosted -e gate11-attacker --no-prompt --timeout 1200
```

`strategy-redteam-attacker` version 3 reached `active`. Its managed identity is
`42c5143f-b009-42bf-a417-1fc47e983792`; its endpoint is
`https://zcxie-test-5455-resource.services.ai.azure.com/api/projects/zcxie-test-5455/agents/strategy-redteam-attacker/endpoint/protocols/invocations?api-version=v1`.
The downloaded version-3 source contained the same 19 paths, byte lengths, and content hashes as
the clean-HEAD audited package. The deterministic ZIP SHA-256 was
`6d33517a9bec715f541b151bfcf8677963f05832d42c777bc95d68a9fcfd1f1a`; Azure's downloaded outer
ZIP/content hash was `f98846a9390577607b4320ae5ea4266815051205a7ffc266e90a05a6317cea30`.

The permanent runtime role set is exactly:

- `Storage Blob Data Reader` on
  `/subscriptions/1d7a7fd7-0893-4ead-8a86-e671cc9e47b1/resourceGroups/rg-zcxie-7188/providers/Microsoft.Storage/storageAccounts/strt5455g11/blobServices/default/containers/strategy-redteam-datasets`,
  assignment `de0d68ac-8dee-40d2-a944-fe2c0c8338f9`.
- `Storage Blob Data Contributor` on
  `/subscriptions/1d7a7fd7-0893-4ead-8a86-e671cc9e47b1/resourceGroups/rg-zcxie-7188/providers/Microsoft.Storage/storageAccounts/strt5455g11/blobServices/default/containers/strategy-redteam-attacker-artifacts`,
  assignment `592a3f99-bce9-496e-a733-b0eeabd492c1`.

Exactly one smoke invocation ran with experiment `gate11b-attacker-smoke-003`, seed `20260823`,
and budgets `1/1/1/1`. It returned a schema-valid 6,901-byte response and exactly seven verified
artifacts below `strategy-redteam/attacker/gate11b-attacker-smoke-003/`; dataset and manifest hashes,
artifact hashes, schemas, and provenance passed the committed verifier. Correlation identifiers are
session `224985de71892b8700Mp31Kt6VnCfUlMTEaEpA4gIzJ2NTW4P1`, invocation
`inv_c29f7b05904e118700jnuuTFkHsWRDy9UUxj1DYaVW0hF1wS5R`, APIM request
`b6e9e94d-78fd-4229-9a0a-b79a9468f147`, and Application Insights operation
`ebf02cf84d6b8f27efa825bbcffc093c`.

The correlated trace proves managed-identity dataset access, one successful project-endpoint model
call, the custom attacker span, artifact Blob creates, and HTTP 200 completion. Trace ingestion used
the existing project/Application Insights connection; Entra-only ingestion and
`Monitoring Metrics Publisher` remain deferred. No monitoring role was assigned.

The user's temporary artifact-container Blob Reader assignments were both removed in `finally`, and
authoritative readback returned zero matches. Final inventory remained six existing ARM resources
and one Foundry agent. No defender, ACR, tool, provisioned throughput, extra model, storage account,
monitoring resource, broader role, `azd up`, `azd provision`, or destructive cleanup was used.
