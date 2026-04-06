# AI Composition Framework Architecture Specification

## 1. Project Overview
The AI Composition Framework is a system designed to build AI-powered voice and chat solutions across various domains. It solves the challenge of building complex AI systems from scratch by providing a modular architecture that separates the core runtime behavior from domain-specific intelligence. Deployers can assemble a system using standardized building blocks and configure it for specific use cases using a configuration toolkit.

## 2. Architecture Philosophy
The framework is built on several key architectural pillars:
*   **Composition Framework:** A system architecture composed of 7 standardized Digital Public Goods (DPG) building blocks.
*   **Reusable Building Blocks:** The core logic of the 7 DPGs remains constant across different deployments.
*   **Domain-Specific Configuration:** All domain-specific elements—such as knowledge, safety policies, and persona—are defined externally through a configuration kit.
*   **Separation of Concerns:** A strict boundary between the runtime engine (the 7 DPGs) and the deployment configuration (Domain Configuration Kit).

## 3. System Architecture
The framework conceptually organizes the 7 DPG building blocks into functional areas:
*   **Intelligence & Integration:** Knowledge Engine and Action Gateway.
*   **Orchestration & Trust:** Agent Core and Trust Layer.
*   **State & Memory:** Memory Layer.
*   **Channels & Reach:** Reach Layer.
*   **Learning & Observability:** Observability Layer.

The **Domain Configuration Kit** acts as the blueprint, configuring all 7 DPGs to serve a specific domain. The **Agent Core** serves as the central orchestrator, managing the flow of information between these blocks to process user interactions.

## 4. The 7 DPG Building Blocks

### Knowledge Engine
**Role:** Assembles the complete prompt for each LLM call. Receives pre-computed NLU results from Agent Core — does not run Language Normalisation or NLU itself.
*   **Internal Components:**
    *   **Glossary & Domain Vocabulary:** Maps entity values to canonical domain concepts using pre-computed NLU entities.
    *   **Static Knowledge Base:** Performs semantic RAG retrieval using intent and entities from Agent Core.
    *   **Multimodal Input Handler:** Processes non-text inputs such as images or PDFs.
*   **Interactions:** Receives normalised input, NLU results, and session history (all passed by Agent Core in the request body) to construct the prompt. Does **not** call Memory Layer directly — Agent Core fetches session state and forwards it.

### Memory Layer
**Role:** Manages all system state and context across different temporal scopes.
*   **State Scopes:**
    *   **Turn:** Short-lived state for the current processing cycle (raw input, intermediate results).
    *   **Session:** State for a single conversation (turn history, confirmed entities, workflow steps).
    *   **Persistent:** Long-term memory (user profile, journey history, outcomes).
*   **Internal Components:** Session Memory Handler, User Profile Store, Task State Manager, and Broadcast/Incident State (for system-wide shared state).
*   **Interactions:** Provides session state to the Agent Core on read; receives state updates from the Agent Core asynchronously after each turn. Does not interact with Knowledge Engine directly.

### Trust Layer
**Role:** Mandatory safety and compliance gate that enforcers policies and escalation rules for every input and output.
*   **Rule Categories:**
    *   **Content Rules:** Blocks harmful inputs and enforces topic boundaries (PII detection, abuse filtering).
    *   **Output Rules:** Verifies LLM responses before delivery (enforcing citations, blocking restricted language).
    *   **Consent Rules:** Manages user consent flows and compliance (e.g., DPDP Act).
    *   **Escalation Rules:** Detects when the AI must hand off to a human agent.
    *   **Topic Firewall:** Routes specific sensitive topics to human agents regardless of AI confidence.
*   **Internal Components:** Guardrails Engine, Consent & Compliance Handler, HITL (Human-in-the-loop) Handler, Priority & Escalation Classifier.

### Agent Core (Orchestrator)
**Role:** The central intelligence and orchestration layer. It coordinates the execution sequence of all other DPGs, runs Language Normalisation and NLU, and executes the LLM calls.
*   **Internal Components:**
    *   **LLM Inferencing Wrapper:** Standard interface over LLM providers handling token management, retries, and fallback models.
    *   **Language Normaliser:** Handles dialect detection, code-switching, and transliteration before the KE call.
    *   **NLU Processor:** Handles intent classification, entity extraction, and sentiment analysis before the KE call.
    *   **Manager Agent:** Rules-first routing component that manages intent-based flows and the tool-use loop.
    *   **Orchestration Config Layer:** Wires the entire agent's runtime behavior based on the configuration file.
