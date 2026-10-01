"""
agent_core/src/understanding — the dialogue-act NLU (NLU dialogue-acts spec).

Pure turn-understanding pipeline: pending question → frame → one strict-JSON
LLM call → post-processing → write plan. No I/O except the LLM call; the
orchestrator applies the returned writes.

Belongs to the Agent Core DPG block.
"""
