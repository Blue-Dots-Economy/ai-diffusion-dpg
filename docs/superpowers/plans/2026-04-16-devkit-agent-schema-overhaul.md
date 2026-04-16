# Dev-Kit Agent Schema Overhaul & Tool Configuration Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update all dev-kit Pydantic schemas and YAML templates to match production configs, add a new Action Gateway tool-configuration phase (REST API via OpenAPI + MCP servers), restructure the Reach Layer to support per-channel selection (web/CLI/voice), and reorder phases so tools are configured before subagents.

**Architecture:** The plan proceeds in strict dependency order: (1) update schemas and templates so validation works, (2) add Action Gateway tools phase with OpenAPI ingestion and MCP discovery, (3) auto-generate agent_core connectors from tool definitions, (4) add channel-aware Reach Layer config, and (5) update all prompts and phase ordering. The dev-kit agent's `connectors` phase is replaced by a `tools` phase that generates both `action_gateway` and `agent_core.connectors` configs.

**Tech Stack:** Python 3.11+, Pydantic v2, PyYAML, anthropic SDK, httpx (for MCP HTTP discovery), jsonschema (OpenAPI parsing). All managed with `uv`.

---

## Gap Summary: What's Wrong Now

| Component | Current state | Production reality |
|---|---|---|
| `action_gateway.yaml` template | `action_gateway.connectors.connector_name → {endpoint, timeout_ms}` | `tools: [{id, type, category, auth, endpoints/mcp_url, ...}]` |
| `schema.py ActionGatewaySettings` | `connectors: dict[str, ConnectorEndpointConfig]` | `tools: list[RestApiToolDef \| McpToolDef]` |
| `reach_layer.yaml` template | Flat `ui: {app_name, ...}` | `reach_layer.channels.{cli, web, voice}` multi-channel |
| `schema.py ReachLayerConfig` | `ui: dict[str, Any]` + `reach_layer.cli` | `reach_layer.channels.{cli, web, voice}` with per-channel models |
| Dev-kit agent phases | `connectors` phase: configures both agent_core and action_gateway together | Action Gateway (tools) should come BEFORE agent_core workflow |
| Dev-kit agent tools phase | No OpenAPI ingestion, no MCP discovery | OpenAPI upload → parse → tool gen; MCP URL → tools/list → select |
| Reach phase | Asks for all UI fields regardless | Must ask which channels first, then configure only those |

---

## File Map

### Modified files
- `dev-kit/dev_kit/schema.py` — Pydantic models for ActionGateway and ReachLayer
- `dev-kit/dev_kit/schemas/action_gateway.yaml` — Template for key validation + LLM prompt
- `dev-kit/dev_kit/schemas/reach_layer.yaml` — Template for key validation + LLM prompt
- `dev-kit/dev_kit/agent/accumulator.py` — PHASES list, new tool accumulation methods
- `dev-kit/dev_kit/agent/tools.py` — New tool definitions + handlers
- `dev-kit/dev_kit/agent/prompts/phases.py` — Rewritten `tools` phase, updated `reach` phase, updated `workflow` phase
- `dev-kit/dev_kit/agent/conversation.py` — Minor: pass `available_tools` to system prompt builder
- `dev-kit/dev_kit/agent/prompts/base.py` — Pass `available_tools` into `build_system_prompt`

### New files
- `dev-kit/dev_kit/agent/openapi_parser.py` — Parse OpenAPI 3.0/3.1 specs, extract tool definitions
- `dev-kit/tests/test_openapi_parser.py` — Tests for OpenAPI parser
- `dev-kit/tests/test_schema_action_gateway.py` — Tests for updated AG schema
- `dev-kit/tests/test_schema_reach_layer.py` — Tests for updated Reach schema
- `dev-kit/tests/test_tools_phase.py` — Tests for new tool-phase handlers

---

## Task 1: Update Action Gateway Pydantic Schema

**Files:**
- Modify: `dev-kit/dev_kit/schema.py`

### Background
The current `ActionGatewaySettings` has `connectors: dict[str, ConnectorEndpointConfig]`. The production KKB config uses a `tools` list where each tool can be `rest_api` (with base_url, auth, endpoints, params) or `mcp` (with mcp_server_url, tool discovery). The config top-level keys are `tools` and `observability` — there is NO `action_gateway:` wrapper in the domain config file.

- [ ] **Step 1: Write a failing test**

Create `dev-kit/tests/test_schema_action_gateway.py`:

```python
"""Tests for updated ActionGateway schema."""
import pytest
from pydantic import ValidationError
from dev_kit.schema import ActionGatewayConfig, validate_partial


def test_rest_api_tool_validates():
    """A valid REST API tool definition should parse without errors."""
    data = {
        "tools": [
            {
                "id": "onest_market_lookup",
                "type": "rest_api",
                "category": "read",
                "description": "Search ONEST job listings",
                "base_url": "https://api.example.com",
                "auth": {"type": "api_key", "header": "X-API-KEY", "secret_env": "MY_KEY"},
                "timeout_ms": 5000,
                "endpoints": [
                    {
                        "name": "search",
                        "method": "POST",
                        "path": "/search",
                        "params": [
                            {"name": "query", "source": "agent", "type": "string", "required": True, "description": "search query"},
                            {"name": "limit", "source": "static", "type": "integer", "value": 10},
                        ],
                    }
                ],
                "response": {"max_size_chars": 4000},
            }
        ]
    }
    config = ActionGatewayConfig.model_validate(data)
    assert config.tools[0].id == "onest_market_lookup"
    assert config.tools[0].endpoints[0].params[0].source == "agent"


def test_mcp_tool_validates():
    """A valid MCP tool definition should parse without errors."""
    data = {
        "tools": [
            {
                "id": "obsrv_query",
                "type": "mcp",
                "category": "read",
                "description": "Query Obsrv data",
                "mcp_server_url": "https://mcp.example.com",
                "tool_name": "query_dataset",
                "input_schema": {"type": "object", "properties": {"dataset": {"type": "string"}}},
            }
        ]
    }
    config = ActionGatewayConfig.model_validate(data)
    assert config.tools[0].type == "mcp"


def test_auth_none_validates():
    """Auth type 'none' requires no extra fields."""
    data = {
        "tools": [
            {
                "id": "webhook",
                "type": "rest_api",
                "category": "write",
                "description": "Post to webhook",
                "base_url": "https://webhook.site/abc",
                "auth": {"type": "none"},
                "endpoints": [{"name": "post", "method": "POST", "path": "/"}],
            }
        ]
    }
    config = ActionGatewayConfig.model_validate(data)
    assert config.tools[0].auth.type == "none"


def test_validate_partial_rest_api_tool():
    """validate_partial should accept valid action_gateway partial data."""
    data = {
        "tools": [
            {
                "id": "test_tool",
                "type": "rest_api",
                "category": "read",
                "description": "test",
                "base_url": "https://api.example.com",
                "auth": {"type": "none"},
                "endpoints": [{"name": "get", "method": "GET", "path": "/data"}],
            }
        ]
    }
    errors = validate_partial("action_gateway", data)
    assert errors == [], f"Unexpected errors: {errors}"


def test_invalid_category_rejected():
    """Invalid category value should fail validation."""
    data = {
        "tools": [
            {
                "id": "bad",
                "type": "rest_api",
                "category": "invalid_category",
                "description": "x",
                "base_url": "https://api.example.com",
                "auth": {"type": "none"},
                "endpoints": [],
            }
        ]
    }
    with pytest.raises(ValidationError):
        ActionGatewayConfig.model_validate(data)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd dev-kit && uv run pytest tests/test_schema_action_gateway.py -v
```
Expected: FAIL — `ActionGatewayConfig` still uses old `connectors` dict shape.

- [ ] **Step 3: Add new Pydantic models and update ActionGatewayConfig in schema.py**

Replace the Action Gateway section of `dev-kit/dev_kit/schema.py` (lines 711–731) with:

```python
# ---------------------------------------------------------------------------
# Action Gateway
# ---------------------------------------------------------------------------

class ToolParamDef(BaseModel):
    """Definition of a single parameter for a REST API tool endpoint."""

    name: str = Field(..., description="Parameter name")
    source: Literal["agent", "static"] = Field(
        ..., description="'agent' = LLM fills this at call time; 'static' = fixed value"
    )
    type: str = Field(default="string", description="JSON type: string | integer | boolean | array")
    required: bool = Field(default=False, description="Whether the agent must provide this param")
    description: str = Field(default="", description="Description shown to the agent")
    value: Any = Field(default=None, description="Fixed value when source is 'static'")
    default: Any = Field(default=None, description="Default value when source is 'agent' and param is optional")


class ToolEndpointDef(BaseModel):
    """One HTTP endpoint within a REST API tool definition."""

    name: str = Field(..., description="Endpoint name, e.g. 'search', 'apply'")
    method: str = Field(..., description="HTTP method: GET | POST | PUT | DELETE | PATCH")
    path: str = Field(..., description="Path appended to base_url, e.g. '/search'")
    params: list[ToolParamDef] = Field(default=[], description="Parameters for this endpoint")


class ToolResponseConfig(BaseModel):
    """Response handling config for a REST API tool."""

    max_size_chars: int = Field(default=4000, description="Truncate response body to this many characters before returning to agent")


class AuthConfig(BaseModel):
    """Authentication configuration for a REST API tool."""

    type: Literal["none", "api_key", "bearer", "oauth2"] = Field(
        ..., description="Auth scheme: none | api_key | bearer | oauth2"
    )
    header: str | None = Field(default=None, description="Header name for api_key auth, e.g. X-API-KEY")
    secret_env: str | None = Field(default=None, description="Environment variable holding the API key or token")
    token_url: str | None = Field(default=None, description="Token endpoint URL for oauth2")


class RestApiToolDef(BaseModel):
    """Full definition of a REST API tool executed by the Action Gateway."""

    id: str = Field(..., description="Unique tool identifier — must match name in agent_core connectors")
    type: Literal["rest_api"] = Field(default="rest_api")
    category: Literal["read", "write", "identity"] = Field(
        ..., description="Tool category: read (no consent), write/identity (Trust Layer consent required)"
    )
    description: str = Field(..., description="What this tool does — shown to LLM for routing decisions")
    base_url: str = Field(..., description="Base URL of the API, e.g. https://api.example.com/v1")
    auth: AuthConfig = Field(..., description="Authentication scheme for this API")
    timeout_ms: int = Field(default=5000, description="Request timeout in milliseconds")
    endpoints: list[ToolEndpointDef] = Field(default=[], description="One or more endpoint definitions")
    response: ToolResponseConfig = Field(default_factory=ToolResponseConfig, description="Response handling config")


class McpToolDef(BaseModel):
    """Full definition of an MCP server tool executed by the Action Gateway."""

    id: str = Field(..., description="Unique tool identifier — must match name in agent_core connectors")
    type: Literal["mcp"] = Field(default="mcp")
    category: Literal["read", "write", "identity"] = Field(..., description="Tool category")
    description: str = Field(..., description="What this tool does — shown to LLM")
    mcp_server_url: str = Field(..., description="Base URL of the MCP server")
    tool_name: str = Field(..., description="Tool name as returned by tools/list on the MCP server")
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for the tool input, as returned by MCP tools/list"
    )
    timeout_ms: int = Field(default=5000, description="Request timeout in milliseconds")


class ActionGatewayConfig(BaseModel):
    """Top-level config for the Action Gateway domain config file."""

    tools: list[RestApiToolDef | McpToolDef] = Field(
        default=[],
        description="List of tool definitions. Each entry is either a rest_api or mcp tool."
    )
    observability: dict[str, Any] = Field(
        default_factory=dict,
        description="Observability settings. At minimum: {domain: 'your_domain_slug'}"
    )
```

