from eval.scenarios.checks import foreign_script_words, run_checks


def test_clean_reply_passes():
    r = run_checks("आपके लिए जॉब्स हैं — पहला: वेल्डर, फ्लिपकार्ट। किसके बारे में जानना चाहेंगे?",
                   {"spoken_first_label": "वेल्डर, फ्लिपकार्ट"})
    assert all(r.values()), r


def test_each_check_fails_on_its_defect():
    assert run_checks("सैलरी 25000 है", {})["no_digits"] is False
    assert run_checks("**ध्यान दें**", {})["no_markdown"] is False
    assert run_checks("पाँच नौकरियाँ मिली हैं", {})["no_job_count"] is False
    assert run_checks("एक पल रुकिए", {})["no_wait_phrase"] is False


def test_foreign_script_words():
    assert foreign_script_words("QUESS CORP में जॉब") == 2
