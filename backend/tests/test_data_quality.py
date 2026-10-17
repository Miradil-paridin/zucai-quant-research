from app.data_quality import _freshness_status


def test_freshness_status_has_explicit_missing_and_warning_states() -> None:
    assert _freshness_status(None, 1, 2) == "fail"
    assert _freshness_status(0.5, 1, 2) == "pass"
    assert _freshness_status(1.5, 1, 2) == "warn"
    assert _freshness_status(3, 1, 2) == "fail"