Also update `_BLOCK_MODEL_MAP` at line 767 to use the new `ActionGatewayConfig` (no change needed to the key, just the model is updated in place).

- [ ] **Step 4: Run the tests**

```bash
cd dev-kit && uv run pytest tests/test_schema_action_gateway.py -v
```
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd dev-kit && git add dev_kit/schema.py tests/test_schema_action_gateway.py
git commit -m "feat(schema): update ActionGatewayConfig to production tools-list model"
```

---

## Task 2: Update Reach Layer Pydantic Schema

**Files:**
- Modify: `dev-kit/dev_kit/schema.py`

### Background
The production KKB reach layer config uses `reach_layer.channels.{cli, web, voice}` with per-channel sub-configs. Web has `auth` + `ui` sub-keys. Voice has `raya` (STT/TTS settings) + `agent_core` (timeout + greeting). The old `ui: dict[str, Any]` flat structure and `ReachLayerSettings.cli: CLIConfig` are removed.

- [ ] **Step 1: Write a failing test**

Create `dev-kit/tests/test_schema_reach_layer.py`:

```python
"""Tests for updated ReachLayer schema."""
import pytest
from pydantic import ValidationError
from dev_kit.schema import ReachLayerConfig, validate_partial


def test_web_channel_config_validates():
    """A valid web channel config should parse without errors."""
    data = {
        "reach_layer": {
            "common": {"observability": {"domain": "kkb"}},
            "channels": {
                "web": {
                    "auth": {"enabled": False, "google_client_id": "", "cookie_secure": False},
                    "ui": {
                        "app_name": "Kaam Ki Baat",
                        "app_tagline": "DPG Skill-Jobs AI",
                        "app_icon": "💼",
                    },
                }
            },
        }
    }
    config = ReachLayerConfig.model_validate(data)
    assert config.reach_layer.channels.web.ui["app_name"] == "Kaam Ki Baat"
    assert config.reach_layer.channels.web.auth.enabled is False


def test_cli_channel_config_validates():
    """A valid CLI channel config should parse without errors."""
    data = {
        "reach_layer": {
            "channels": {
                "cli": {"prompt": "You: ", "agent_prefix": "Agent: "}
            }
        }
    }
    config = ReachLayerConfig.model_validate(data)
    assert config.reach_layer.channels.cli.prompt == "You: "


def test_voice_channel_config_validates():
    """A valid voice channel config should parse without errors."""
    data = {
        "reach_layer": {
            "channels": {
                "voice": {
                    "raya": {"stt_language": "hi", "tts_language": "hi", "voice_id": "abc-123"},
                    "agent_core": {
                        "timeout_ms": 15000,
                        "greeting": "Namaste!",
                        "fallback_phrase": "Please repeat.",
                    },
                }
            }
        }
    }
    config = ReachLayerConfig.model_validate(data)
    assert config.reach_layer.channels.voice.raya.stt_language == "hi"


def test_multiple_channels_coexist():
    """Web and CLI channels can both be configured simultaneously."""
    data = {
        "reach_layer": {
            "channels": {
                "cli": {"prompt": "You: ", "agent_prefix": "Agent: "},
                "web": {"auth": {"enabled": False}, "ui": {"app_name": "Test App"}},
            }
        }
    }
    config = ReachLayerConfig.model_validate(data)
    assert config.reach_layer.channels.cli is not None
    assert config.reach_layer.channels.web is not None


def test_validate_partial_channels():
    """validate_partial should accept valid reach_layer partial."""
    data = {
        "reach_layer": {
            "channels": {
                "web": {"ui": {"app_name": "My App"}}
            }
        }
    }
    errors = validate_partial("reach_layer", data)
    assert errors == [], f"Unexpected errors: {errors}"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd dev-kit && uv run pytest tests/test_schema_reach_layer.py -v
```
Expected: FAIL — `ReachLayerConfig` still uses old `ui: dict` flat structure.

- [ ] **Step 3: Replace Reach Layer models in schema.py**

Replace the entire Reach Layer section of `dev-kit/dev_kit/schema.py` (lines 733–760) with:

```python
# ---------------------------------------------------------------------------
# Reach Layer
# ---------------------------------------------------------------------------

class CLIChannelConfig(BaseModel):
    """Configuration for the CLI (stdin/stdout) channel adapter."""

    prompt: str = Field(default="You: ", description="Prompt prefix shown before user input")
    agent_prefix: str = Field(default="Agent: ", description="Prefix shown before agent replies")


class WebAuthConfig(BaseModel):
    """Authentication settings for the web channel."""

    enabled: bool = Field(default=False, description="Whether Google OAuth is enabled")
    google_client_id: str = Field(default="", description="Google OAuth2 client ID")
    cookie_secure: bool = Field(
        default=True,
        description="Set Secure flag on session cookie. False for local http:// dev.",
    )


class WebChannelConfig(BaseModel):
    """Configuration for the web channel adapter (React frontend)."""

    auth: WebAuthConfig = Field(default_factory=WebAuthConfig)
    ui: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Web UI branding and copy. Common keys: app_name, app_tagline, app_icon, "
            "agent_avatar, user_avatar, setup_heading, setup_subtitle, user_id_placeholder, "
            "user_id_hint, start_btn_label, new_session_msg, returning_user_msg, "
            "storage_key, theme_storage_key, sign_out_confirm, switch_user_confirm, "
            "delete_conversation_confirm"
        ),
    )


class RayaSTTTTSConfig(BaseModel):
    """Raya STT/TTS configuration for the voice channel."""

    stt_language: str = Field(..., description="BCP-47 language code for speech-to-text, e.g. 'hi', 'en'")
    tts_language: str = Field(..., description="BCP-47 language code for text-to-speech")
    voice_id: str = Field(..., description="Voice ID for the TTS provider")


class VoiceAgentCoreConfig(BaseModel):
    """Agent Core connection settings for the voice channel."""

    timeout_ms: int = Field(default=15000, description="Agent Core call timeout in milliseconds")
    greeting: str = Field(default="", description="First message spoken to the user when voice session starts")
    fallback_phrase: str = Field(default="", description="Phrase spoken when STT fails or input is unintelligible")


class VoiceChannelConfig(BaseModel):
    """Configuration for the voice (VOIP/Raya) channel adapter."""

    raya: RayaSTTTTSConfig = Field(..., description="Raya STT/TTS language and voice settings")
    agent_core: VoiceAgentCoreConfig = Field(
        default_factory=VoiceAgentCoreConfig,
        description="Agent Core connection settings for voice",
    )


class ChannelsConfig(BaseModel):
    """Per-channel configuration. Omit channels that are not deployed."""

    cli: CLIChannelConfig | None = Field(default=None, description="CLI channel config. None = not deployed.")
    web: WebChannelConfig | None = Field(default=None, description="Web channel config. None = not deployed.")
    voice: VoiceChannelConfig | None = Field(default=None, description="Voice channel config. None = not deployed.")


class CommonReachConfig(BaseModel):
    """Common settings shared across all channels."""

    observability: dict[str, Any] = Field(
        default_factory=dict,
        description="Observability settings. At minimum: {domain: 'your_domain_slug'}",
    )


class ReachLayerSettings(BaseModel):
    """Top-level reach layer settings wrapping channel configs."""

    common: CommonReachConfig = Field(default_factory=CommonReachConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)


class AgentCoreClientConfig(BaseModel):
    """Agent Core HTTP client settings for the Reach Layer."""

    endpoint: str
    timeout_s: float = 30.0


class ReachLayerConfig(BaseModel):
    """Top-level config for the Reach Layer domain config file."""

    server: ServerConfig
    reach_layer: ReachLayerSettings = Field(default_factory=ReachLayerSettings)
    agent_core_client: AgentCoreClientConfig
```

- [ ] **Step 4: Run the tests**

```bash
cd dev-kit && uv run pytest tests/test_schema_reach_layer.py -v
```
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd dev-kit && git add dev_kit/schema.py tests/test_schema_reach_layer.py
git commit -m "feat(schema): update ReachLayerConfig to multi-channel model"
```

---

## Task 3: Update Schema YAML Templates

**Files:**
- Modify: `dev-kit/dev_kit/schemas/action_gateway.yaml`
- Modify: `dev-kit/dev_kit/schemas/reach_layer.yaml`

The YAML templates are used for two purposes: (1) key validation in `_check_keys_against_template`, (2) injected verbatim into LLM system prompts so Claude knows the exact valid field names.

- [ ] **Step 1: Rewrite action_gateway.yaml template**

Replace the entire content of `dev-kit/dev_kit/schemas/action_gateway.yaml`:

