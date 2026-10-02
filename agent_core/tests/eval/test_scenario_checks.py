from eval.scenarios.checks import foreign_script_words, run_checks

M = ["पच्चीस से बीस हज़ार रुपये", "पंद्रह से अठारह हज़ार रुपये", "तीस हज़ार रुपये"]


def test_clean_reply_passes():
    r = run_checks("आपके लिए जॉब्स हैं — पहला: वेल्डर, फ्लिपकार्ट। किसके बारे में जानना चाहेंगे?",
                   {"offered_markers": M})
    assert r["first_job_is_option_1"] is None
    assert all(v is not False for v in r.values()), r


def test_each_check_fails_on_its_defect():
    assert run_checks("सैलरी 25000 है", {})["no_digits"] is False
    assert run_checks("**ध्यान दें**", {})["no_markdown"] is False
    assert run_checks("पाँच नौकरियाँ मिली हैं", {})["no_job_count"] is False
    assert run_checks("एक पल रुकिए", {})["no_wait_phrase"] is False


def test_job_count_variants():
    for bad in ("तीन जॉब हैं", "कुछ नौकरियाँ उपलब्ध हैं", "three jobs", "I found a few openings"):
        assert run_checks(bad, {})["no_job_count"] is False, bad
    assert run_checks("पहला: वेल्डर", {})["no_job_count"] is True
    assert run_checks("पहला जॉब वेल्डर है, दूसरा फिटर", {})["no_job_count"] is True


def test_order_in_order():
    r = f"पहला, सैलरी {M[0]}। दूसरा, {M[1]}।"
    assert run_checks(r, {"offered_markers": M})["first_job_is_option_1"] is True


def test_order_reranked():
    r = f"{M[1]} वाला पहला, फिर {M[0]}"
    assert run_checks(r, {"offered_markers": M})["first_job_is_option_1"] is False


def test_order_only_row_one():
    assert run_checks(f"एक जॉब, {M[1]}", {"offered_markers": M})["first_job_is_option_1"] is False


def test_order_no_marker_is_skipped():
    assert run_checks("कोई और बात", {"offered_markers": M})["first_job_is_option_1"] is None
    assert run_checks("कोई और बात", {})["first_job_is_option_1"] is None


def test_foreign_script_words():
    assert foreign_script_words("QUESS CORP में जॉब") == 2
