# agent_core/eval/nlu/run.py
"""CLI: replay labelled cases through one NLU mode, or compare two reports (spec §11)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.nlu.adapters import predict_dialogue_act, predict_intent
from eval.nlu.cases import load_cases
from eval.nlu.offline import OfflineGateway, load_merged_config
from eval.nlu.score import gate, score
from src.chat_provider import build_chat_provider
from src.preprocessing.nlu_processor import NLUProcessor
from src.schema.config import MergedConfig
from src.tool_registry import ToolRegistry
from src.understanding.understander import TurnUnderstander
from src.workflow_loader import AgentWorkflowLoader


def _routed(workflow) -> set[str]:
    rules = [r for s in workflow.subagents.values() for r in s.routing] + list(workflow.global_routing)
    return {r.intent for r in rules if r.intent != "*"}


def _resolves_to(workflow) -> str | None:
    return next((p.resolves_to for s in workflow.subagents.values() for p in s.pending if p.resolves_to), None)


def main(argv: list[str] | None = None) -> int:
    """Run the harness. Exit code 0 on success (and gate pass with --compare), 1 otherwise."""
    ap = argparse.ArgumentParser(prog="eval.nlu.run")
    ap.add_argument("--config", help="domain config dir, e.g. ../dev-kit/configs/blue-dots")
    ap.add_argument("--mode", choices=["intent", "dialogue_act"])
    ap.add_argument("--cases", help="JSONL cases")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", help="write the report JSON here")
    ap.add_argument("--compare", nargs=2, metavar=("INTENT_REPORT", "DA_REPORT"))
    args = ap.parse_args(argv)

    if args.compare:
        a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.compare)
        fails = gate(a, b)
        print("GATE PASS" if not fails else "GATE FAIL\n- " + "\n- ".join(fails))
        return 0 if not fails else 1

    config = load_merged_config(args.config, args.mode)
    MergedConfig.validate_full(config)
    workflow = AgentWorkflowLoader().load(config=config, tool_registry=ToolRegistry(config, OfflineGateway(config)))
    cases = load_cases(args.cases)
    agent_cfg = dict(config.get("agent") or {})
    nlu_cfg = config["preprocessing"]["nlu_processor"]
    provider_cfg = {**agent_cfg, "provider": nlu_cfg.get("provider") or agent_cfg.get("provider"),
                    "primary_model": nlu_cfg.get("model") or agent_cfg.get("primary_model")}
    preds: dict[str, list] = {}
    if args.mode == "dialogue_act":
        provider = build_chat_provider({**provider_cfg, "timeout_ms": nlu_cfg.get("timeout_ms", 2500),
                                        "retry_attempts": nlu_cfg.get("retry_attempts", 2),
                                        "sdk_max_retries": 0, "retry_on_timeout": False})
        und = TurnUnderstander.from_config(config, workflow, provider)
        for c in cases:
            preds[c.id] = [predict_dialogue_act(c, und) for _ in range(args.repeat)]
    else:
        nlu = NLUProcessor(config, chat_provider=build_chat_provider(provider_cfg))
        routed, rt = _routed(workflow), _resolves_to(workflow) or "selected_job_item_id"
        emap = dict(config.get("entity_to_profile_field") or {})
        for c in cases:
            preds[c.id] = [predict_intent(c, nlu, workflow, emap, routed, rt) for _ in range(args.repeat)]
    report = score(cases, preds, args.mode)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