```yaml
# action_gateway domain config — TEMPLATE
# Fill in values only. Do NOT rename, add, or remove any keys.
# Each entry in `tools` is either a rest_api tool or an mcp tool.

tools:
  # --- REST API tool (use for external HTTP APIs) ---
  - id: ""                          # required — unique snake_case identifier, e.g. onest_market_lookup
    type: rest_api                  # rest_api | mcp
    category: read                  # read | write | identity
    description: ""                 # required — what this tool does (shown to LLM for routing)
    base_url: ""                    # required — API base URL, e.g. https://api.example.com/v2
    auth:
      type: none                    # none | api_key | bearer | oauth2
      header: ""                    # header name for api_key, e.g. X-API-KEY (omit if type: none)
      secret_env: ""                # env var holding the secret (omit if type: none)
      token_url: ""                 # token endpoint for oauth2 (omit unless type: oauth2)
    timeout_ms: 5000
    endpoints:
      - name: ""                    # endpoint name, e.g. search
        method: POST                # GET | POST | PUT | DELETE | PATCH
        path: ""                    # path appended to base_url, e.g. /search
        params:
          - name: ""                # parameter name
            source: agent           # agent (LLM fills at call time) | static (fixed value below)
            type: string            # string | integer | boolean | array
            required: true          # true if agent must provide it (only for source: agent)
            description: ""         # shown to LLM when source: agent
            value: null             # fixed value when source: static; null otherwise
            default: null           # default when source: agent and param is optional
    response:
      max_size_chars: 4000          # response body truncated to this before returning to agent

  # --- MCP tool (use for MCP protocol servers) ---
  - id: ""                          # required — unique snake_case identifier
    type: mcp                       # rest_api | mcp
    category: read                  # read | write | identity
    description: ""                 # required — what this tool does (shown to LLM)
    mcp_server_url: ""              # required — MCP server base URL
    tool_name: ""                   # required — tool name as returned by MCP tools/list
    input_schema: {}                # JSON Schema for the tool input (from MCP tools/list)
    timeout_ms: 5000

observability:
  domain: ""                        # required — short domain slug, e.g. kkb, fasal_doctor
```

- [ ] **Step 2: Rewrite reach_layer.yaml template**

Replace the entire content of `dev-kit/dev_kit/schemas/reach_layer.yaml`:

```yaml
# reach_layer domain config — TEMPLATE
# Fill in values only. Do NOT rename, add, or remove any keys.
# Only include the channels you want to deploy (cli, web, voice).
# agent_core_client endpoint and server settings are DPG framework defaults — not needed here.

reach_layer:

  common:
    observability:
      domain: ""                        # required — short domain slug, e.g. kkb, fasal_doctor

  channels:

    # --- CLI channel (terminal stdin/stdout) ---
    cli:
      prompt: "You: "                   # prompt prefix shown before user input
      agent_prefix: "Agent: "           # prefix shown before agent replies

    # --- Web channel (React frontend) ---
    web:
      auth:
        enabled: false                  # true to require Google OAuth sign-in
        google_client_id: ""            # Google OAuth2 client ID (required if enabled: true)
        cookie_secure: false            # true in production (TLS); false for local http://

      ui:
        app_name: ""                    # required — display name in browser tab and chat header
        app_tagline: ""                 # short subtitle under app name
        app_icon: ""                    # emoji representing the app, e.g. 🌾 💊 💼
        agent_avatar: ""                # emoji on agent chat bubbles
        user_avatar: "👤"              # emoji on user chat bubbles
        setup_heading: ""               # heading on the user-ID setup screen (local + English)
        setup_subtitle: ""              # subtitle under heading — explain what to enter
        user_id_placeholder: ""         # hint text inside the user ID field, e.g. e.g. rahul_electrician
        user_id_hint: ""                # secondary hint below the field
        start_btn_label: ""             # start button label (local + English)
        new_session_msg: ""             # first message for brand new users (local + English)
        returning_user_msg: ""          # first message for returning users (local + English)
        storage_key: ""                 # localStorage key for persisting user ID, e.g. kkb_user_id
        theme_storage_key: ""           # localStorage key for theme, e.g. kkb_theme
        sign_out_confirm: ""            # confirmation text for sign-out action
        switch_user_confirm: ""         # confirmation text for switch-user action
        delete_conversation_confirm: "" # confirmation text for delete-conversation action

    # --- Voice channel (Raya STT/TTS + VOIP) ---
    voice:
      raya:
        stt_language: ""                # required — BCP-47 STT language code, e.g. hi, en
        tts_language: ""                # required — BCP-47 TTS language code
        voice_id: ""                    # required — voice ID for the TTS provider
      agent_core:
        timeout_ms: 15000               # Agent Core call timeout in milliseconds
        greeting: ""                    # first spoken message when voice session starts
        fallback_phrase: ""             # phrase spoken when STT fails or input is unintelligible
```

- [ ] **Step 3: Run existing schema validation tests to check no regressions**

```bash
cd dev-kit && uv run pytest tests/ -v -k "schema" 2>/dev/null || uv run pytest tests/ -v
```
Expected: All previously-passing tests still PASS.

- [ ] **Step 4: Verify the template cache is cleared (if running in same process)**

The schema loader uses a module-level cache. In test runs, clear it:

```python
# add to test teardown or conftest.py if not already present
from dev_kit.schemas import loader as _loader
def clear_template_cache():
    _loader._template_text_cache.clear()
    _loader._template_dict_cache.clear()
```

- [ ] **Step 5: Commit**

```bash
cd dev-kit && git add dev_kit/schemas/action_gateway.yaml dev_kit/schemas/reach_layer.yaml
git commit -m "feat(templates): rewrite action_gateway and reach_layer YAML templates for production schema"
```

---

## Task 4: OpenAPI Spec Parser

**Files:**
- Create: `dev-kit/dev_kit/agent/openapi_parser.py`
- Create: `dev-kit/tests/test_openapi_parser.py`

### Background
When the user provides an OpenAPI 3.0/3.1 spec (as a URL or pasted JSON/YAML), the agent needs to extract tool-building data: endpoints, HTTP methods, parameter names and types, auth schemes. The output is a list of candidate tool definitions that can become `RestApiToolDef` entries in the action_gateway config. The agent then presents these to the user to name and filter.

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/test_openapi_parser.py`:

```python
"""Tests for OpenAPI spec parser."""
import pytest
from dev_kit.agent.openapi_parser import parse_openapi_spec, ParsedTool, ParsedParam


MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0"},
    "servers": [{"url": "https://api.example.com/v1"}],
    "paths": {
        "/search": {
            "post": {
                "operationId": "searchJobs",
                "summary": "Search for jobs",
                "description": "Search job listings by query",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["query"],
                                "properties": {
                                    "query": {"type": "string", "description": "Search term"},
                                    "limit": {"type": "integer", "default": 10},
                                },
                            }
                        }
                    },
                },
            }
        },
        "/apply/{job_id}": {
            "post": {
                "summary": "Apply to a job",
                "parameters": [
                    {
                        "name": "job_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Job identifier",
                    }
                ],
            }
        },
    },
}


def test_parse_base_url():
    """Parser extracts the first server URL as base_url."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    assert tools[0].base_url == "https://api.example.com/v1"


def test_parse_endpoints():
    """Parser returns one ParsedTool per path+method combination."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    paths = [(t.path, t.method) for t in tools]
    assert ("/search", "POST") in paths
    assert ("/apply/{job_id}", "POST") in paths


def test_parse_request_body_params():
    """Request body schema properties become ParsedParam entries with source='agent'."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    search_tool = next(t for t in tools if t.path == "/search")
    param_names = [p.name for p in search_tool.params]
    assert "query" in param_names
    assert "limit" in param_names
    query_param = next(p for p in search_tool.params if p.name == "query")
    assert query_param.required is True
    assert query_param.source == "agent"


def test_parse_path_params():
    """Path parameters become ParsedParam entries."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    apply_tool = next(t for t in tools if t.path == "/apply/{job_id}")
    assert any(p.name == "job_id" for p in apply_tool.params)


def test_parse_description_from_summary():
    """Tool description uses operationId summary or path+method fallback."""
    tools = parse_openapi_spec(MINIMAL_SPEC)
    search_tool = next(t for t in tools if t.path == "/search")
    assert "Search" in search_tool.description or "search" in search_tool.description.lower()


def test_parse_api_key_auth():
    """API key auth scheme is detected and mapped correctly."""
    spec = {
        **MINIMAL_SPEC,
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-KEY",
                }
            }
        },
        "security": [{"ApiKeyAuth": []}],
    }
    tools = parse_openapi_spec(spec)
    assert tools[0].auth_type == "api_key"
    assert tools[0].auth_header == "X-API-KEY"


def test_empty_paths_returns_empty_list():
    """A spec with no paths returns an empty tool list."""
    spec = {"openapi": "3.0.0", "info": {"title": "Empty", "version": "1"}, "paths": {}}
    result = parse_openapi_spec(spec)
    assert result == []


