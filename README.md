# AI Diffusion DPG

AI Diffusion is a set of seven building blocks for voice and chat assistants that help people find opportunities, such as jobs, on a Blue Dots network.
The runtime blocks are fixed. Everything specific to your use case (persona, knowledge, safety rules, connectors) is YAML in a configuration kit, so a new deployment needs no code changes.
The reference use case is Blue Dots (`dev-kit/configs/blue-dots/`).

## The seven blocks

| Block | Port | Responsibility |
|---|---|---|
| Agent Core | 8000 | Runs each turn; the only caller of the LLM and of the other blocks. |
| Knowledge Engine | 8001 | Retrieval over use-case documents and glossaries, called as a tool. |
| Memory Layer | 8002 | Session state, profile graph, saved tool results, audit. |
| Trust Layer | 8003 | Input and output checks, consent, constraints, human handoff; fails closed. |
| Observability Layer | 8004 | Traces, metrics, turn and outcome events (async). |
| Reach Layer | 8005–8008 | Channels (web, voice/telephony, MCP, VoicERA bridge). |
| Action Gateway | 9999 | Calls external systems through declared tools. |

`dev-kit` (8080) is a configuration tool, not a runtime block.

## Quick start

This starts the blocks against the hosted Blue Dots UAT Signals, so no `SIGNALS_*` variables are needed. You do need your `BLUE_DOTS_*` keys and an OpenAI key. To run against a local Signals instead, follow the [local setup guide](https://docs.bluedotseconomy.org/guides/installation/local-setup/ai-diffusion-dpg/).

```bash
cd automation/docker
cat > .env <<'ENV'
OPENAI_API_KEY=<your OpenAI key>
BLUE_DOTS_API_KEY=<your Signals service key>
BLUE_DOTS_SEARCH_API_KEY=<your Signals service key>
BLUE_DOTS_ORG_ID=<your Signals organisation id>
TOOL_RESULT_KEY_SECRET=<output of: openssl rand -hex 32>
REACH_SESSION_SECRET=<output of: openssl rand -hex 32>
GOOGLE_CLIENT_ID=<your Google OAuth client id>
DOMAIN=blue-dots
DPG_IMAGE_TAG=<short git sha>
ENV
GIT_SHA=<short git sha> docker compose -f docker-compose.yml build action_gateway agent_core knowledge_engine memory_layer observability_layer trust_layer reach_layer_web dev_kit
DOMAIN=blue-dots docker compose -f docker-compose.dev.yml up -d --wait redis memgraph action_gateway knowledge_engine memory_layer trust_layer observability_layer agent_core reach_layer_web dev_kit otelcol jaeger loki prometheus grafana
curl -s localhost:8005/health
```

This exact sequence was not exercised end to end. The verified path, against a local Signals, is the [local setup guide](https://docs.bluedotseconomy.org/guides/installation/local-setup/ai-diffusion-dpg/).

`/health` means the service is up, not that chat works. The web chat needs a real Google OAuth client, otherwise `/chat` returns 401. `REACH_SESSION_SECRET` and `GOOGLE_CLIENT_ID` are still required, or `reach_layer_web` won't boot.

Pass `DOMAIN=blue-dots` on the command line: a `DOMAIN` already set in your shell overrides `.env`. The dev compose file has no `build:` sections, so it uses the images built in the previous step (`DPG_IMAGE_TAG` must equal the `GIT_SHA` you built with).

## Repository map

- `action_gateway/` - Action Gateway: calls external systems through declared tools.
- `agent_core/` - Agent Core: the per-turn orchestrator, plus the eval suites in `agent_core/eval/`.
- `automation/` - Docker Compose files, dashboards and deployment assets.
- `dev-kit/` - the configuration kit: framework defaults in `dev-kit/dpg/`, per-use-case overrides in `dev-kit/configs/`, and the configuration agent UI.
- `docs/` - design notes, gap analyses and reference PDFs; designs live in `docs/superpowers/specs/`.
- `knowledge_engine/` - Knowledge Engine: retrieval and glossary mapping.
- `memory_layer/` - Memory Layer: sessions, profiles, saved tool results, audit.
- `observability_layer/` - Observability Layer: traces, metrics and outcome events.
- `reach_layer/` - Reach Layer: web, voice, MCP and VoicERA bridge channels.
- `trust_layer/` - Trust Layer: checks, consent, constraints and handoff.

## Documentation

- [Architecture](https://docs.bluedotseconomy.org/core-concepts/architecture/ai-diffusion-dpg/): how the blocks fit together and how a turn runs.
- [Configuration](https://docs.bluedotseconomy.org/core-concepts/architecture/ai-diffusion-configuration/): the configuration layers and what each YAML file controls.
- [Local setup guide](https://docs.bluedotseconomy.org/guides/installation/local-setup/ai-diffusion-dpg/): running the stack, including against a local Signals.
- `docs/superpowers/specs/`: design documents.
- `agent_core/eval/`: NLU, scenario and voice-bench evaluations.
- [ARCHITECTURE.md](ARCHITECTURE.md): the contributor map.

## Contributing

- Open branches as pull requests into `deploy/voicera-vm`.
- A runtime schema change must update the dev-kit mirror; see `.claude/rules/runtime-devkit-sync.md`.
- Run each block's tests with `cd <block> && uv run --extra dev pytest`.

## Licence

See [LICENSE](LICENSE).