*   **Interactions:** Coordinates Knowledge Engine, Trust Layer, Action Gateway, and Memory Layer for every turn. Passes NLU results and session state to Knowledge Engine in the request body.

### Action Gateway
**Role:** The framework's interface with the external world. It executes interactions with external systems via tool calls.
*   **Interactions:** Receives intent from the LLM (expressed as tool calls) and returns normalized results to the Agent Core to be fed back into the conversation.

### Reach Layer
**Role:** Manages inbound and outbound communication channels and ensures context continuity across channel moves.
*   **Internal Components:** Campaign Orchestrator (for outbound tasks), Channel Adapter (normalizing across VOIP, WhatsApp, Web, etc.), Handoff Manager (for cross-channel transitions).
*   **Interactions:** Receives user messages, normalizes them for the Agent Core, and delivers the processed responses to the user.

### Observability Layer
**Role:** Asynchronous logging and observability layer that measures quality and feeds the improvement loop.
*   **Output Streams:**
    *   **Audit Log:** Timstamped records of every prompt, response, and routing decision.
    *   **Quality Evaluation:** Scores for groundedness, relevance, and task completion.
    *   **Signal Collection:** Captures explicit and implicit feedback (e.g., repeated questions).
*   **Internal Components:** LLM Observability, Evals Framework, Feedback Signal Collector, Outcome Tracker.

## 5. Runtime Flow
The standard processing flow for a single turn is as follows:
1.  **Input arrives:** Received via the Reach Layer.
2.  **Read state:** Agent Core retrieves context from the Memory Layer.
3.  **Safety check (input):** Mandatory validation via Trust Layer content rules.
3b. **Language Normalisation:** Agent Core normalises dialect, code-switching, and transliteration.
3c. **NLU:** Agent Core classifies intent, extracts entities, and detects sentiment. Early exit if intent is unknown or confidence is below threshold.
4.  **Prompt assembly:** Knowledge Engine constructs the prompt using RAG retrieval; receives NLU results and session history from Agent Core in the request body.
5.  **LLM call:** Agent Core executes the primary LLM call.
6.  **Tool execution (if required):** If the LLM triggers a tool call, Action Gateway executes it and results are fed back for a second LLM response.
7.  **Safety check (output):** Mandatory validation of LLM response via Trust Layer output rules.
8.  **Response delivery:** Reach Layer delivers the response to the user's channel.
9.  **State update (async):** Agent Core writes back to the Memory Layer.
10. **Observability events (async):** Turn metadata and metrics are emitted to the Observability Layer.

## 6. Configuration Architecture

The framework uses a **two-level configuration model** that separates deployment-specific intelligence from service-level operational settings.

### Level 1 — Domain Configuration Kit (Deployment-level)
A single YAML file per deployment that configures all 7 DPGs for a specific domain. This is the contract between the deployer and the framework — no source code changes required to deploy to a new domain.

1.  **Knowledge Corpus:** Configures source documents, domain glossaries, and chunking strategy for retrieval.
2.  **Connectors & Data Sources:** Declares external systems with endpoints, authentication, and fallback behaviors.
3.  **Conversation Design:** Defines the agent's persona, behavior rules, and multi-step workflows.
4.  **Trust & Policy:** Specifies domain-specific safety rules, escalation triggers, and topic firewalls.
5.  **Success Criteria & Evaluation Rules:** Defines the metrics used to measure deployment quality and outcomes.

### Level 2 — Per-DPG Service Config (Service-level)
Each DPG maintains its own `config.yaml` for **operational settings that belong to the service itself** — not to any specific deployment. These stay constant across domains but may differ between environments (staging vs production).

| DPG | Service Config Controls |
|---|---|
| **Agent Core** | LLM model selection (primary/fallback), timeout, retry attempts, max tool rounds, fallback messages |
| **Knowledge Engine** | Embedding model, chunk size, retrieval top-k, NLU confidence thresholds |
| **Memory Layer** | TTL for session state, max history length, persistence backend settings |
| **Trust Layer** | Rule evaluation order, ML model thresholds, escalation sensitivity |
| **Action Gateway** | Connector timeout, retry policy, circuit breaker settings |
| **Reach Layer** | Channel adapter settings, handoff timeout, max message length |
| **Observability Layer** | Emit batch size, flush interval, log retention settings |

