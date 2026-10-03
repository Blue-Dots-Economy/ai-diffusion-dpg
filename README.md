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
| Reach Layer | 8005–8008 | The bridge (OpenAI chat-completions compatible, the default integration) plus optional web chat (local/dev), CLI, voice (telephony) and MCP. |
| Action Gateway | 9999 | Calls external systems through declared tools. |

`dev-kit` (8080) is a configuration tool, not a runtime block.

## Channels

The **bridge** is the default and the only channel enabled out of the box. It is a generic OpenAI chat-completions-compatible endpoint (`POST /v1/chat/completions`, port 8008), so any system that speaks that API can connect to the agent, for example your own voice pipeline. [VoicERA](https://github.com/COSS-India/VoicEra), an external DPG voice service, is one example of such a pipeline.

The other channels are optional and need enabling: a channel block in the use case's `agent_core.yaml` and `reach_layer.yaml`, and a compose profile (`web`, `voice` or `mcp`). The [optional channels guide](https://docs.bluedotseconomy.org/guides/installation/local-setup/ai-diffusion-channels/) lists the exact edits for each.

- **Web chat** (8005): for local/dev testing only. It runs without login; Google sign-in is optional (`auth.enabled: true` plus `GOOGLE_CLIENT_ID` and `REACH_SESSION_SECRET`).
- **CLI**: a terminal client, run as a container on the stack's network.
- **Voice** (8006): phone calls through a telephony provider.
- **MCP** (8007): the agent as tools for an MCP host.

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
DOMAIN=blue-dots
DPG_IMAGE_TAG=<release tag>
ENV
GIT_SHA=<release tag> docker compose -f docker-compose.yml build action_gateway agent_core knowledge_engine memory_layer observability_layer trust_layer reach_layer_bridge dev_kit
COMPOSE="docker compose -f docker-compose.dev.yml -f local-signals.override.yml"
DOMAIN=blue-dots $COMPOSE up -d --wait redis memgraph action_gateway knowledge_engine memory_layer trust_layer observability_layer agent_core reach_layer_bridge dev_kit otelcol jaeger loki prometheus grafana
curl -s localhost:8008/health
curl -sN -X POST http://127.0.0.1:8008/v1/chat/completions -H 'content-type: application/json' -d '{
    "model": "dpg", "stream": true,
    "messages": [{"role": "user", "content": "नमस्ते"}],
    "metadata": {"caller_phone": "9199000000101", "call_id": "run-call-003"}}'
```

This exact sequence was not exercised end to end. The verified path, against a local Signals, is the [local setup guide](https://docs.bluedotseconomy.org/guides/installation/local-setup/ai-diffusion-dpg/).

`local-signals.override.yml` publishes the bridge on `127.0.0.1:8008` (loopback only) and moves dev-kit to 8081 and Loki to 3101; with no `SIGNALS_*` variables set, the tools still call the hosted Signals. `/health` means the service is up, not that chat works. The chat request is one turn through the bridge, with `curl` standing in for any OpenAI-compatible client; it streams `data:` chunks and ends with `data: [DONE]`. Send `metadata.caller_phone` as the caller's phone number, digits only with the country code first, and keep `metadata.call_id` the same for the whole call.

Pass `DOMAIN=blue-dots` on the command line: a `DOMAIN` already set in your shell overrides `.env`. The dev compose file has no `build:` sections, so it uses the images built in the previous step (`DPG_IMAGE_TAG` must equal the `GIT_SHA` you built with).

Releases are tagged `<YYYYMM>-s<sprint>-rc<n>` (for example `202610-s1-rc1`), and a release's images carry the same tag. Check out the release tag and build with it. Until the first release tag is published, build from `main` and use `local` as the tag.

The Blue Dots configuration in this checkout sets its Signals URLs as `${SIGNALS_*:-…}` placeholders, which only an Action Gateway image built from this commit or later expands. Build the images from this checkout, as above, or use a release tag that includes this change. An older image, such as the default `sha-646216d` or `latest`, would call the placeholder text as the URL.

## Repository map

- `action_gateway/` - Action Gateway: calls external systems through declared tools.
- `agent_core/` - Agent Core: the per-turn orchestrator, plus the eval suites in `agent_core/eval/`.
- `automation/` - Docker Compose files, dashboards and deployment assets.
- `dev-kit/` - the configuration kit: framework defaults in `dev-kit/dpg/`, per-use-case overrides in `dev-kit/configs/`, and the configuration agent UI.
- `docs/` - design notes, gap analyses and reference PDFs; designs live in `docs/superpowers/specs/`.
- `knowledge_engine/` - Knowledge Engine: retrieval and glossary mapping.
- `memory_layer/` - Memory Layer: sessions, profiles, saved tool results, audit.
- `observability_layer/` - Observability Layer: traces, metrics and outcome events.
- `reach_layer/` - Reach Layer: the bridge (the default), plus the optional web chat (local/dev), CLI, voice and MCP channels.
- `trust_layer/` - Trust Layer: checks, consent, constraints and handoff.

## Documentation

- [Architecture](https://docs.bluedotseconomy.org/core-concepts/architecture/ai-diffusion-dpg/): how the blocks fit together and how a turn runs.
- [Configuration](https://docs.bluedotseconomy.org/core-concepts/architecture/ai-diffusion-configuration/): the configuration layers and what each YAML file controls.
- [Local setup guide](https://docs.bluedotseconomy.org/guides/installation/local-setup/ai-diffusion-dpg/): running the stack, including against a local Signals.
- `docs/superpowers/specs/`: design documents.
- `agent_core/eval/`: NLU, scenario and voice-bench evaluations.
- [ARCHITECTURE.md](ARCHITECTURE.md): the contributor map.

## Contributing

- Open pull requests into `main`.
- A runtime schema change must update the dev-kit mirror; see `.claude/rules/runtime-devkit-sync.md`.
- Run each block's tests with `cd <block> && uv run --extra dev pytest`.

## Licence

See [LICENSE](LICENSE).
