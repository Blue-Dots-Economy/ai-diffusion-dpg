"""<identity> prompt block from the use-case identity config (identity/handoff spec §3.2)."""
from __future__ import annotations


def render_identity(identity: dict | None) -> str:
    """Body of the <identity> block, or '' when the use case has no identity config."""
    if not identity:
        return ""
    lines = [f"You are {identity['name']}, an AI assistant run by {identity['operator']}.",
             f"Disclosure line (say it verbatim, one sentence): {identity['disclosure']}",
             "When the caller asks who you are or who they are talking to, asks whether you are a human or "
             "a computer, or asks to speak to a person: say the disclosure line, then "]
    if identity.get("human_handoff", "none") == "request":
        lines[-1] += ("let the handoff flow handle a request for a person (the system speaks it); "
                      "then return to the open question.")
    else:
        lines[-1] += f"say verbatim: {identity['no_handoff_line']} Then return to the open question."
    lines.append("Never claim to be human. Never promise a callback, a counsellor or a person unless the "
                 "handoff step has reported success in this call.")
    return "\n".join(lines)
