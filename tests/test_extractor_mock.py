"""
tests/test_extractor_mock.py

Covers the deterministic, offline stand-in for the LLM extraction call
(agents.extractor._mock_extract) using small inline contract snippets that
each isolate one extraction rule (simple numeric, percent-before-keyword
ordering, ambiguous phrasing, conflicting resolution windows). The full
50-contract / 504-row production dataset (contracts.json,
telemetry_logs.csv) is exercised end-to-end by `python harness.py --mock`,
not by this unit-test file.
"""
from agents.extractor import _mock_extract, extract_sla_with_retry


def test_extracts_simple_numeric_contract():
    item = {
        "contract_id": "C1",
        "raw_text": (
            "Vendor guarantees a minimum fleet uptime of 99.5% measured monthly, "
            "and commits to resolving any reported vehicle fault within 4 hours "
            "of ticket creation."
        ),
    }
    rules = _mock_extract(item)
    assert rules.required_uptime_percent == 99.5
    assert rules.max_resolution_hours == 4
    assert rules.conflict_detected is False
    assert rules.ambiguity_detected is False


def test_extracts_percent_before_keyword_ordering():
    # "99.8% uptime" (number BEFORE the keyword) must resolve same as
    # "uptime of 99.8%" (number AFTER the keyword).
    item = {
        "contract_id": "C5",
        "raw_text": "Provider guarantees 99.8% uptime and a maximum resolution time of 3 hours.",
    }
    rules = _mock_extract(item)
    assert rules.required_uptime_percent == 99.8
    assert rules.max_resolution_hours == 3


def test_flags_ambiguous_qualitative_language():
    item = {
        "contract_id": "C3",
        "raw_text": (
            "The Vendor shall provide fleet support in a timely, industry-standard "
            "manner and shall make best efforts to keep vehicles operational."
        ),
    }
    rules = _mock_extract(item)
    assert rules.required_uptime_percent is None
    assert rules.max_resolution_hours is None
    assert rules.ambiguity_detected is True
    assert rules.ambiguity_notes  # non-empty explanation


def test_flags_conflicting_resolution_windows():
    item = {
        "contract_id": "C4",
        "raw_text": (
            "The Vendor shall resolve any critical fault within 6 hours of ticket "
            "creation, with a required uptime of 99.2%. ADDENDUM B: the Vendor's "
            "resolution window for critical faults shall be 12 hours."
        ),
    }
    rules = _mock_extract(item)
    assert rules.conflict_detected is True
    assert rules.required_uptime_percent == 99.2  # unambiguous field still extracted
    assert "12" in rules.ambiguity_notes and "6" in rules.ambiguity_notes


def test_mock_flag_short_circuits_without_api_key(monkeypatch):
    # extract_sla_with_retry(mock=True) must never touch _get_client()/OpenAI,
    # so it works with no OPENAI_API_KEY set at all.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    item = {"contract_id": "C1", "raw_text": "Uptime of 99.5%, resolution within 4 hours."}
    rules = extract_sla_with_retry(item, mock=True)
    assert rules.required_uptime_percent == 99.5


def test_sla_mock_mode_env_var_is_honored(monkeypatch):
    monkeypatch.setenv("SLA_MOCK_MODE", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    item = {"contract_id": "C1", "raw_text": "Uptime of 99.5%, resolution within 4 hours."}
    rules = extract_sla_with_retry(item)  # mock param left as default (None)
    assert rules.required_uptime_percent == 99.5
