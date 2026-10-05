"""Tests for the write plan and NLU-owned precedence."""
from src.understanding.config import DialogueActConfig
from src.understanding.models import ResolvedReference, SlotUpdate, StateWrite
from src.understanding.precedence import PROVENANCE_KEY, nlu_owned_values
from src.understanding.slot_writer import plan_writes
from src.workflow_loader import OptionsFrom, PendingQuestion


def _cfg():
    return DialogueActConfig.from_config({
        "entity_to_profile_field": {"consent": "consent_response"},
        "entity_persistence": {"scope": "session"},
        "preprocessing": {"nlu_processor": {"slots": {
            "consent": {"type": "enum", "values": ["granted", "declined"]},
            "trade": {"type": "string"}, "age": {"type": "int", "min": 14, "max": 80}}}}})


def _plan(**kw):
    base = dict(accepted={}, cfg=_cfg(), state={}, session={}, pending=None, resolved=None,
                extras=(), off_track_count=0)
    base.update(kw)
    return plan_writes(**base)


def test_new_value_written_with_provenance_and_no_update_record():
    writes, updates = _plan(accepted={"consent": "granted"})
    assert StateWrite("session", "consent_response", "granted") in writes
    assert StateWrite("session", PROVENANCE_KEY, ["consent_response"]) in writes
    assert updates == []


def test_correction_over_existing_value_is_an_update():
    writes, updates = _plan(accepted={"trade": "Welder"}, state={"trade": "Electrician"},
                            session={PROVENANCE_KEY: ["trade"]})
    assert StateWrite("session", "trade", "Welder") in writes
    assert updates == [SlotUpdate("trade", "Electrician", "Welder")]
    assert not any(w.key == PROVENANCE_KEY for w in writes)        # unchanged provenance not rewritten


def test_seeded_zero_is_not_an_update():
    _, updates = _plan(accepted={"age": 25}, state={"age": 0})
    assert updates == []


def test_unchanged_value_writes_nothing():
    writes, updates = _plan(accepted={"trade": "Welder"}, state={"trade": "Welder"})
    assert writes == [] and updates == []


def test_resolved_reference_written_to_resolves_to():
    p = PendingQuestion("select_job", options_from=OptionsFrom("fetch_jobs", ("role",), "item_id"),
                        resolves_to="selected_job_item_id")
    writes, _ = _plan(pending=p, resolved=ResolvedReference(1, "j1", "Welder", "item_id"))
    assert StateWrite("session", "selected_job_item_id", "j1") in writes


def test_extras_merge_and_off_track_count_change():
    writes, _ = _plan(extras=(("tool", "drill"),), session={"nlu_extras": {"pet": "dog"}, "off_track_count": 1},
                      off_track_count=2)
    assert StateWrite("session", "nlu_extras", {"pet": "dog", "tool": "drill"}) in writes
    assert StateWrite("session", "off_track_count", 2) in writes


def test_nlu_owned_values_skips_seeds_and_unlisted():
    s = {PROVENANCE_KEY: ["trade", "age", "ghost"], "trade": "Welder", "age": 0, "location": "X"}
    assert nlu_owned_values(s) == {"trade": "Welder"}
    assert nlu_owned_values({}) == {}
    assert nlu_owned_values({PROVENANCE_KEY: "bad"}) == {}


def test_persistent_scope_writes_value_without_provenance():
    cfg = DialogueActConfig.from_config({
        "entity_persistence": {"scope": "persistent"},
        "preprocessing": {"nlu_processor": {"slots": {
            "trade": {"type": "string"}}}}})
    writes, _ = _plan(cfg=cfg, accepted={"trade": "Welder"})
    assert StateWrite("persistent", "trade", "Welder") in writes
    assert not any(w.key == PROVENANCE_KEY for w in writes)