**Rule:** Domain Configuration Kit overrides service config where there is overlap. Service config provides defaults; the Domain Kit provides deployment-specific values.

## 7. Connector Model
The framework interacts with external systems using a unified connector pattern:
*   **Read Connectors:** Used to query external systems for information (e.g., fetching prices or job listings).
*   **Write Connectors:** Used to take actions in external systems (e.g., submitting applications). These always require explicit user consent via Trust Layer rules.
*   **Identity Connectors:** Used for mid-conversation identity verification (e.g., OTP checks).

The system uses a **Tool Execution Pattern** where the LLM does not touch external systems directly but expresses intent via tool definitions, which are then executed by the Action Gateway.

## 8. System Interaction Model
*   The **Agent Core** is the central orchestrator that triggers all other blocks in a defined sequence.
*   The **Knowledge Engine** receives session history from **Agent Core** (forwarded in the request body) and applies vocabulary from the **Configuration Kit** before the **Agent Core** calls the LLM. Knowledge Engine does not call Memory Layer directly.
*   The **Trust Layer** operates as a mandatory firewall for every I/O pass.
*   The **Memory Layer** persists state across scopes and is updated asynchronously by the **Agent Core**.
*   The **Action Gateway** is isolated from the LLM, acting only on instructions from the **Agent Core**.
*   The **Observability Layer** runs entirely out-of-band (asynchronously) to prevent adding to the response latency.

### LLM Proxy
Agent Core exposes an internal LLM proxy endpoint (`POST /internal/llm/call`). Any DPG service that needs LLM access in the future calls this endpoint instead of holding its own Anthropic API key — Agent Core remains the sole owner of the key and the sole caller of the Anthropic API.

**Current status:** The proxy is implemented but not yet active. No other service currently calls it. It is available for future use (e.g., if the Trust Layer needs ML-based evaluation or the Action Gateway needs natural language parsing).

## 9. Runtime Characteristics
*   **Latency Budget:** Targeted at 800–1200ms per turn (voice-first optimization).
*   **Stateless Agent Core:** The Agent Core instances are stateless; all state persists in the Memory Layer backing stores.
*   **Scaling Model:** Horizontal scaling based on concurrent sessions, with no cross-instance coordination required.
*   **Responsibility:** The Agent Core manages LLM calls, tool execution, and manages the turn within the latency budget, including model fallback.

## 10. Architectural Boundaries
The framework intentionally excludes several components which are considered external or platform-level:
*   **ASR/TTS Pipeline:** The framework processes text-to-text; voice conversion is handled upstream/downstream.
*   **Model Training & Fine-tuning:** Uses foundation models via API.
*   **Infrastructure Provisioning:** The architecture is infrastructure-agnostic.
*   **Multi-tenancy & Cost Attribution:** Handled at the platform engineering layer.

## 11. Design Principles
*   **Composability:** Building complex systems through the assembly of 7 distinct building blocks.
*   **Modularity:** Clean boundaries between functional blocks like Trust, Memory, and Action.
*   **Domain-Driven Configuration:** Externalizing all domain intelligence into the Configuration Kit.
*   **Statelessness:** Decoupling runtime logic from state to enable unlimited horizontal scaling.
*   **Reusable Runtime:** The code for the 7 DPGs remains unchanged across different deployments.


---

## 12. PoC Implementation Scope

For the current Proof of Concept, system components are partitioned into full module implementations and lightweight architectural stubs.

### Full Modules
- **Knowledge Engine:** Implements RAG retrieval (Glossary, Static KB, Multimodal) and prompt assembly. NLU and Language Normalisation run in Agent Core.
- **Agent Core:** Orchestration logic and LLM lifecycle management.
- **Domain Configuration Kit:** YAML-based runtime wiring.

### Lightweight Stubs
The following layers are implemented as stubs that mimic their final interfaces to allow end-to-end execution:

| Layer | Stub Behaviour |
|---|---|
| **Memory Layer** | Simple in-process state store for session data. |
| **Trust Layer** | Basic rule checks (e.g., blocked phrases); no ML evaluation. |
| **Action Gateway** | Mock API server returning synthetic responses for external calls. |
| **Reach Layer** | CLI-based input/output (stdin/stdout). |
| **Observability Layer** | OTel instrumentation; no full observability pipeline. |

**Architectural Continuity Rule:** Stub interfaces must strictly match the expected interface of their real implementations. This allows them to be replaced in future milestones without requiring changes to the Agent Core or other core modules.