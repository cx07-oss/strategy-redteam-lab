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
