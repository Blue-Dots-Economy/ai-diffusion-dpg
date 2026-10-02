"""Human handoff payload and spoken line (identity/handoff spec §4–§5). Pure functions; session data only."""
from __future__ import annotations

import json

CAP = 300


def _cap(v: object) -> str:
    return str(v or "")[:CAP]


def _recent(raw: object, n: int) -> list[dict]:
    try:
        turns = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (ValueError, TypeError):
        return []
    if not isinstance(turns, list):
        return []
    return [{"caller": _cap(t.get("caller")), "bot": _cap(t.get("bot"))}
            for t in turns[-n:] if isinstance(t, dict)]


def build_handoff_payload(*, ticket_hint: str, use_case: str, session: dict, phone: str, call_id: str,
                          last_caller_turn: str, summary_turns: int, now_iso: str) -> dict:
    """Handoff JSON for the webhook. ``ticket_hint`` is unused by Agent Core (Trust assigns the ticket)."""
    apps = []
    if str(session.get("last_application_id") or ""):
        apps.append({"application_id": _cap(session.get("last_application_id")), "status": "submitted"})
    return {
        "use_case": use_case, "reason": "human_request", "created_at": now_iso, "call_id": call_id,
        "caller": {"phone": phone, "name": _cap(session.get("name")),
                   "language": _cap(session.get("detected_language") or "")},
        "context": {"step": _cap(session.get("current_subagent_id")), "last_caller_turn": _cap(last_caller_turn),
                    "trade": _cap(session.get("stored_trade") or session.get("trade")),
                    "location": _cap(session.get("stored_location") or session.get("location")),
                    "applications": apps},
        "summary": _recent(session.get("recent_turns"), summary_turns),
    }


def choose_handoff_line(result: dict | None, lines: dict, already: bool) -> tuple[str, str]:
    """(line, outcome) — outcome is delivered | failed | already."""
    if already:
        return lines["already"], "already"
    if result and result.get("delivered") is True:
        return lines["delivered"], "delivered"
    return lines["failed"], "failed"
