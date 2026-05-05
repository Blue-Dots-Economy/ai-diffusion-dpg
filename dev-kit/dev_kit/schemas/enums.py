"""Shared enum types loaded from enums_config.yaml + closed code enums.

Open enums (provider/model/language/voice) are loaded from YAML so a new
model or voice can be added without touching Python. Closed enums are
declared as Python Enum classes — every value verified against runtime code.
"""
from __future__ import annotations
from enum import Enum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import AfterValidator

# ---------------------------------------------------------------------------
# Load open-enum values from config
# ---------------------------------------------------------------------------

_CFG_PATH = Path(__file__).parent / "enums_config.yaml"
try:
    _CFG: dict = yaml.safe_load(_CFG_PATH.read_text())
except FileNotFoundError as e:
    raise ImportError(f"enums_config.yaml not found at {_CFG_PATH}") from e
except yaml.YAMLError as e:
    raise ImportError(f"enums_config.yaml is malformed: {e}") from e

PROVIDERS: list[str] = _CFG["providers"]
ANTHROPIC_MODELS: list[str] = _CFG["anthropic_models"]
OPENAI_MODELS: list[str] = _CFG["openai_models"]
ALL_CHAT_MODELS: list[str] = ANTHROPIC_MODELS + OPENAI_MODELS

LANGUAGES: list[str] = _CFG["languages"]

RAYA_VOICES: list[dict] = _CFG["raya_voices"]
RAYA_VOICE_IDS: list[str] = [v["voice_id"] for v in RAYA_VOICES]
RAYA_VOICE_LANGUAGE: dict[str, str] = {v["voice_id"]: v["language"] for v in RAYA_VOICES}
RAYA_LANGUAGES: list[str] = sorted({v["language"] for v in RAYA_VOICES})

EMBEDDING_PROVIDERS: list[str] = _CFG["embedding_providers"]


def _make_validator(allowed: list[str], label: str):
    """Create a Pydantic AfterValidator that enforces membership in `allowed`.

    Args:
        allowed: The list of valid string values for the field.
        label: Human-readable field name used in the error message
            (e.g. "provider", "model", "voice_id").

    Returns:
        A function suitable for use as `AfterValidator(...)` that returns
        the value unchanged when valid, or raises ValueError otherwise.
    """
    def check(v: str) -> str:
        if v not in allowed:
            raise ValueError(f"{label} must be one of {allowed}, got {v!r}")
        return v
    return check


ProviderField           = Annotated[str, AfterValidator(_make_validator(PROVIDERS, "provider"))]
ChatModelField          = Annotated[str, AfterValidator(_make_validator(ALL_CHAT_MODELS, "model"))]
LanguageField           = Annotated[str, AfterValidator(_make_validator(LANGUAGES, "language"))]
RayaVoiceIdField        = Annotated[str, AfterValidator(_make_validator(RAYA_VOICE_IDS, "voice_id"))]
RayaLanguageField       = Annotated[str, AfterValidator(_make_validator(RAYA_LANGUAGES, "raya_language"))]
EmbeddingProviderField  = Annotated[str, AfterValidator(_make_validator(EMBEDDING_PROVIDERS, "embedding_provider"))]


# ---------------------------------------------------------------------------
# Closed code enums — every value verified against runtime code support.
# ---------------------------------------------------------------------------

class AgentType(str, Enum):
    transactional = "transactional"
    informational = "informational"
    agentic = "agentic"
    conversational = "conversational"


class TrustQueueBackend(str, Enum):
    """'memory' intentionally excluded — runtime crashes on it."""
    log = "log"
    redis = "redis"
    webhook = "webhook"


class DignityFailAction(str, Enum):
    rewrite = "rewrite"
    flag = "flag"
    skip = "skip"


class ToolType(str, Enum):
    rest_api = "rest_api"
    mcp = "mcp"


class ToolCategory(str, Enum):
    read = "read"
    write = "write"
    identity = "identity"


class StorageMode(str, Enum):
    saved = "saved"
    anonymous = "anonymous"


class PersistentBackend(str, Enum):
    memgraph = "memgraph"
    neo4j = "neo4j"


class SessionFieldType(str, Enum):
    enum = "enum"
    string = "string"
    int_ = "int"
    list_ = "list"


class InstrumentType(str, Enum):
    counter = "counter"
    gauge = "gauge"
    histogram = "histogram"


class SpecialHandler(str, Enum):
    """Wired in agent_core/src/orchestrator.py."""
    hitl = "hitl"
    whatsapp_handoff = "whatsapp_handoff"


class AuthType(str, Enum):
    """'oauth2' excluded — adapter has no oauth2 branch in rest_api.py."""
    none = "none"
    api_key = "api_key"
    bearer = "bearer"


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


class ParamSource(str, Enum):
    agent = "agent"
    static = "static"


class ParamType(str, Enum):
    string = "string"
    integer = "integer"
    number = "number"
    boolean = "boolean"
    array = "array"
    object = "object"


class McpTransport(str, Enum):
    """'stdio' excluded — _SUPPORTED_TRANSPORTS in mcp.py is {sse, streamable_http}."""
    sse = "sse"
    streamable_http = "streamable_http"


class ReengagementChannel(str, Enum):
    """Schema-declared; runtime impl deferred (GH-168)."""
    outbound_call = "outbound_call"
    whatsapp = "whatsapp"
    sms = "sms"


class RoutingOperator(str, Enum):
    eq = "eq"
    not_eq = "not_eq"
    gt = "gt"
    lt = "lt"
    in_ = "in"


class InternalRoute(str, Enum):
    knowledge_engine = "knowledge_engine"


class GuardrailSeverity(str, Enum):
    """Trust layer policy-pack guardrail severity."""
    blocker = "blocker"
    warning = "warning"


class GuardrailFailureMode(str, Enum):
    """Trust layer guardrail failure response. block=refuse, constrain=apply prompt_constraints."""
    block = "block"
    constrain = "constrain"