def test_invalid_spec_raises_value_error():
    """A dict without 'paths' key raises ValueError."""
    with pytest.raises(ValueError, match="paths"):
        parse_openapi_spec({"openapi": "3.0.0"})
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd dev-kit && uv run pytest tests/test_openapi_parser.py -v
```
Expected: FAIL — `openapi_parser` module does not exist.

- [ ] **Step 3: Implement openapi_parser.py**

Create `dev-kit/dev_kit/agent/openapi_parser.py`:

```python
"""
dev-kit/dev_kit/agent/openapi_parser.py

Parses OpenAPI 3.0/3.1 specs to extract tool-building data for the Action Gateway.

Given an OpenAPI spec dict (already parsed from JSON/YAML), produces a list of
ParsedTool entries that the agent can present to the user for naming and filtering
before writing to action_gateway config.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParsedParam:
    """A single parameter extracted from an OpenAPI endpoint."""

    name: str
    source: str  # "agent" always — static values set interactively later
    type: str
    required: bool
    description: str
    default: Any = None


@dataclass
class ParsedTool:
    """An API endpoint extracted from an OpenAPI spec, ready for tool naming."""

    path: str
    method: str
    description: str
    base_url: str
    params: list[ParsedParam] = field(default_factory=list)
    auth_type: str = "none"
    auth_header: str | None = None
    auth_secret_env_hint: str | None = None

    @property
    def suggested_id(self) -> str:
        """Generate a snake_case suggested tool ID from path and method."""
        path_part = self.path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
        if not path_part:
            path_part = "root"
        return f"{self.method.lower()}_{path_part}"


def parse_openapi_spec(spec: dict[str, Any]) -> list[ParsedTool]:
    """Extract tool definitions from an OpenAPI 3.0/3.1 specification dict.

    Parses paths, methods, parameters (path, query, body), auth schemes,
    and produces one ParsedTool per path+method combination.

    Args:
        spec: Parsed OpenAPI spec as a dict. Must contain a 'paths' key.

    Returns:
        List of ParsedTool entries, one per path+method combination.

    Raises:
        ValueError: If 'paths' key is absent from the spec.
    """
    if "paths" not in spec:
        raise ValueError("OpenAPI spec must contain a 'paths' key")

    base_url = _extract_base_url(spec)
    auth_type, auth_header = _extract_global_auth(spec)
    paths: dict[str, Any] = spec.get("paths", {})

    tools: list[ParsedTool] = []
    http_methods = {"get", "post", "put", "delete", "patch", "head", "options"}

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in http_methods:
                continue
            if not isinstance(operation, dict):
                continue

            description = (
                operation.get("summary")
                or operation.get("description")
                or f"{method.upper()} {path}"
            )

            params = _extract_params(operation, path_item)

            tool = ParsedTool(
                path=path,
                method=method.upper(),
                description=description,
                base_url=base_url,
                params=params,
                auth_type=auth_type,
                auth_header=auth_header,
            )
            tools.append(tool)
            logger.debug(
                "openapi_parse_endpoint",
                extra={
                    "operation": "openapi_parser.parse_openapi_spec",
                    "status": "success",
                    "path": path,
                    "method": method,
                    "param_count": len(params),
                },
            )

    return tools


def _extract_base_url(spec: dict[str, Any]) -> str:
    """Extract the first server URL from the spec.

    Args:
        spec: OpenAPI spec dict.

    Returns:
        First server URL, or empty string if none found.
    """
    servers = spec.get("servers", [])
    if servers and isinstance(servers[0], dict):
        url = servers[0].get("url", "")
        # Strip trailing slash
        return url.rstrip("/")
    return ""


def _extract_global_auth(spec: dict[str, Any]) -> tuple[str, str | None]:
    """Extract global auth scheme from components/securitySchemes.

    Only examines the first security requirement at the spec level.

    Args:
        spec: OpenAPI spec dict.

    Returns:
        Tuple of (auth_type, auth_header). auth_type is one of:
        'none', 'api_key', 'bearer'. auth_header is the header name
        for api_key, or None otherwise.
    """
    components = spec.get("components", {})
    schemes: dict[str, Any] = components.get("securitySchemes", {})

    # Find which scheme is applied globally
    global_security = spec.get("security", [])
    active_scheme_name: str | None = None
    if global_security and isinstance(global_security[0], dict):
        active_scheme_name = next(iter(global_security[0]), None)

    if active_scheme_name and active_scheme_name in schemes:
        scheme = schemes[active_scheme_name]
    elif schemes:
        # Fall back to the first scheme defined
        scheme = next(iter(schemes.values()))
    else:
        return "none", None

    scheme_type = scheme.get("type", "")
    if scheme_type == "apiKey":
        location = scheme.get("in", "header")
        header_name = scheme.get("name", "") if location == "header" else None
        return "api_key", header_name
    if scheme_type == "http":
        bearer_format = scheme.get("scheme", "").lower()
        if bearer_format == "bearer":
            return "bearer", None
    if scheme_type == "oauth2":
        flows = scheme.get("flows", {})
        token_url = None
        for flow in flows.values():
            token_url = flow.get("tokenUrl")
            if token_url:
                break
        return "oauth2", None

    return "none", None


def _extract_params(operation: dict[str, Any], path_item: dict[str, Any]) -> list[ParsedParam]:
    """Extract all parameters from an operation (path + query + body).

    Combines path-level parameters with operation-level parameters.
    Request body properties are also extracted as agent-sourced params.

    Args:
        operation: The operation dict from the OpenAPI paths section.
        path_item: The parent path-item dict (may contain shared parameters).

    Returns:
        List of ParsedParam entries.
    """
    params: list[ParsedParam] = []
    seen: set[str] = set()

    # Collect path-level + operation-level parameters
    all_param_defs = list(path_item.get("parameters", [])) + list(operation.get("parameters", []))
    for param_def in all_param_defs:
        if not isinstance(param_def, dict):
            continue
        name = param_def.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)

        schema = param_def.get("schema", {})
        param_type = schema.get("type", "string")
        default_val = schema.get("default")
        description = param_def.get("description", schema.get("description", ""))
        required = bool(param_def.get("required", False))

        params.append(ParsedParam(
            name=name,
            source="agent",
            type=param_type,
            required=required,
            description=description,
            default=default_val,
        ))

    # Extract request body properties
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    body_schema: dict[str, Any] = {}

    for media_type in ("application/json", "application/x-www-form-urlencoded"):
        if media_type in content:
            body_schema = content[media_type].get("schema", {})
            break

    if body_schema.get("type") == "object":
        required_fields: list[str] = body_schema.get("required", [])
        properties: dict[str, Any] = body_schema.get("properties", {})
        for prop_name, prop_schema in properties.items():
            if prop_name in seen:
                continue
            seen.add(prop_name)
            params.append(ParsedParam(
                name=prop_name,
                source="agent",
                type=prop_schema.get("type", "string"),
                required=prop_name in required_fields,
                description=prop_schema.get("description", ""),
                default=prop_schema.get("default"),
            ))

    return params
```

- [ ] **Step 4: Run the tests**

```bash
cd dev-kit && uv run pytest tests/test_openapi_parser.py -v
```
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd dev-kit && git add dev_kit/agent/openapi_parser.py tests/test_openapi_parser.py
git commit -m "feat(devkit): add OpenAPI 3.0/3.1 spec parser for tool extraction"
```

---

## Task 5: Add New Tool-Phase Agent Tools (action_gateway tools)

**Files:**
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Modify: `dev-kit/dev_kit/agent/accumulator.py`

### Background
The dev-kit agent needs 4 new tools:
1. `parse_openapi_spec` — accepts raw OpenAPI JSON/YAML string, parses it, returns candidate tool list
2. `add_rest_api_tool` — adds a REST API tool definition to action_gateway config (one tool at a time)
3. `discover_mcp_tools` — fetches tools/list from an MCP server URL, returns available tools
4. `add_mcp_tool` — adds an MCP tool to action_gateway config

The accumulator needs a new method `add_action_gateway_tool` that appends to the `tools` list.

- [ ] **Step 1: Write failing tests**

Create `dev-kit/tests/test_tools_phase.py`:

```python
"""Tests for the new tools-phase tool handlers."""
import json
import pytest
from unittest.mock import patch, MagicMock

from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.tools import ToolHandler


@pytest.fixture()
def acc():
    return ConfigAccumulator()


@pytest.fixture()
def state():
    return {"phase": "tools", "phase_changed": None, "rollback_to": None, "project_meta": {}}


@pytest.fixture()
def handler(acc, state):
    return ToolHandler(acc, state)


# ---- add_rest_api_tool ----

def test_add_rest_api_tool_adds_to_accumulator(handler, acc):
    """add_rest_api_tool should append a tool to action_gateway.tools."""
    result = handler.dispatch("add_rest_api_tool", {
        "id": "onest_search",
        "category": "read",
        "description": "Search jobs",
        "base_url": "https://api.example.com",
        "auth_type": "api_key",
        "auth_header": "X-API-KEY",
        "auth_secret_env": "ONEST_KEY",
        "endpoints": [
            {
                "name": "search",
                "method": "POST",
                "path": "/search",
                "params": [
                    {"name": "query", "source": "agent", "type": "string", "required": True, "description": "Search query"}
                ],
            }
        ],
    })
    assert "onest_search" in result
    ag = acc.get_block("action_gateway")
    assert len(ag["tools"]) == 1
    assert ag["tools"][0]["id"] == "onest_search"


def test_add_rest_api_tool_rejects_duplicate(handler, acc):
    """Adding a tool with a duplicate ID returns an error."""
    params = {
        "id": "dup_tool",
        "category": "read",
        "description": "x",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [],
    }
    handler.dispatch("add_rest_api_tool", params)
    result = handler.dispatch("add_rest_api_tool", params)
    assert "already exists" in result.lower() or "duplicate" in result.lower()


def test_add_rest_api_tool_syncs_agent_core_connector(handler, acc):
    """Adding a REST API tool auto-creates a corresponding agent_core connector."""
    handler.dispatch("add_rest_api_tool", {
        "id": "market_lookup",
        "category": "read",
        "description": "Find job listings",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [
            {
                "name": "search",
                "method": "GET",
                "path": "/jobs",
                "params": [{"name": "location", "source": "agent", "type": "string", "required": True, "description": "City or region"}],
            }
        ],
    })
    ac = acc.get_block("agent_core")
    read_connectors = ac.get("connectors", {}).get("read", [])
    assert any(c["name"] == "market_lookup" for c in read_connectors)


# ---- add_mcp_tool ----

def test_add_mcp_tool_adds_to_accumulator(handler, acc):
    """add_mcp_tool should append an MCP tool to action_gateway.tools."""
    result = handler.dispatch("add_mcp_tool", {
        "id": "obsrv_query",
        "category": "read",
        "description": "Query Obsrv data",
        "mcp_server_url": "https://mcp.example.com",
        "tool_name": "query_dataset",
        "input_schema": {"type": "object", "properties": {"dataset": {"type": "string"}}},
    })
    assert "obsrv_query" in result
    ag = acc.get_block("action_gateway")
    mcp_tools = [t for t in ag["tools"] if t["type"] == "mcp"]
    assert len(mcp_tools) == 1


# ---- parse_openapi_spec ----

def test_parse_openapi_spec_returns_candidates(handler):
    """parse_openapi_spec should return a JSON list of candidate tool descriptions."""
    spec_json = json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/search": {
                "post": {
                    "summary": "Search",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"q": {"type": "string"}},
                                }
                            }
                        }
                    },
                }
            }
        },
    })
    result = handler.dispatch("parse_openapi_spec", {"spec_json": spec_json})
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["path"] == "/search"


def test_parse_openapi_spec_invalid_json_returns_error(handler):
    """Invalid JSON in spec_json returns an error string."""
    result = handler.dispatch("parse_openapi_spec", {"spec_json": "not json {{"})
    assert "error" in result.lower() or "ERROR" in result
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd dev-kit && uv run pytest tests/test_tools_phase.py -v
```
Expected: FAIL — `add_rest_api_tool`, `add_mcp_tool`, `parse_openapi_spec` not yet defined.

- [ ] **Step 3: Add `add_action_gateway_tool` method to accumulator.py**

Add this method to the `ConfigAccumulator` class in `dev-kit/dev_kit/agent/accumulator.py`, after the `remove_subagent` method:

```python
def add_action_gateway_tool(self, tool: dict) -> None:
    """Add a tool definition to the action_gateway tools list.

    Args:
        tool: Tool dict. Must include an 'id' key. Type should be 'rest_api' or 'mcp'.

    Raises:
        ValueError: If tool has no 'id' key.
        ValueError: If a tool with the same id already exists.
    """
    if "id" not in tool:
        raise ValueError("Tool must have an 'id' key")
    tools: list[dict] = self._data["action_gateway"].setdefault("tools", [])
    if any(t.get("id") == tool["id"] for t in tools):
        raise ValueError(f"Tool with id {tool['id']!r} already exists")
    tools.append(deepcopy(tool))

def get_action_gateway_tools(self) -> list[dict]:
    """Return the current list of action_gateway tool definitions.

    Returns:
        Deep copy of the tools list.
    """
    return deepcopy(self._data["action_gateway"].get("tools", []))
```

- [ ] **Step 4: Add the 4 new tool definitions to TOOL_DEFINITIONS in tools.py**

Add to the `TOOL_DEFINITIONS` list in `dev-kit/dev_kit/agent/tools.py`, after the `rollback_to_checkpoint` entry:

```python
    {
        "name": "parse_openapi_spec",
        "description": (
            "Parse a raw OpenAPI 3.0/3.1 spec (JSON or YAML string) and return a list of candidate tool definitions. "
            "Use this when the user uploads or provides an OpenAPI spec. "
            "The returned candidates help you decide which endpoints to add with add_rest_api_tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec_json": {
                    "type": "string",
                    "description": "The full OpenAPI spec as a JSON or YAML string",
                },
            },
            "required": ["spec_json"],
        },
    },
    {
        "name": "add_rest_api_tool",
        "description": (
            "Add a REST API tool to the Action Gateway config. "
            "Call this once per tool after confirming the endpoint details with the user. "
            "This also auto-creates the matching connector in agent_core.connectors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique snake_case tool ID, e.g. onest_market_lookup"},
                "category": {"type": "string", "enum": ["read", "write", "identity"], "description": "read = no consent; write/identity = Trust Layer consent required"},
                "description": {"type": "string", "description": "What this tool does — shown to the LLM for routing decisions"},
                "base_url": {"type": "string", "description": "API base URL, e.g. https://api.example.com/v2"},
                "auth_type": {"type": "string", "enum": ["none", "api_key", "bearer", "oauth2"]},
                "auth_header": {"type": "string", "description": "Header name for api_key auth, e.g. X-API-KEY"},
                "auth_secret_env": {"type": "string", "description": "Env var name holding the API key"},
                "timeout_ms": {"type": "integer", "default": 5000},
                "endpoints": {
                    "type": "array",
                    "description": "One or more endpoint definitions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
                            "path": {"type": "string"},
                            "params": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "source": {"type": "string", "enum": ["agent", "static"]},
                                        "type": {"type": "string"},
                                        "required": {"type": "boolean"},
                                        "description": {"type": "string"},
                                        "value": {"description": "Fixed value when source is static"},
                                        "default": {"description": "Default value for optional agent params"},
                                    },
                                    "required": ["name", "source", "type"],
                                },
                            },
                        },
                        "required": ["name", "method", "path"],
                    },
                },
            },
            "required": ["id", "category", "description", "base_url", "auth_type", "endpoints"],
        },
    },
    {
        "name": "discover_mcp_tools",
        "description": (
            "Fetch the list of available tools from an MCP server by calling its tools/list endpoint. "
            "Use this when the user provides an MCP server URL. "
            "Returns the raw tools list so you can present options to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mcp_server_url": {
                    "type": "string",
                    "description": "Base URL of the MCP server, e.g. https://mcp.example.com",
                },
            },
            "required": ["mcp_server_url"],
        },
    },
    {
        "name": "add_mcp_tool",
        "description": (
            "Add an MCP tool to the Action Gateway config. "
            "Call this once per tool after the user has selected which MCP tools to include. "
            "This also auto-creates the matching connector in agent_core.connectors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique snake_case tool ID"},
                "category": {"type": "string", "enum": ["read", "write", "identity"]},
                "description": {"type": "string", "description": "What this tool does — shown to the LLM"},
                "mcp_server_url": {"type": "string", "description": "Base URL of the MCP server"},
                "tool_name": {"type": "string", "description": "Tool name as returned by MCP tools/list"},
                "input_schema": {"type": "object", "description": "JSON Schema from MCP tools/list response"},
                "timeout_ms": {"type": "integer", "default": 5000},
            },
            "required": ["id", "category", "description", "mcp_server_url", "tool_name"],
        },
    },
```

- [ ] **Step 5: Add handler methods to ToolHandler in tools.py**

Add these methods to the `ToolHandler` class and register them in `dispatch()`:

In the `handlers` dict in `dispatch()`:
```python
"parse_openapi_spec": self._handle_parse_openapi_spec,
"add_rest_api_tool": self._handle_add_rest_api_tool,
"discover_mcp_tools": self._handle_discover_mcp_tools,
"add_mcp_tool": self._handle_add_mcp_tool,
```

New handler methods:

```python
def _handle_parse_openapi_spec(self, inputs: dict) -> str:
    import json
    import yaml as _yaml
    from dev_kit.agent.openapi_parser import parse_openapi_spec

    spec_json = inputs.get("spec_json", "")
    try:
        # Try JSON first, then YAML
        try:
            spec = json.loads(spec_json)
        except json.JSONDecodeError:
            spec = _yaml.safe_load(spec_json)
        if not isinstance(spec, dict):
            return "ERROR: spec must be a JSON or YAML object"
    except Exception as exc:
        return f"ERROR: could not parse spec — {exc}"

    try:
        tools = parse_openapi_spec(spec)
    except ValueError as exc:
        return f"ERROR: {exc}"

    candidates = [
        {
            "suggested_id": t.suggested_id,
            "path": t.path,
            "method": t.method,
            "description": t.description,
            "base_url": t.base_url,
            "param_names": [p.name for p in t.params],
            "auth_type": t.auth_type,
            "auth_header": t.auth_header,
        }
        for t in tools
    ]
    return json.dumps(candidates, ensure_ascii=False, indent=2)

def _handle_add_rest_api_tool(self, inputs: dict) -> str:
    tool = {
        "id": inputs["id"],
        "type": "rest_api",
        "category": inputs["category"],
        "description": inputs["description"],
        "base_url": inputs["base_url"],
        "auth": {
            "type": inputs["auth_type"],
            **({"header": inputs["auth_header"]} if inputs.get("auth_header") else {}),
            **({"secret_env": inputs["auth_secret_env"]} if inputs.get("auth_secret_env") else {}),
        },
        "timeout_ms": inputs.get("timeout_ms", 5000),
        "endpoints": inputs.get("endpoints", []),
        "response": {"max_size_chars": 4000},
    }
    try:
        self._acc.add_action_gateway_tool(tool)
    except ValueError as exc:
        return f"ERROR: {exc}"

    # Auto-sync agent_core connector
    self._sync_connector_from_tool(tool)
    return f"Tool '{inputs['id']}' added to Action Gateway config."

def _handle_discover_mcp_tools(self, inputs: dict) -> str:
    import json
    import httpx

    url = inputs["mcp_server_url"].rstrip("/")
    payload = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
    try:
        response = httpx.post(
            f"{url}/",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        return f"ERROR: could not reach MCP server at {url} — {exc}"
    except Exception as exc:
        return f"ERROR: {exc}"

    tools = data.get("result", {}).get("tools", [])
    if not tools:
        return f"No tools found at {url}. Check the URL and ensure the server is running."

    summary = [
        {"name": t.get("name", ""), "description": t.get("description", ""), "input_schema": t.get("inputSchema", {})}
        for t in tools
    ]
    return json.dumps(summary, ensure_ascii=False, indent=2)

def _handle_add_mcp_tool(self, inputs: dict) -> str:
    tool = {
        "id": inputs["id"],
        "type": "mcp",
        "category": inputs["category"],
        "description": inputs["description"],
        "mcp_server_url": inputs["mcp_server_url"],
        "tool_name": inputs["tool_name"],
        "input_schema": inputs.get("input_schema", {}),
        "timeout_ms": inputs.get("timeout_ms", 5000),
    }
    try:
        self._acc.add_action_gateway_tool(tool)
    except ValueError as exc:
        return f"ERROR: {exc}"

    # Auto-sync agent_core connector
    self._sync_connector_from_tool(tool)
    return f"MCP tool '{inputs['id']}' added to Action Gateway config."

def _sync_connector_from_tool(self, tool: dict) -> None:
    """Auto-create or update agent_core connector from a tool definition.

    Generates the LLM-facing connector (name, description, input_schema)
    from the full tool definition and merges it into agent_core.connectors
    under the appropriate category (read/write/identity).

    Args:
        tool: Tool dict from action_gateway.tools with keys: id, category, description,
              type, endpoints (rest_api) or input_schema (mcp).
    """
    category = tool.get("category", "read")
    tool_id = tool["id"]

    # Build input schema from endpoints (rest_api) or input_schema (mcp)
    if tool.get("type") == "mcp":
        input_schema = tool.get("input_schema", {"type": "object", "properties": {}})
    else:
        # Merge agent-sourced params from all endpoints into one schema
        properties: dict = {}
        required: list = []
        for endpoint in tool.get("endpoints", []):
            for param in endpoint.get("params", []):
                if param.get("source") != "agent":
                    continue
                prop: dict = {"type": param.get("type", "string")}
                if param.get("description"):
                    prop["description"] = param["description"]
                if param.get("default") is not None:
                    prop["default"] = param["default"]
                properties[param["name"]] = prop
                if param.get("required"):
                    required.append(param["name"])
        input_schema = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required

    connector = {
        "name": tool_id,
        "description": tool.get("description", ""),
        "input_schema": input_schema,
    }

    # Determine section: read/write/identity
    section_map = {"read": "read", "write": "write", "identity": "identity"}
    section = section_map.get(category, "read")

    connectors_block = self._acc._data["agent_core"].setdefault("connectors", {})
    connector_list: list = connectors_block.setdefault(section, [])

    # Update existing or append
    for i, c in enumerate(connector_list):
        if c.get("name") == tool_id:
            connector_list[i] = connector
            return
    connector_list.append(connector)
```

Also add `httpx` to the dev-kit dependencies:
```bash
cd dev-kit && uv add httpx
```

- [ ] **Step 6: Run the tests**

```bash
cd dev-kit && uv run pytest tests/test_tools_phase.py -v
```
Expected: All tests PASS (note: `discover_mcp_tools` test is not in the test file — it requires a live server; integration test is covered separately).

- [ ] **Step 7: Commit**

```bash
cd dev-kit && git add dev_kit/agent/accumulator.py dev_kit/agent/tools.py pyproject.toml uv.lock
git commit -m "feat(devkit): add action_gateway tool configuration phase handlers"
```

---

## Task 6: Phase Ordering and Reach Layer Channel Selection

**Files:**
- Modify: `dev-kit/dev_kit/agent/accumulator.py`
- Modify: `dev-kit/dev_kit/agent/tools.py`
- Modify: `dev-kit/dev_kit/agent/conversation.py`
- Modify: `dev-kit/dev_kit/agent/prompts/base.py`

### Background
The `connectors` phase is renamed `tools` and its position moves to just before `workflow` (so tool IDs are known when assigning tools to subagents). The `reach` phase must first ask which channels the user wants, then configure only those.

- [ ] **Step 1: Write failing tests for channel selection tool**

Add to `dev-kit/tests/test_tools_phase.py`:

```python
# ---- set_reach_channels ----

def test_set_reach_channels_stores_selection(handler, acc):
    """set_reach_channels should write selected channels to reach_layer config."""
    result = handler.dispatch("set_reach_channels", {"channels": ["web", "cli"]})
    assert "web" in result
    rl = acc.get_block("reach_layer")
    assert rl.get("_selected_channels") == ["web", "cli"]


def test_set_reach_channels_rejects_unknown(handler, acc):
    """set_reach_channels should reject unknown channel names."""
    result = handler.dispatch("set_reach_channels", {"channels": ["fax", "web"]})
    assert "ERROR" in result or "invalid" in result.lower()


def test_set_reach_channels_requires_at_least_one(handler, acc):
    """set_reach_channels rejects empty list."""
    result = handler.dispatch("set_reach_channels", {"channels": []})
    assert "ERROR" in result or "at least one" in result.lower()
```

- [ ] **Step 2: Run to confirm fail**

```bash
cd dev-kit && uv run pytest tests/test_tools_phase.py::test_set_reach_channels_stores_selection -v
```
Expected: FAIL.

- [ ] **Step 3: Update PHASES list in accumulator.py**

In `dev-kit/dev_kit/agent/accumulator.py`, update `PHASES`:

```python
PHASES: list[str] = [
    "overview",
    "language",
    "knowledge",
    "memory",
    "trust",
    "tools",       # was "connectors" — now covers action_gateway tool config
    "workflow",
    "observability",
    "reach",
    "review",
]
```

- [ ] **Step 4: Add `set_reach_channels` to TOOL_DEFINITIONS in tools.py**

Add this to `TOOL_DEFINITIONS`:

```python
    {
        "name": "set_reach_channels",
        "description": (
            "Record which deployment channels the user wants (web, cli, voice). "
            "Call this at the start of the reach phase, before collecting per-channel config. "
            "Only the selected channels will be configured."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channels": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["web", "cli", "voice"]},
                    "description": "One or more of: web, cli, voice",
                    "minItems": 1,
                },
            },
            "required": ["channels"],
        },
    },
```

Update `dispatch()` to register it:
```python
"set_reach_channels": self._handle_set_reach_channels,
```

Add handler:
```python
def _handle_set_reach_channels(self, inputs: dict) -> str:
    channels = inputs.get("channels", [])
    valid = {"web", "cli", "voice"}
    invalid = [c for c in channels if c not in valid]
    if invalid:
        return f"ERROR: unknown channel(s): {invalid}. Valid channels: {sorted(valid)}"
    if not channels:
        return "ERROR: at least one channel must be selected."
    # Store selection in reach_layer data for prompt use
    self._acc._data["reach_layer"]["_selected_channels"] = list(channels)
    channel_list = ", ".join(channels)
    return f"Channels selected: {channel_list}. Now configure each selected channel."
```

- [ ] **Step 5: Update set_phase to enforce 'tools' instead of 'connectors'**

In `_handle_set_phase`, the PHASES list already changed so `tools` is now the phase name. The `set_phase` tool definition also needs updating:

In `TOOL_DEFINITIONS`, find the `set_phase` entry and update its enum:

```python
"enum": ["overview", "language", "knowledge", "memory", "trust", "tools", "workflow", "observability", "reach", "review"],
```

- [ ] **Step 6: Update conversation.py to pass available tool IDs to system prompt**

In `dev-kit/dev_kit/agent/conversation.py`, replace the `_build_system_prompt` method:

```python
def _build_system_prompt(self) -> str:
    """Build the system prompt for the current phase and accumulator state."""
    meta = self._state.get("project_meta", {})
    available_tools = [t["id"] for t in self.accumulator.get_action_gateway_tools()]
    return build_system_prompt(
        project_name=meta.get("name", ""),
        project_description=meta.get("description", ""),
        accumulator=self.accumulator,
        phase=self._state["phase"],
        checkpoint_summaries=self._get_checkpoint_summaries(),
        available_tools=available_tools or None,
    )
```

- [ ] **Step 7: Update build_system_prompt signature in prompts/base.py**

In `dev-kit/dev_kit/agent/prompts/base.py`, update the function signature:

```python
def build_system_prompt(
    project_name: str,
    project_description: str,
    accumulator: ConfigAccumulator,
    phase: str,
    checkpoint_summaries: list[str],
    available_tools: list[str] | None = None,
) -> str:
    """Build the full system prompt for the given conversation phase."""
    sections = [_DPG_OVERVIEW]

    if project_name:
        sections.append(f"## Project\nName: {project_name}\nDescription: {project_description}")

    if checkpoint_summaries:
        sections.append("## Prior phase summaries\n" + "\n---\n".join(checkpoint_summaries))

    sections.append(accumulator.summary())
    sections.append(f"## Current phase: {phase}")

    addition = get_phase_addition(phase, available_tools=available_tools)
    if addition:
        sections.append(addition)

    return "\n\n".join(sections)
```

- [ ] **Step 8: Run all tool phase tests**

```bash
cd dev-kit && uv run pytest tests/test_tools_phase.py -v
```
Expected: All tests PASS.

- [ ] **Step 9: Commit**

```bash
cd dev-kit && git add dev_kit/agent/accumulator.py dev_kit/agent/tools.py dev_kit/agent/conversation.py dev_kit/agent/prompts/base.py
git commit -m "feat(devkit): rename connectors→tools phase, add channel selection tool, wire available_tools to workflow prompt"
```

---

## Task 7: Update Phase Prompts

**Files:**
- Modify: `dev-kit/dev_kit/agent/prompts/phases.py`

### Background
`phases.py` contains the per-phase additions to the system prompt. These need updating:
1. `overview` — update phase list (connectors→tools, explain new action_gateway flow)
2. `connectors` phase removed, new `tools` phase added
3. `workflow` phase — reference `available_tools` (not `available_connectors`)
4. `reach` phase — now channel-selection-first

- [ ] **Step 1: Update `get_phase_addition` signature**

In `phases.py`, change the function signature from:
```python
def get_phase_addition(phase: str, available_connectors: list[str] | None = None) -> str:
```
to:
```python
def get_phase_addition(phase: str, available_tools: list[str] | None = None) -> str:
```

- [ ] **Step 2: Update overview phase text**

Replace the `if phase == "overview":` block with:

```python
    if phase == "overview":
        return (
            "## Overview phase\n\n"
            "Your goal in this phase: understand the use case well enough to configure all 7 DPG blocks.\n\n"
            "**Required 10-phase sequence — you MUST visit every phase in this exact order:**\n"
            "1. overview       — understand the use case (current phase)\n"
            "2. language       — LLM models, language normalisation, NLU intents/entities\n"
            "3. knowledge      — RAG knowledge base, persona, document sources\n"
            "4. memory         — session state fields, persistent graph, consent mode\n"
            "5. trust          — blocked phrases, escalation topics, safety guardrails\n"
            "6. tools          — Action Gateway tools (REST APIs via OpenAPI spec, or MCP servers)\n"
            "7. workflow       — subagent state machine, routing rules (uses tool IDs from step 6)\n"
            "8. observability  — outcome lifecycle states, metrics, domain name\n"
            "9. reach          — deployment channels (web/CLI/voice) and per-channel config\n"
            "10. review        — validate, fix missing fields, finalize all blocks\n\n"
            "**CRITICAL: you may NOT skip any phase.** set_phase will return an error if you try to jump ahead.\n\n"
            "**What to collect in this phase:**\n"
            "- What problem does this agent solve? Who are the users?\n"
            "- What languages do users speak?\n"
            "- What knowledge/documents will the agent use?\n"
            "- What external APIs or MCP servers are needed (if any)?\n"
            "- What does a successful conversation look like?\n\n"
            "Once you have a clear picture of the use case, call `set_project_meta` to save it, "
            "then call `set_phase('language')` to begin configuration.\n"
            "Do NOT call set_phase('language') until you have asked at least 2-3 clarifying questions "
            "and understood the use case."
        )
```

- [ ] **Step 3: Remove `connectors` phase, add `tools` phase**

Remove the `if phase == "connectors":` block entirely and add:

```python
    if phase == "tools":
        return (
            "## Tools phase — configure Action Gateway tools\n\n"
            "In this phase you configure the external tools the AI agent can call. "
            "There are two tool types: REST API (external HTTP APIs) and MCP (MCP protocol servers).\n\n"
            "**Step 1 — Discover what tools the user needs:**\n"
            "Ask: 'Does your agent need to call any external APIs or services?'\n"
            "Ask: 'Do you have an OpenAPI/Swagger spec, or an MCP server URL, or will you describe the API manually?'\n"
            "- If no tools at all needed: confirm and call set_phase('workflow') immediately.\n\n"
            "**Step 2A — REST API tools via OpenAPI spec:**\n"
            "- If user has a spec: call `parse_openapi_spec` with the spec JSON/YAML string.\n"
            "  Present the candidate endpoints and ask: 'Which of these do you want to include? Give each a name.'\n"
            "- For each selected endpoint, call `add_rest_api_tool` with the full config.\n"
            "- Ask about auth: 'Does this API need an API key? What header? What env var holds the key?'\n\n"
            "**Step 2B — REST API tools conversationally (no spec):**\n"
            "- If user has no spec, collect each tool by asking:\n"
            "  1. 'What is a short name for this tool? (e.g. job_search, booking_create)'\n"
            "  2. 'What does it do?' (description shown to the LLM)\n"
            "  3. 'What is the base URL of the API?'\n"
            "  4. 'What endpoint path and HTTP method? (e.g. POST /search)'\n"
            "  5. 'What parameters does the agent need to provide? (name, type, required?)'\n"
            "  6. 'Does it require authentication? (none / API key / bearer token)'\n"
            "  7. 'Is this a read tool (no consent needed) or a write tool (modifies data)?'\n"
            "- Call `add_rest_api_tool` once all details are collected.\n"
            "- Repeat for each additional tool.\n\n"
            "**Step 2C — MCP server tools:**\n"
            "- If user has an MCP server: ask for the URL.\n"
            "- Call `discover_mcp_tools` to fetch available tools from the server.\n"
            "- Present the list and ask: 'Which of these should your agent use?'\n"
            "- For each selected tool, ask: 'What category is this? (read/write/identity)'\n"
            "- Call `add_mcp_tool` for each selected tool.\n\n"
            "**Step 3 — Always ask after any tools are added:**\n"
            "'Are there any other tools your agent needs that we haven't configured yet?'\n"
            "If yes, repeat Step 2A, 2B, or 2C as appropriate for each remaining tool.\n"
            "Only proceed to set_phase('workflow') when the user confirms all tools are done.\n\n"
            "**After adding tools:**\n"
            "- Each tool auto-creates a matching connector in agent_core.connectors.\n"
            "- The tool ID becomes the name used to assign tools to subagents in the next phase.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("action_gateway")
            + "```\n\n"
            "➡️ When all tools are configured (or confirmed not needed), call `set_phase('workflow')`."
        )
```

- [ ] **Step 4: Update workflow phase to use `available_tools`**

Replace the `if phase == "workflow":` block:

```python
    if phase == "workflow":
        tool_note = ""
        if available_tools:
            tool_note = f"\n\nAvailable tools (configured in Tools phase): {', '.join(available_tools)}"
            tool_note += "\nAssign these to subagents by including their IDs in the `tools` list."
        return (
            "## Workflow Design phase\n\n"
            "**CRITICAL — forbidden keys that will cause validation failure:**\n"
            "❌ DO NOT use: agent.name, agent.system_prompt (these don't exist)\n"
            "❌ DO NOT use: agent_workflow.start_subagent, agent_workflow.fallback_subagent\n"
            "✅ USE: agent_workflow.default_fallback_subagent_id for the fallback subagent\n"
            "✅ USE: agent_workflow.agent_system_prompt for the top-level LLM persona\n\n"
            "Build the subagent state machine step by step:\n"
            "1. Use `create_subagent` for each node and `add_routing_rule` for each edge.\n"
            "2. After the graph is built, use `update_config` with section=`agent_workflow` to set:\n"
            "   workflow_id, version, agent_system_prompt, global_intents, global_routing, default_fallback_subagent_id\n"
            "3. If agent.primary_model was not set in the Language phase, set it now with section=`agent`.\n"
            "4. If preprocessing.nlu_processor.intents was not set, set it now with section=`preprocessing.nlu_processor`.\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below for each subagent:\n\n"
            "```yaml\n"
            + _extract_template_sections("agent_core", ["agent_workflow"])
            + "```"
            + tool_note
            + "\n\n"
            + _WORKFLOW_EXAMPLE
            + "\n\n➡️ When all subagents are created, routing rules added, and agent_workflow metadata set, call `set_phase('observability')`."
        )
```

- [ ] **Step 5: Update reach phase to include channel selection**

Replace the `if phase == "reach":` block:

```python
    if phase == "reach":
        return (
            "## Reach phase — deployment channel configuration\n\n"
            "The Reach Layer handles how users interact with the agent. "
            "Three channel types are supported: web (React chat UI), cli (terminal), voice (VOIP/Raya).\n\n"
            "**Step 1 — Channel selection (REQUIRED first step):**\n"
            "Ask the user: 'Which channels do you want to deploy? You can choose one or more: web, CLI (terminal), voice.'\n"
            "Once you know the answer, call `set_reach_channels` with the selected channel names.\n\n"
            "**Step 2 — Configure each selected channel:**\n\n"
            "**Web channel config** (if selected):\n"
            "- UI branding: app_name, app_tagline, app_icon (emoji), agent_avatar, user_avatar\n"
            "- Setup screen: setup_heading (local + English), setup_subtitle, user_id_placeholder, user_id_hint, start_btn_label\n"
            "- Chat messages: new_session_msg, returning_user_msg (both in local language + English)\n"
            "- Storage: storage_key (e.g. kkb_user_id), theme_storage_key (e.g. kkb_theme)\n"
            "- Auth: enabled (false for dev), google_client_id (if enabled: true), cookie_secure\n"
            "Use: block=`reach_layer`, section=`reach_layer.channels.web`\n\n"
            "**CLI channel config** (if selected):\n"
            "- prompt: prefix shown before user input (e.g. 'You: ')\n"
            "- agent_prefix: prefix shown before agent replies (e.g. 'Agent: ')\n"
            "Use: block=`reach_layer`, section=`reach_layer.channels.cli`\n\n"
            "**Voice channel config** (if selected):\n"
            "- raya.stt_language: BCP-47 language code for speech-to-text (e.g. 'hi', 'en')\n"
            "- raya.tts_language: BCP-47 language code for text-to-speech\n"
            "- raya.voice_id: voice ID for the TTS provider\n"
            "- agent_core.timeout_ms: call timeout in ms (default 15000)\n"
            "- agent_core.greeting: first spoken message when session starts\n"
            "- agent_core.fallback_phrase: phrase when STT fails\n"
            "Use: block=`reach_layer`, section=`reach_layer.channels.voice`\n\n"
            "**Also set the domain:**\n"
            "Use: block=`reach_layer`, section=`reach_layer.common`, values={observability: {domain: 'your_slug'}}\n\n"
            "The `update_config` tool will return an ERROR if you use wrong key names. Read the error and retry.\n\n"
            "Use EXACTLY the key names shown in the template below:\n\n"
            "```yaml\n"
            + load_template_text("reach_layer")
            + "```\n\n"
            "➡️ When all selected channels are configured, call `set_phase('review')`."
        )
```

- [ ] **Step 6: Update review phase checklist**

Replace the `if phase == "review":` block to reflect new structure:

```python
    if phase == "review":
        return (
            "## Review phase\n\n"
            "All configs have been generated. Review the accumulated state above.\n"
            "Check that these required fields are set (fix with update_config if missing):\n"
            "- agent_core: agent.primary_model, agent.fallback_model,\n"
            "  preprocessing.language_normalisation.model, preprocessing.language_normalisation.supported_languages,\n"
            "  preprocessing.nlu_processor.model, preprocessing.nlu_processor.intents, preprocessing.nlu_processor.entities,\n"
            "  agent_workflow.workflow_id, agent_workflow.subagents (at least one with is_start: true)\n"
            "- knowledge_engine: knowledge.blocks.static_knowledge_base.collection_name\n"
            "- memory_layer: state.session, state.persistent.backend, state.persistent.graph.user_node\n"
            "- action_gateway: tools list (or empty list if no external tools needed)\n"
            "- observability_layer: observability.domain, observability.outcomes.lifecycle (at least one state)\n"
            "- reach_layer: reach_layer.channels (at least one channel configured)\n\n"
            "Call `finalize_config` for each block that is complete.\n"
            "The user can now view configs in the dashboard and edit them directly."
        )
```

- [ ] **Step 7: Commit**

```bash
cd dev-kit && git add dev_kit/agent/prompts/phases.py
git commit -m "feat(devkit): update phase prompts for tools phase, channel-selection reach, and workflow tool IDs"
```

---

## Task 8: Update the Accumulator summary() and get_workflow_graph()

**Files:**
- Modify: `dev-kit/dev_kit/agent/accumulator.py`

The `summary()` method needs to show configured tools. The `_build_system_prompt` method in conversation.py calls `accumulator.get_action_gateway_tools()` which was added in Task 5.

- [ ] **Step 1: Update summary() to show tools count**

Replace the `summary()` method in `ConfigAccumulator`:

```python
def summary(self) -> str:
    """Return a human-readable summary of current config state for system prompts."""
    lines = ["Current config state:"]
    for block in BLOCKS:
        data = self._data[block]
        status = self._statuses[block].value
        if block == "action_gateway":
            tool_count = len(data.get("tools", []))
            tool_ids = [t.get("id", "?") for t in data.get("tools", [])]
            if tool_ids:
                lines.append(f"  {block} ({status}): {tool_count} tools — {', '.join(tool_ids)}")
            else:
                lines.append(f"  {block} ({status}): no tools configured")
        elif data:
            keys = list(data.keys())[:4]
            lines.append(f"  {block} ({status}): {', '.join(keys)}")
        else:
            lines.append(f"  {block} ({status}): empty")
    return "\n".join(lines)
```

- [ ] **Step 2: Commit**

```bash
cd dev-kit && git add dev_kit/agent/accumulator.py
git commit -m "feat(devkit): update accumulator summary to show configured tool IDs"
```

---

## Task 9: End-to-End Integration Tests

**Files:**
- Create: `dev-kit/tests/test_integration_tools_phase.py`

- [ ] **Step 1: Write integration tests**

Create `dev-kit/tests/test_integration_tools_phase.py`:

```python
"""
Integration tests for the full tools phase flow:
add REST API tools, verify auto-sync to agent_core connectors,
add MCP tools, verify schema validation passes.
"""
import pytest
from dev_kit.agent.accumulator import ConfigAccumulator
from dev_kit.agent.tools import ToolHandler
from dev_kit.schema import validate_partial


@pytest.fixture()
def setup():
    acc = ConfigAccumulator()
    state = {"phase": "tools", "phase_changed": None, "rollback_to": None, "project_meta": {}}
    handler = ToolHandler(acc, state)
    return acc, handler


def test_rest_api_tool_passes_schema_validation(setup):
    """A REST API tool added via handler should pass action_gateway schema validation."""
    acc, handler = setup
    handler.dispatch("add_rest_api_tool", {
        "id": "job_search",
        "category": "read",
        "description": "Search for job listings",
        "base_url": "https://api.example.com/v1",
        "auth_type": "api_key",
        "auth_header": "X-API-KEY",
        "auth_secret_env": "JOBS_API_KEY",
        "endpoints": [
            {
                "name": "search",
                "method": "POST",
                "path": "/jobs",
                "params": [
                    {"name": "query", "source": "agent", "type": "string", "required": True, "description": "job query"},
                ],
            }
        ],
    })
    errors = validate_partial("action_gateway", acc.get_block("action_gateway"))
    assert errors == [], f"Validation errors: {errors}"


def test_agent_core_connector_auto_synced(setup):
    """After adding a tool, agent_core.connectors.read contains the auto-generated connector."""
    acc, handler = setup
    handler.dispatch("add_rest_api_tool", {
        "id": "job_search",
        "category": "read",
        "description": "Find jobs",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [
            {
                "name": "search",
                "method": "GET",
                "path": "/jobs",
                "params": [{"name": "q", "source": "agent", "type": "string", "required": True, "description": "query"}],
            }
        ],
    })
    ac = acc.get_block("agent_core")
    read_connectors = ac.get("connectors", {}).get("read", [])
    assert len(read_connectors) == 1
    assert read_connectors[0]["name"] == "job_search"
    assert read_connectors[0]["input_schema"]["properties"]["q"]["type"] == "string"


def test_mcp_tool_passes_schema_validation(setup):
    """An MCP tool added via handler should pass action_gateway schema validation."""
    acc, handler = setup
    handler.dispatch("add_mcp_tool", {
        "id": "data_query",
        "category": "read",
        "description": "Query dataset",
        "mcp_server_url": "https://mcp.example.com",
        "tool_name": "query",
        "input_schema": {"type": "object", "properties": {"dataset": {"type": "string"}}},
    })
    errors = validate_partial("action_gateway", acc.get_block("action_gateway"))
    assert errors == [], f"Validation errors: {errors}"


def test_write_tool_creates_write_connector(setup):
    """A write-category tool creates a connector under agent_core.connectors.write."""
    acc, handler = setup
    handler.dispatch("add_rest_api_tool", {
        "id": "apply_job",
        "category": "write",
        "description": "Submit job application",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [{"name": "apply", "method": "POST", "path": "/apply"}],
    })
    ac = acc.get_block("agent_core")
    write_connectors = ac.get("connectors", {}).get("write", [])
    assert any(c["name"] == "apply_job" for c in write_connectors)


def test_static_params_excluded_from_connector_schema(setup):
    """Static params (not filled by agent) should not appear in the agent_core connector input_schema."""
    acc, handler = setup
    handler.dispatch("add_rest_api_tool", {
        "id": "search",
        "category": "read",
        "description": "Search",
        "base_url": "https://api.example.com",
        "auth_type": "none",
        "endpoints": [
            {
                "name": "search",
                "method": "POST",
                "path": "/search",
                "params": [
                    {"name": "query", "source": "agent", "type": "string", "required": True, "description": "query"},
                    {"name": "limit", "source": "static", "type": "integer", "value": 10},
                ],
            }
        ],
    })
    ac = acc.get_block("agent_core")
    connector = ac["connectors"]["read"][0]
    props = connector["input_schema"]["properties"]
    assert "query" in props
    assert "limit" not in props  # static param excluded


def test_reach_channel_selection_schema(setup):
    """Reach channel selection stores correctly and web config validates."""
    acc, handler = setup
    handler.dispatch("set_reach_channels", {"channels": ["web"]})
    handler.dispatch("update_config", {
        "block": "reach_layer",
        "section": "reach_layer.channels.web",
        "values": {"ui": {"app_name": "My App", "app_icon": "🌾", "storage_key": "my_app_uid"}},
    })
    errors = validate_partial("reach_layer", acc.get_block("reach_layer"))
    # _selected_channels is an internal key, filter it out for validation
    rl_data = acc.get_block("reach_layer")
    rl_data.pop("_selected_channels", None)
    errors = validate_partial("reach_layer", rl_data)
    assert errors == [], f"Unexpected reach_layer errors: {errors}"
```

Note: For the `update_config` call in the last test, ToolHandler.dispatch needs access to the `_handle_update_config` method which is already there. But the test calls `update_config` directly — confirm the `ToolHandler.dispatch` already handles `update_config` (it does, from Task 5 Step 5, as it's in the existing handlers dict).

- [ ] **Step 2: Run integration tests**

```bash
cd dev-kit && uv run pytest tests/test_integration_tools_phase.py -v
```
Expected: All 6 tests PASS.

- [ ] **Step 3: Run full test suite**

```bash
cd dev-kit && uv run pytest tests/ -v --tb=short 2>/dev/null || uv run pytest -v --tb=short
```
Expected: All previously passing tests still PASS, no regressions.

- [ ] **Step 4: Commit**

```bash
cd dev-kit && git add tests/test_integration_tools_phase.py
git commit -m "test(devkit): add integration tests for tools phase and reach channel selection"
```

---

## Task 10: Final Review and Clean-up

- [ ] **Step 1: Verify no stale `connectors` references in phases.py**

```bash
grep -n "connectors" dev-kit/dev_kit/agent/prompts/phases.py
```
Expected: Only references inside YAML template blocks (for agent_core `connectors:` section) — no phase name references to "connectors".

- [ ] **Step 2: Verify set_phase tool reflects new phase names**

```bash
grep -A5 '"set_phase"' dev-kit/dev_kit/agent/tools.py | grep enum
```
Expected: Enum contains `"tools"` not `"connectors"`.

- [ ] **Step 3: Verify schema.py _BLOCK_MODEL_MAP is correct**

```bash
grep -A10 "_BLOCK_MODEL_MAP" dev-kit/dev_kit/schema.py
```
Expected: `action_gateway` maps to `ActionGatewayConfig`, `reach_layer` maps to `ReachLayerConfig`.

- [ ] **Step 4: Run full test suite with coverage**

```bash
cd dev-kit && uv run pytest tests/ --cov=dev_kit --cov-report=term-missing -q 2>/dev/null || uv run pytest --cov=dev_kit --cov-report=term-missing -q
```
Expected: Coverage stays ≥ 70% on changed modules.

- [ ] **Step 5: Final commit**

```bash
cd dev-kit && git add -A
git commit -m "feat(devkit): complete schema overhaul and tools phase — action_gateway tools-list model, reach multi-channel, OpenAPI+MCP ingestion"
```

---

## Self-Review

### Spec coverage check

| Requirement | Covered by task |
|---|---|
| Action Gateway schema updated to tools-list model | Task 1 |
| Pydantic models for RestApiToolDef, McpToolDef, AuthConfig | Task 1 |
| Reach Layer multi-channel schema | Task 2 |
| action_gateway.yaml template rewrite | Task 3 |
| reach_layer.yaml template rewrite (cli+web+voice) | Task 3 |
| OpenAPI 3.0/3.1 spec parser | Task 4 |
| parse_openapi_spec agent tool | Task 5 |
| add_rest_api_tool agent tool | Task 5 |
| discover_mcp_tools agent tool (MCP HTTP) | Task 5 |
| add_mcp_tool agent tool | Task 5 |
| Auto-sync agent_core connectors from tool defs | Task 5 |
| connectors phase renamed to tools | Task 6 |
| set_reach_channels tool | Task 6 |
| available_tools passed to workflow prompt | Task 6 |
| tools phase prompt with OpenAPI + MCP flow | Task 7 |
| reach phase with channel-selection-first | Task 7 |
| workflow phase references tool IDs | Task 7 |
| Phase ordering: tools before workflow | Task 6 |
| Issue #94: OpenAPI spec ingestion | Tasks 4+5 |
| Issue #95: MCP tool discovery and per-type flow | Tasks 5+7 |
| Integration tests | Task 9 |

### Known gaps / intentional out-of-scope
- **OpenAPI spec URL fetch**: `parse_openapi_spec` only accepts pasted JSON/YAML string. URL fetch (httpx.get + parse) can be a follow-up; for now instruct the LLM to tell the user to paste the spec content.
- **MCP SSE transport**: `discover_mcp_tools` uses HTTP POST (standard JSON-RPC). MCP SSE transport is a follow-up.
- **Voice channel full integration**: The Raya voice adapter config fields are defined in schema and template; actual deployment wiring is out of scope here.
- **Reach layer web adapter template cache**: `_selected_channels` is stored in accumulator data but filtered before schema validation in tests. The renderer should also strip this key before writing YAML. This is handled in Task 9 by filtering in the test; a follow-up should add stripping in `renderer.py`.
