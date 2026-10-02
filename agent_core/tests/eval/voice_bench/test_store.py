from eval.voice_bench.records import CallRecord
from eval.voice_bench.store import ResultStore


def _rec(run=0):
    return CallRecord(target="M3", target_commit="8b39427", scenario="T01", run=run, phone="919900001000",
                      suite_version=1, seed_version=1, caller_model="m", judge_model="m", legs=[], attempts=1,
                      voided=False, error=None)


def test_save_has_load(tmp_path):
    st = ResultStore(tmp_path)
    assert not st.has("8b39427", "T01", 0)
    st.save(_rec())
    assert st.has("8b39427", "T01", 0)
    assert st.load("8b39427", "T01", 0) == _rec()
    assert st.path("8b39427", "T01", 0).parent.name == "8b39427"
    assert "suite-v1" in str(st.path("8b39427", "T01", 0))


def test_partial_file_is_not_a_cache_hit(tmp_path):
    st = ResultStore(tmp_path)
    p = st.path("8b39427", "T01", 1)
    p.parent.mkdir(parents=True)
    p.write_text('{"target": "M3", ', encoding="utf-8")       # killed mid-write
    assert not st.has("8b39427", "T01", 1)


def test_load_target_and_meta(tmp_path):
    st = ResultStore(tmp_path)
    st.save(_rec(0)); st.save(_rec(1))
    assert [r.run for r in st.load_target("8b39427")] == [0, 1]
    st.write_meta("8b39427", {"name": "M3", "unmeasurable": None})
    assert st.read_meta("8b39427")["name"] == "M3"
    assert st.read_meta("nope") == {}


def test_harness_error_record_is_not_a_cache_hit(tmp_path):
    """I1: a record the harness failed (caller LLM down) is re-run, not treated as done."""
    st = ResultStore(tmp_path)
    r = _rec()
    r.harness_error = "caller_llm_APITimeoutError"
    st.save(r)
    assert not st.has("8b39427", "T01", 0)
    assert st.load("8b39427", "T01", 0).harness_error == "caller_llm_APITimeoutError"


def test_non_object_json_is_not_a_cache_hit(tmp_path):
    st = ResultStore(tmp_path)
    p = st.path("8b39427", "T01", 0)
    p.parent.mkdir(parents=True)
    p.write_text("[1, 2]", encoding="utf-8")
    assert not st.has("8b39427", "T01", 0)
