"""
agent_core/src/understanding/caller_turn.py

Renders the <caller_turn> body: NLU's structured conclusion for the main LLM
(NLU dialogue-acts spec §6.8). Values here reach only the LLM prompt, like
<known_profile>; never logs.

Belongs to the Agent Core DPG block.
"""
from __future__ import annotations

from src.understanding.models import TurnUnderstanding


def render_caller_turn(u: TurnUnderstanding | None) -> str:
    """Render the <caller_turn> body, or "" when there is nothing to say.

    Args:
        u: This turn's understanding; None or a dialogue-less result
            renders nothing.

    Returns:
        Newline-joined lines; empty lines are omitted.
    """
    if u is None:
        return ""
    if u.fallback_reason:
        return "understanding unavailable this turn"
    d = u.dialogue
    if d is None:
        return ""
    head = f"acts: {', '.join(d.acts)} · relation: {d.relation}"
    if d.topic:
        head += f" · topic: {d.topic}"
    lines = [head]
    if u.pending_id:
        lines.append(f"pending: {u.pending_id} (now answered)" if d.relation == "answers_pending"
                     else f"open: {u.pending_id} — still unanswered")
    if u.resolved:
        r = u.resolved
        lines.append(f"resolved: option {r.option} — {r.label} ({r.id_field} {r.id})")
    if u.unresolved:
        lines.append(f"caller referred to option {u.unresolved.option}; {u.unresolved.offered} offered")
    corrected = "correct" in d.acts
    for up in u.updates:
        lines.append(f"updated: {up.key} {up.old} → {up.new}" + (" (caller corrected)" if corrected else ""))
    for rej in u.rejected_slots:
        if rej.reason.startswith("normalise:"):
            lines.append(f'not accepted: {rej.slot} "{rej.value}" ({rej.reason.split(":", 1)[1]})')
    if u.signals:
        lines.append("signals: " + ", ".join(u.signals))
    if u.off_track_tripped:
        lines.append("off track: several turns in a row — re-ask the open question simply, or offer to end the call")
    return "\n".join(lines)
