"""Result records for one benchmarked call (spec §6.4) and test-case verdicts (spec §3)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

VERDICT_STATUSES = ("pass", "fail", "n/a", "unscored", "error")


@dataclass(frozen=True)
class Verdict:
    status: str
    quote: str | None = None
    reason: str = ""
    turn: int | None = None

    def __post_init__(self) -> None:
        if self.status not in VERDICT_STATUSES:
            raise ValueError(f"verdict status {self.status!r} not in {VERDICT_STATUSES}")


@dataclass(frozen=True)
class TapEntry:
    t_ms: int                 # epoch ms when the request reached the tap
    method: str
    path: str
    query: str
    req_body: Any
    status: int
    resp_body: Any
    upstream: str             # "signals" | "search"

    @property
    def tool(self) -> str:
        """Which Blue Dots tool this upstream request belongs to."""
        if self.path.endswith("/v1/search"):
            return "fetch_jobs"
        if self.path.endswith("/api/v1/action/perform"):
            return "apply_job"
        if self.path.endswith("/api/v1/admin/participant"):
            return "fetch_profile" if self.method == "GET" else "save_profile"
        return "other"


@dataclass
class TurnRecord:
    idx: int
    caller: str
    reply: str
    status_phrase: str | None
    t_first_content_ms: int | None
    t_first_reply_ms: int | None
    t_total_ms: int | None
    session: dict
    tap: list[TapEntry]
    banner: dict
    session_ended: bool
    error: str | None
    terminal_word: str | None = None  # M0 "Thank you" / M1+ "धन्यवाद" stripped from the end of `reply`

    @property
    def is_tool_turn(self) -> bool:
        """A turn that called at least one Blue Dots tool upstream."""
        return any(t.tool != "other" for t in self.tap)


@dataclass
class Leg:
    call_id: str
    turns: list[TurnRecord]
    ended_by: str             # bot | caller | max_turns | error
    error: str | None = None  # leg-level error not tied to a bridge turn (e.g. caller LLM failure)


@dataclass
class CallRecord:
    target: str
    target_commit: str
    scenario: str
    run: int
    phone: str
    suite_version: int
    seed_version: int
    caller_model: str
    judge_model: str
    legs: list[Leg]
    attempts: int
    voided: bool
    error: str | None
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    started_at: str = ""
    void_reason: str | None = None
    harness_error: str | None = None   # the harness (caller LLM), not the target, failed; never a cache hit
    prior_legs: list[Leg] = field(default_factory=list)   # the discarded first attempt (retry or void re-run)
    prior_error: str | None = None     # that attempt's error, if it was retried because of one

    def to_json(self) -> str:
        """Serialise (Devanagari kept as-is)."""
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, s: str) -> "CallRecord":
        """Inverse of to_json."""
        d = json.loads(s)
        legs = [_leg(lg) for lg in d.pop("legs")]
        prior = [_leg(lg) for lg in d.pop("prior_legs", None) or []]
        verdicts = {k: Verdict(**v) for k, v in d.pop("verdicts").items()}
        return cls(legs=legs, verdicts=verdicts, prior_legs=prior, **d)


def _leg(lg: dict) -> Leg:
    return Leg(call_id=lg["call_id"], ended_by=lg["ended_by"], error=lg.get("error"),
               turns=[TurnRecord(**{**t, "tap": [TapEntry(**x) for x in t["tap"]]}) for t in lg["turns"]])


def bot_replies(rec: CallRecord) -> list[str]:
    """Every non-empty bot reply in call order (status phrases excluded)."""
    return [t.reply for lg in rec.legs for t in lg.turns if t.reply]
