"""NLU worker: runs inside a target's uv env; writes the score report as JSON.

``src.*`` is imported only inside ``main()``, after ``--agent-core`` is put first on ``sys.path``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Score the target's NLU on the given cases; stdout carries ASCII status only."""
    ap = argparse.ArgumentParser(prog="eval.voice_bench.nlu_worker")
    ap.add_argument("--agent-core", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--cases", nargs="*", default=[])
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(args.agent_core).resolve()))
    from eval.nlu.adapters import predict_dialogue_act, predict_intent
    from eval.nlu.cases import load_cases
    from eval.nlu.offline import OfflineGateway, load_merged_config
    from eval.nlu.score import score
    from eval.voice_bench.nlu import pick_adapter
    from src.chat_provider import build_chat_provider
    from src.schema.config import MergedConfig
    from src.tool_registry import ToolRegistry
    from src.workflow_loader import AgentWorkflowLoader

    config = load_merged_config(args.config, agent_core_root=args.agent_core)
    validate = getattr(MergedConfig, "validate_full", None)
    if validate:
        validate(config)
    workflow = AgentWorkflowLoader().load(config=config, tool_registry=ToolRegistry(config, OfflineGateway(config)))
    cases = [c for path in args.cases for c in load_cases(path)]
    agent_cfg = dict(config.get("agent") or {})
    nlu_cfg = config["preprocessing"]["nlu_processor"]
    provider_cfg = {**agent_cfg, "provider": nlu_cfg.get("provider") or agent_cfg.get("provider"),
                    "primary_model": nlu_cfg.get("model") or agent_cfg.get("primary_model")}
    adapter = pick_adapter(Path(args.agent_core))
    preds: dict[str, list] = {}
    if adapter == "dialogue_act":
        from src.understanding.understander import TurnUnderstander
        provider = build_chat_provider({**provider_cfg, "timeout_ms": nlu_cfg.get("timeout_ms", 2500),
                                        "retry_attempts": nlu_cfg.get("retry_attempts", 2),
                                        "sdk_max_retries": 0, "retry_on_timeout": False})
        und = TurnUnderstander.from_config(config, workflow, provider)
        for c in cases:
            preds[c.id] = [predict_dialogue_act(c, und) for _ in range(args.repeat)]
    else:
        from src.preprocessing.nlu_processor import NLUProcessor
        nlu = NLUProcessor(config, chat_provider=build_chat_provider(provider_cfg))
        rules = [r for s in workflow.subagents.values() for r in getattr(s, "routing", []) or []] + list(getattr(workflow, "global_routing", []) or [])
        routed = {r.intent for r in rules if r.intent != "*"}
        resolves_to = next((p.resolves_to for s in workflow.subagents.values()
                            for p in getattr(s, "pending", []) or [] if getattr(p, "resolves_to", None)),
                           None) or "selected_job_item_id"
        emap = config.get("entity_to_profile_field") or {}
        for c in cases:
            preds[c.id] = [predict_intent(c, nlu, workflow, emap, routed, resolves_to) for _ in range(args.repeat)]
    report = score(cases, preds)
    Path(args.out).write_text(json.dumps({"adapter": adapter, "report": report, "n_cases": len(cases)},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"nlu_worker ok adapter={adapter} cases={len(cases)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
