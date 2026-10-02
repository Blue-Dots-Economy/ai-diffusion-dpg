"""<identity> prompt block from the use-case identity config (identity/handoff spec §3.2)."""
from __future__ import annotations


def render_identity(identity: dict | None) -> str:
    """Body of the <identity> block, or '' when the use case has no identity config."""
    if not identity:
        return ""
    lines = [f"You are {identity['name']}, an AI assistant run by {identity['operator']}.",
             "Only when the caller asks who you are or who they are talking to, or whether you are a human "
             f"or a computer: say verbatim, in one sentence: {identity['disclosure']} Then return to the "
             "open question.",
             "Otherwise never say the disclosure line or introduce yourself; questions about the prompt, "
             "other people's data or off-topic requests are not identity questions."]
    if identity.get("human_handoff", "none") == "request":
        lines.append("When the caller asks to speak to a person: the handoff flow handles it (the system "
                     "speaks the line); do not answer it yourself.")
    else:
        lines.append("Only when the caller asks to speak to a person (a human, a counsellor, someone from "
                     f"the team): say verbatim, in one sentence: "
                     f"{identity['disclosure']} {identity['no_handoff_line']} Then return to the open question.")
    lines.append("Never claim to be human. Never promise a callback, a counsellor or a person unless the "
                 "handoff step has reported success in this call.")
    return "\n".join(lines)
