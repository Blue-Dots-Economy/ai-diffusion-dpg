"""Seed dataset v1: payload builders and the watermark cleanup / snapshot / restore SQL (ruling 5).

All SQL here is built from validated ids, a validated timestamp, or values read back from the DB snapshot.
Literals are escaped by doubling single quotes.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

SEED_VERSION = 1

_SEED_FILE = Path(__file__).parent / "seed" / f"v{SEED_VERSION}.json"
_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

# Enum values from bluedots-schemas blue_dot/up-gzb/network.json job_posting_1.0 (the backend's schema, U1).
_EXPERIENCE_YEARS = "< 1 Year"
_EXPERIENCE_TYPE = {True: "Fresher", False: "Worked before"}


def load_seed() -> dict:
    """Return the parsed seed/v1.json."""
    return json.loads(_SEED_FILE.read_text(encoding="utf-8"))


def job_rows(seed: dict) -> list[dict]:
    """60 deterministic job postings: one per (trade, city).

    Returns:
        Rows of ``{"phone", "name", "item_state"}``. ``phone`` is the poster's (reserved range, unique per job),
        ``name`` the employer, ``item_state`` a job_posting_1.0 payload. up-gzb job_posting_1.0 has
        ``additionalProperties: false`` and no ``title``; ``jobCategory`` is set only for trades whose category
        enum value clearly fits (seed ``job_category``), and ``typeOfJob`` is omitted (no clean per-trade fit).
    """
    rows = []
    employers = seed["employers"]
    for i, trade in enumerate(seed["trades"]):
        for j, city in enumerate(seed["cities"]):
            salary_min = 9000 + 1000 * ((i + 2 * j) % 8)
            employer = employers[(i * 3 + j) % len(employers)]
            rows.append({
                "phone": f"9199000{80 + i:02d}{j}00",
                "name": employer,
                "item_state": {
                    "jobProviderName": employer,
                    "jobProviderLocation": f"{city}",
                    "role": trade["role"],
                    "positions": 1 + (i + j) % 4,
                    "natureOfJob": "Apprenticeship" if (i + j) % 5 == 0 else "Full-time",
                    "hiringManagerName": "HR Desk",
                    "hiringManagerPhoneNumber": f"+91990008{i}{j}00",
                    "salaryMin": salary_min,
                    "salaryMax": salary_min + 4000,
                    "workExperienceYears": _EXPERIENCE_YEARS,
                    "candidateExperienceType": _EXPERIENCE_TYPE[(i + j) % 2 == 0],
                    **({"jobCategory": trade["job_category"]} if trade.get("job_category") else {}),
                },
            })
    return rows


def places(seed: dict) -> dict[str, list[str]]:
    """Canonical city -> spoken aliases (Devanagari and Latin)."""
    return {city: list(aliases) for city, aliases in seed["places"].items()}


def profile_item_state(profile: dict) -> dict:
    """The profile_1.0 item_state a seed profile is POSTed with, in save_profile's field names.

    The seed file's ``trade`` maps to ``nameOfJobRolesInterestedIn``; ``phone`` and ``age`` are added because
    profile_1.0 requires them to go live. An age-less profile stays draft.
    """
    src = profile["item_state"]
    st = {"name": src["name"], "location": src["location"], "phone": profile["phone"]}
    if profile.get("age") is not None:
        st["age"] = profile["age"]
    if src.get("trade"):
        st["nameOfJobRolesInterestedIn"] = src["trade"]
    return st


def participant_body(kind: str, phone: str, name: str, item_state: dict, age: int | None = None) -> dict:
    """POST /api/v1/admin/participant body.

    Args:
        kind: ``"seeker"`` (profile_1.0) or ``"provider"`` (job_posting_1.0).
        phone: Digits, country code first, no "+".
        name: Participant display name.
        item_state: Item payload.
        age: Omitted when None.

    Returns:
        The request body. An age-less seeker sends only ``profile_creation`` consent: the seeker domain is
        guardian-gated, so the user-level pair without an age is rejected with 400 AGE_REQUIRED.
    """
    consent = ["profile_creation"] if (kind == "seeker" and age is None) else [
        "user_terms", "user_privacy", "profile_creation"]
    return {"name": name, "phone_number": f"+{phone}", **({"age": age} if age is not None else {}),
            "compliance": [{"key": k, "value": True} for k in consent],
            "channel": "voice", "network": "blue_dot", "domain": kind,
            "item_type": "profile_1.0" if kind == "seeker" else "job_posting_1.0",
            "item_state": item_state}


def _ids(ids: list[str]) -> str:
    bad = [i for i in ids if not isinstance(i, str) or not _ID_RE.fullmatch(i)]
    if bad or not ids:
        raise ValueError("ids must be a non-empty list matching [A-Za-z0-9_-]+")
    return ",".join(f"'{i}'" for i in ids)


def _ts(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"not an ISO timestamp: {value!r}") from None
    return value


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    return "'" + str(v).replace("'", "''") + "'"


def cleanup_sql(watermark: str, keep_user_ids: list[str]) -> str:
    """One transaction deleting everything created after ``watermark``, except the kept users.

    Raises:
        ValueError: watermark is not ISO-8601, or an id is not ``[A-Za-z0-9_-]+``.
    """
    w, keep = _ts(watermark), _ids(keep_user_ids)
    return (
        "BEGIN;\n"
        f"DELETE FROM action_events WHERE created_at > '{w}'::timestamptz;\n"
        f"DELETE FROM item_actions  WHERE created_at > '{w}'::timestamptz;\n"
        f"DELETE FROM consent_record WHERE created_at > '{w}'::timestamptz AT TIME ZONE 'UTC';\n"
        "DELETE FROM item_search s USING items i\n"
        f"  WHERE i.created_at > '{w}'::timestamptz AND s.item_network=i.item_network AND s.item_domain=i.item_domain\n"
        "    AND s.item_type=i.item_type AND s.item_id=i.item_id;\n"
        f"DELETE FROM items WHERE created_at > '{w}'::timestamptz;\n"
        f"DELETE FROM \"user\" WHERE created_at > '{w}'::timestamptz AT TIME ZONE 'UTC' AND id NOT IN ({keep});\n"
        "COMMIT;\n"
    )


def snapshot_sql(user_ids: list[str]) -> str:
    """A query printing one JSON line ``{"items": [...], "users": [...]}`` for the given users.

    Only the columns restore_sql writes back are selected, so the snapshot carries no phone/email.
    """
    ids = _ids(user_ids)
    return (
        "SELECT json_build_object("
        "'items', COALESCE((SELECT json_agg(row_to_json(i)) FROM (SELECT item_id, item_state, item_private_state, "
        f"lifecycle_status, item_locations, updated_at FROM items WHERE created_by IN ({ids})) i), '[]'::json), "
        "'users', COALESCE((SELECT json_agg(row_to_json(u)) FROM (SELECT id, name, updated_at FROM \"user\" "
        f"WHERE id IN ({ids})) u), '[]'::json));\n"
    )


def restore_sql(snapshot: dict) -> str:
    """UPDATE statements putting the snapshotted items and users back. Uses snapshot values only.

    Raises:
        ValueError: a snapshotted item_id is not a UUID or a user id is malformed.
    """
    out = ["BEGIN;"]
    for it in snapshot.get("items") or []:
        item_id = str(uuid.UUID(str(it["item_id"])))
        out.append(f"UPDATE items SET item_state={_lit(it['item_state'])}::jsonb, "
                   f"item_private_state={_lit(it['item_private_state'])}, "
                   f"lifecycle_status={_lit(it['lifecycle_status'])}, "
                   f"item_locations={_lit(it['item_locations'])}::jsonb, updated_at={_lit(it['updated_at'])} "
                   f"WHERE item_id='{item_id}';")
    for u in snapshot.get("users") or []:
        _ids([u["id"]])
        out.append(f"UPDATE \"user\" SET name={_lit(u['name'])}, updated_at={_lit(u['updated_at'])} "
                   f"WHERE id='{u['id']}';")
    out.append("COMMIT;")
    return "\n".join(out) + "\n"
