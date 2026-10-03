# AI Diffusion DPG documentation: architecture, configuration and local setup

**Status:** Draft for review, 2026-10-03.
**Story:** #460 (Publish the documentation for the 2 Oct 2026 AI Diffusion release).
**Branches:**
- `docs/ai-diffusion-architecture` in ai-diffusion-dpg (this spec, README, ARCHITECTURE, the Signals URL change);
- a matching branch in bluedots-docs (site pages).

## 1. Problem

Someone adopting the AI Diffusion DPG has no current, readable description of it.

- **The Google docs.** "Draft - AI Agentic DPGs" has the vision, principles and blocks, but it predates the code: it still names a "Learning Layer" and has the Knowledge Engine assemble the prompt. The Technical Specification (May 2026) is very long. About 45% of it is appendices. Seven of its eighteen figures show the same turn at different levels, with no starting figure.
- **`ARCHITECTURE.md`.** About half of it is changelog and status. It misses everything merged since September and refers to files that no longer exist.
- **`README.md`.** Stale: the block statuses, a quick-start step for a CLI service that no longer exists, and KKB references.
- **`docs/local-setup.md`** (#417) exists only on `main`. It doesn't match `deploy/voicera-vm`:
  - the domain, env file and secrets wiring have changed;
  - it doesn't pin memgraph;
  - it doesn't mention `TOOL_RESULT_KEY_SECRET`, the bridge, or the monitoring profile.
- **The bluedots-docs site** doesn't mention AI Diffusion at all.

## 2. Goals and non-goals

**Goals**
- **G1.** An adopting engineer can learn, from the docs site alone:
  - what the DPG is;
  - what each of its seven blocks does;
  - how one caller turn flows;
  - what they configure, versus what the framework owns.
- **G2.** The same engineer can run the Blue Dots agent locally against the site's local Signals stack, and complete a conversation that ends in a real application, by following one guide exactly.
- **G3.** The repo docs tell a contributor where things are in the code, and point to the site for the architecture. The architecture is written in one place only.
- **G4.** Every statement matches `deploy/voicera-vm` at the time of writing. Unbuilt work is labelled as planned.

**Non-goals**
- API reference pages (endpoints, payloads, the data model). These can be generated later.
- Rewriting or replacing the Google docs. They stay as historical design material.
- Cloud or Helm deployment guides.
- Changing `docs/local-setup.md` on `main`. A later PR replaces it with a link to the site guide, once that is published.

## 3. Audience and voice

The reader is an engineer at an organisation adopting the DPG for its own use case. They know Docker, HTTP and YAML. They don't know our history, our internal labels (Spec A–E, D1–D3, M0–M3, finding codes) or our people.

- Write in plain, present-tense English.
- Give every design decision a one-sentence *why*.
- Use Blue Dots, the voice job assistant, as the only worked example.

## 4. Sources and precedence

1. The code on `deploy/voicera-vm`.
2. The merged specs in `docs/superpowers/specs/`.
3. The Technical Specification.
4. "Draft - AI Agentic DPGs".

Where the sources disagree, the higher one wins. The code's names are used throughout: Observability Layer, `knowledge_retrieval` as a tool, one FastAPI service per block, and layered per-block YAML.

## 5. Deliverables

### 5.1 Site page: AI Diffusion DPG architecture

- **Path:** `bluedots-docs/src/content/docs/core-concepts/architecture/ai-diffusion-dpg.md`.
- **Sidebar:** Core Concepts → Architecture, after the Aggregator page.
- **Length:** about 200 lines.

**Sections**
1. **What it is.** One paragraph: seven fixed services plus per-use-case configuration, with Blue Dots as the reference use case.
2. **The seven blocks.** A table of block, port and one-line responsibility:

   | Block | Port | Responsibility |
   |---|---|---|
   | Agent Core | 8000 | Runs each turn. It is the only caller of the LLM and of the other blocks. |
   | Knowledge Engine | 8001 | Retrieval over use-case documents and glossaries, called as a tool. |
   | Memory Layer | 8002 | Session state, the profile graph, saved tool results and the audit trail. |
   | Trust Layer | 8003 | Input and output checks, consent, constraints and human handoff. It fails closed. |
   | Observability Layer | 8004 | Traces, metrics, and turn and outcome events, sent asynchronously. |
   | Reach Layer | 8005–8008 | The channels: web, voice (telephony), MCP and the VoicERA bridge. |
   | Action Gateway | 9999 | Calls external systems through declared tools. |

   Below the table, dev-kit (8080) is described as a configuration tool, not a runtime block.
3. **Diagram 1: who calls whom.** Channels call Agent Core. Agent Core calls Trust, Memory, Knowledge, Action Gateway, Observability and the LLM provider. Action Gateway calls external APIs. The stated rule: no block calls another block directly.
4. **Diagram 2: one turn.** A Blue Dots caller asks for electrician work in Lucknow. The diagram goes through ten steps:
   1. load the session;
   2. Trust checks the input;
   3. dialogue-act understanding pulls out the slots (trade and city);
   4. routing picks `job_match`;
   5. the job search is dispatched early;
   6. the prompt is assembled: identity, `<state>`, `<recent>`, constraints and tool results;
   7. the LLM call runs, with a tool loop;
   8. the output guard runs;
   9. Trust checks the output, and the reply streams to the caller;
   10. Memory, the audit trail and telemetry are written after the reply.

   Each step has one line saying why it is there.
5. **Design principles.** Each principle is a short paragraph that leads with its why:
   - configure, don't code;
   - the LLM writes the words and code makes the decisions (routing, consent, termination, handoff, spoken numbers);
   - Trust is a fail-closed boundary, and tools are withheld until consent;
   - Agent Core is stateless, with all state in Memory;
   - facts come from saved tool results, not from the model;
   - the design meets a voice-latency budget: streamed sentences, at most two LLM calls, early tool dispatch and post-reply writes.
6. **Channels.** One paragraph each for web, voice, MCP and the bridge, plus caller identity by phone number.
7. **Built today vs planned.** A dated list with links to issues.
   - Planned: WhatsApp, outbound campaigns, live tuning, more LLM providers and a shared consent service.
   - Built: the human handoff, which ships off by default.
8. **Read next.** Links to the configuration page, the setup guide and the repo's contributor map.

### 5.2 Site page: Configuring a use case

- **Path:** `core-concepts/architecture/ai-diffusion-configuration.md`.
- **Length:** about 150 lines.

**Sections**
1. **Two layers.** `dpg/<block>.yaml` holds the framework defaults and is deep-merged with `configs/<domain>/<block>.yaml`. The merged config is validated at startup, and an unknown key stops the service. **Diagram 3** shows the two files for each block, the merge, the schema check and the running block.
2. **What you write, block by block.**
   - Agent Core: phases, routing rules, dialogue acts and intents, identity, handoff and the output contract.
   - Action Gateway: tools, with auth taken from env and result shaping.
   - Trust Layer: topics, escalation and consent text.
   - Reach Layer: channels.
   - Knowledge Engine: documents.
   - Memory Layer: session fields.
3. **One phase, end to end.** A trimmed Blue Dots `job_match` excerpt (prompt, tools, pending question and two routing rules), traced against the turn in Diagram 2.
4. **What the framework owns.** The pipeline, the guards (per-turn tool caps, grounding, consent), termination and streaming.
5. **The dev-kit.** It interviews you, writes the YAML, and validates it against the runtime schemas.
6. **Common mistakes.** One example: a config key that is newer than the image schema stops the service at startup.

### 5.3 Site guide: AI Diffusion local setup

- **Path:** `guides/installation/local-setup/ai-diffusion-dpg.md`.
- **Sidebar:** Guides → Installation → Local Setup, last. The guide pages' "Path N of M" prev/next labels are renumbered.

**Steps**
1. What you'll have at the end.
2. Prerequisites: the site's *Local Stack (Docker)* page done with `--profile search`, Docker with Compose, and an OpenAI key. Memory needs are measured during verification.
3. Get the code: `deploy/voicera-vm`, with a known-good image tag.
4. Connect to local Signals:
   - create the agent's org and API key in local Signals, with documented API calls or a script;
   - set the Signals URL env vars (§6).
5. Configure `.env`:
   - **Required:** `OPENAI_API_KEY`, `BLUE_DOTS_API_KEY`, `BLUE_DOTS_ORG_ID`, `BLUE_DOTS_SEARCH_API_KEY` and `TOOL_RESULT_KEY_SECRET`. Without `TOOL_RESULT_KEY_SECRET`, saved tool results are silently disabled.
   - **Optional:** a table covering `HITL_*`, Discord, and the voice and telephony keys.
   - **Port clashes with the Signals stack:** 8080 is used by both dev-kit and Keycloak, and 3100 by both Loki and search. Both are resolved with a compose override in the guide.
6. Start: one `docker compose up -d` with the core service list, then wait for health.
7. Verify:
   - every `/health` endpoint returns 200;
   - a web chat turn on :8005;
   - a scripted bridge conversation with curl (greeting, consent, age, trade and city, pick, apply), ending with a Signals query that shows the application exists.
8. Optional: run voice-bench against the stack.
9. Common problems: a symptom → cause → fix table.
10. Stop, reset and clean up.

### 5.4 Repo: README.md (about 80 lines)

1. What the DPG is, in three lines.
2. The block and port table, the same as §5.1.
3. A five-command quick start, linking to the site guide.
4. A repo map: each top-level directory, one line each.
5. Where the docs live: the site, `docs/superpowers/specs/` and `agent_core/eval/`.
6. Contributing:
   - the branch flow;
   - the rule that a runtime schema change also updates the dev-kit mirror;
   - the tests for each block;
   - the licence.

Remove the stale status table, the CLI step and the KKB references.

### 5.5 Repo: ARCHITECTURE.md (about 120 lines, a contributor map)

1. A link to the site page for the full architecture.
2. A table of block → directory → entry file → config schema file.
3. The turn pipeline as `[STEP n]` log marker → function → file.
4. Module rules:
   - only Agent Core calls other blocks;
   - base classes;
   - Trust fails closed;
   - the runtime↔dev-kit sync rule.
5. How config is loaded.
6. Where tests and evals live.
7. A checklist for adding a block feature.

Delete the existing sections on implementation status, design changes from the original spec, and stub replacement. Every path must be checked against the tree.

### 5.6 Repo: automation/docker/README.md

Correct the lines that contradict the guide:
- images come from GHCR, not Docker Hub;
- the default key is OpenAI for Blue Dots;
- there is no CLI profile;
- the stated resource limits are wrong.

## 6. Code change: env-overridable Signals URLs

Pointing the agent at another Signals today means hand-editing four values in `dev-kit/configs/blue-dots/action_gateway.yaml`: three `base_url`s and `apply_job`'s static `instance_url`. voice-bench does this with a patch file (`agent_core/eval/voice_bench/patches/blue-dots-local.yaml`).

**Change**
- Action Gateway's config loader gains the same `${VAR}` / `${VAR:-default}` expansion that Reach Layer already uses (`reach_layer/base/config_loader.py` `_expand_env_vars`).
- The Blue Dots config uses it:
  - `base_url: "${SIGNALS_BASE_URL:-https://signals.bluedotseconomy.org}"`;
  - the search URL uses `${SIGNALS_SEARCH_URL:-…}`;
  - `instance_url` uses `${SIGNALS_INSTANCE_URL:-…}`.
- The defaults equal today's values, so nothing changes for existing deployments.

**Tests and sync**
- Loader unit tests: a var that is set, an unset var with a default, and an unset var with no default (the value is left as written, matching Reach Layer).
- A Blue Dots config test that every Signals URL uses one of the three vars.
- The dev-kit sync rule is checked. If dev-kit validates `base_url` as a URL, its mirror must accept the `${…}` form.
- voice-bench switches from its patch file to the env vars where that is simple; otherwise it is left as it is and noted.

**Deploy docs:** the three vars are added to `automation/deploy/shared-vm/env.example` with their defaults. The VM runbook's (#426) "switching Signals cluster" section becomes a change of env vars instead of five edits.

## 7. Diagrams

There are exactly three diagrams, each at one level of abstraction and using the block names from §5.1:
1. Who calls whom (§5.1).
2. One turn (§5.1).
3. The config layers (§5.2).

They are written in Mermaid, embedded as `<pre class="mermaid">` blocks, because the site renders Mermaid client-side and not from fenced code. The same Mermaid source is used in the repo docs as fenced blocks, which GitHub renders. No images, so a code change can't leave a stale picture.

## 8. Verification

- **Facts.**
  - Every port, path, endpoint, env var and step name is checked against `deploy/voicera-vm`.
  - Every file path in ARCHITECTURE.md is checked with a script that resolves each path.
- **The guide.** It is run from a fresh clone, on top of a fresh site *Local Stack* with search, exactly as written. The run must reach a real application in Signals, and the commands' outputs are recorded. Memory figures come from `docker stats` during that run.
- **The site.**
  - `pnpm build` passes in bluedots-docs.
  - The three diagrams render in `pnpm dev`, checked in a browser.
  - The sidebar and the prev/next links are correct.
- **The repo.**
  - Action Gateway tests pass.
  - The full test suites of the affected blocks show only their known failures.
  - Rendered on GitHub, README and ARCHITECTURE show their Mermaid diagrams.

## 9. Delivery

- **PR 1** (ai-diffusion-dpg into `deploy/voicera-vm`): the §6 code change, README, ARCHITECTURE, the docker README and this spec. It merges first, so the guide's env vars exist.
- **PR 2** (bluedots-docs): the three pages, the sidebar entries and the renumbered guide links. It merges after PR 1.
- **Tracking:** child tasks under story #460, one per PR, with story points from the user.

## 10. Risks

- **The docs drift from the code again.** Mitigation: the repo doc is a map of file pointers, checked by a script; the site holds the stable concepts; status lives in issues.
- **The local guide breaks when the image tag or compose file changes.** Mitigation: pin a tag in the guide, and keep a "verified on" date and commit at the top.
- **Memory.** Running the Signals stack with search plus the full AI Diffusion stack may need more memory than a laptop has. Mitigation: the guide lists a minimal service set (no monitoring profile, no voice) and a measured figure.
