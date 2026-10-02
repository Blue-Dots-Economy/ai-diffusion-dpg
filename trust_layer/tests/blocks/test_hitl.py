import pytest
from trust_layer.src.blocks.hitl import HiTLBlock


@pytest.fixture
def block():
    return HiTLBlock({
        "trust": {
            "hitl": {
                "queue_backend": "log",
                "holding_message": "Aapki baat ek advisor tak pahunch rahi hai.",
                "notification_webhook": None,
            }
        }
    })


# ── normal ────────────────────────────────────────────────────────────────
def test_escalate_returns_queued(block):
    result = block.escalate(
        session_id="s1",
        escalation_reason="escalation_topic:suicide",
        user_message="main bahut pareshaan hoon",
        workflow_step="ready",
    )
    assert result["queued"] is True
    assert result["holding_message"] == "Aapki baat ek advisor tak pahunch rahi hai."
    assert result["ticket_id"].startswith("TKT-")


def test_ticket_id_unique(block):
    r1 = block.escalate("s1", "reason", "msg", "ready")
    r2 = block.escalate("s2", "reason", "msg", "ready")
    assert r1["ticket_id"] != r2["ticket_id"]


# ── edge ──────────────────────────────────────────────────────────────────
def test_empty_user_message_still_queues(block):
    result = block.escalate("s1", "reason", "", "ready")
    assert result["queued"] is True


def test_none_session_raises(block):
    with pytest.raises(ValueError):
        block.escalate(None, "reason", "msg", "ready")


# ── failure: missing config ───────────────────────────────────────────────
def test_missing_hitl_config():
    block = HiTLBlock({})
    result = block.escalate("s1", "reason", "msg", "ready")
    assert result["queued"] is True
    assert result["holding_message"] == ""


def test_unsupported_queue_backend_is_not_queued():
    block = HiTLBlock({
        "trust": {
            "hitl": {
                "queue_backend": "redis",
                "holding_message": "Waiting for support...",
            }
        }
    })
    result = block.escalate("s1", "reason", "msg", "ready")
    assert result["queued"] is False
    assert result["ticket_id"].startswith("TKT-")


# ── delivery semantics ────────────────────────────────────────────────────
def _cfg(backend="log"):
    return {"trust": {"hitl": {"queue_backend": backend, "holding_message": "hold", "notification_webhook": None}}}


def test_log_backend_is_queued_but_not_delivered():
    r = HiTLBlock(_cfg("log")).escalate("s1", "human_request", "msg", "job_match", handoff={"a": 1})
    assert r["queued"] is True and r["delivered"] is False and r["reason"] == "log_only"
    assert r["ticket_id"].startswith("TKT-")


def test_unsupported_backend_is_not_queued():
    r = HiTLBlock(_cfg("redis")).escalate("s1", "human_request", "msg", "job_match")
    assert r["queued"] is False and r["delivered"] is False and r["reason"] == "unsupported_backend"


def test_payload_is_never_logged(caplog):
    caplog.set_level("DEBUG")
    HiTLBlock(_cfg("log")).escalate("s1", "human_request", "msg", "job_match",
                                    handoff={"caller": {"phone": "919900001000"}})
    assert caplog.records
    assert "919900001000" not in caplog.text
    assert all("919900001000" not in str(r.__dict__) for r in caplog.records)
