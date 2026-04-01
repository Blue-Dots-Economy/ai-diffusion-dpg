"""
agent_core/main.py

Startup entrypoint for the Agent Core orchestration service.

Responsibilities:
- Load config from config/config.yaml
- Instantiate ClaudeLLMWrapper with agent config
- Create HTTP clients for Memory Layer, Trust Layer, Learning Layer, Knowledge Engine,
  and Action Gateway
- Wire ToolRegistry, AgentWorkflowLoader, ManagerAgent, and AgentCore
- Create the FastAPI orchestration app via create_orchestration_app()
- Start the uvicorn HTTP server on port 8000

Run:
    python -m main                    (from agent_core/ directory)
    uvicorn main:app --reload         (dev hot-reload)

Environment:
    ANTHROPIC_API_KEY must be set. ClaudeLLMWrapper reads it from the environment
    via the Anthropic SDK -- never hardcoded here.

Prerequisites (all must be running before this starts):
    memory_layer/main.py     (port 8002)
    trust_layer/main.py      (port 8003)
    learning_layer/main.py   (port 8004)
    knowledge_engine/main.py (port 8001)
    action_gateway/main.py   (port 9999)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv

# Load .env before anything reads ANTHROPIC_API_KEY from the environment.
# Has no effect if .env does not exist (safe in production where the var is
# injected by the orchestrator / secrets manager directly).
load_dotenv()

from src.llm_wrapper.claude_wrapper import ClaudeLLMWrapper
from src.http_clients.knowledge_engine import HttpKnowledgeEngineClient
from src.http_clients.memory_layer import MemoryLayerHttpClient
from src.http_clients.trust_layer import TrustLayerHttpClient
from src.http_clients.learning_layer import LearningLayerHttpClient
from src.http_clients.action_gateway import ActionGatewayHttpClient
from src.tool_registry import ToolRegistry
from src.manager_agent import ManagerAgent
from src.orchestrator import AgentCore
from src.workflow_loader import AgentWorkflowLoader
from src.servers.orchestration_server import create_orchestration_app

# ---------------------------------------------------------------------------
# Logging -- structured output, INFO level default
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base. Override values win. Dicts are merged recursively."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# App construction -- exposed at module level for uvicorn --reload
# ---------------------------------------------------------------------------


def _build_app():
    dpg_config = _load_config("config/dpg.yaml")
    domain_config = _load_config("config/domain.yaml")
    config = _deep_merge(dpg_config, domain_config)

    agent_cfg = config.get("agent", {})

    llm = ClaudeLLMWrapper(agent_cfg)

    memory   = MemoryLayerHttpClient(config)
    trust    = TrustLayerHttpClient(config)
    learning = LearningLayerHttpClient(config)
    ke       = HttpKnowledgeEngineClient(config)
    gateway  = ActionGatewayHttpClient(config)

    # Tool Registry -- built from gateway's tool definitions
    tool_registry = ToolRegistry(config=config, gateway=gateway)

    # Workflow Loader -- parse and validate agent_workflow block at startup
    workflow = AgentWorkflowLoader().load(config=config, tool_registry=tool_registry)

    manager = ManagerAgent(
        llm_wrapper=llm,
        tool_registry=tool_registry,
        action_gateway=gateway,
        knowledge_engine=ke,
        trust_layer=trust,
        max_tool_rounds=agent_cfg.get("max_tool_rounds", 1),
    )

    # Agent Core -- central orchestrator
    agent_core = AgentCore(
        config=config,
        llm_wrapper=llm,
        memory=memory,
        trust=trust,
        knowledge_engine=ke,
        tool_registry=tool_registry,
        manager_agent=manager,
        learning=learning,
        workflow=workflow,
    )

    model_name = llm.get_active_model()

    # FastAPI app
    app = create_orchestration_app(agent_core)

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 8000)

    logger.info(
        "agent_core.startup",
        extra={
            "operation": "main.startup",
            "status": "success",
            "host": host,
            "port": port,
            "model": model_name,
            "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.version,
        },
    )

    return app, host, port


app, _host, _port = _build_app()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=_host,
        port=_port,
        log_level="info",
    )
