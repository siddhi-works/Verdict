import json


from agents.clause_reader import merge_extractions, read_clauses_llm, read_clauses_regex
from agents.number_words import find_numbers
from agents.quote_verifier import verify_extraction
from core.rules_ir import Candidate, ClauseExtraction, enumerate_readings


def vals(text):
    return [v for v, _, _ in find_numbers(text)]


def test_number_words_and_digits():
    assert vals("ninety-nine percent") == [99.0]
    assert vals("twenty-four hours and 12 hours") == [24.0, 12.0]
    assert vals("ninety-nine point five percent") == [99.5]
    assert vals("one hundred") == [100.0]
    assert vals("$2,000,000") == [2000000.0]


def read(text):
    return read_clauses_regex({"contract_id": "T", "contract_text": text})


def test_regex_reads_words_days_and_scope():
    e = read("Fleet uptime shall be ninety-nine percent. Each Critical Event shall be restored within two days.")
    assert [c.value for c in e.uptime_candidates] == [99.0]
    assert [c.value for c in e.resolution_candidates] == [48.0]
    assert e.scope_severities == ["critical"]


def test_regex_separates_response_time_from_repair_time():
    e = read("The Provider shall respond to each Critical Event within one hour and shall restore operational status within 12 hours.")
    assert [c.value for c in e.resolution_candidates] == [12.0]


def test_regex_ignores_dispute_and_termination_days():
    e = read("Uptime shall be at least 98%. Faults shall be repaired within 8 hours. Disputes seek resolution within 30 days. "
             "Either party may terminate on 30 days notice to remedy the breach.")
    assert [c.value for c in e.resolution_candidates] == [8.0]


def test_regex_marks_governing_value_only_when_same_sentence_says_so():
    same = read("Faults shall be repaired within 12 hours. Notwithstanding Section 3, repairs shall be completed within 24 hours and this Addendum shall prevail.")
    gov = [c.value for c in same.resolution_candidates if c.governs]
    assert gov == [24.0]
    apart = read("Faults shall be repaired within 12 hours. Repairs are due within 24 hours. In the event of conflict between Section 3 and Addendum B, Addendum B shall prevail.")
    assert not any(c.governs for c in apart.resolution_candidates)  # cannot tell which one: stays a conflict


def test_business_days_is_vague_not_a_number():
    e = read("Uptime shall be 98%. Each Critical Event shall be restored within two business days.")
    assert e.resolution_candidates == [] and e.vague_terms


def test_boilerplate_precedence_and_promptly_are_ignored():
    e = read("Uptime shall be 98%. Repairs within 12 hours. This Agreement is the entire agreement and supersedes all prior understandings. "
             "The Customer shall promptly notify the Provider of changes.")
    assert e.precedence_quote is None and e.vague_terms == []


# ---- quote verifier


def test_verifier_rejects_invented_numbers_and_missing_quotes():
    text = "Fleet uptime shall be 98%. Repairs within 12 hours."
    ext = ClauseExtraction(contract_id="T",
                           uptime_candidates=[Candidate(value=98, quote="Fleet uptime shall be 98%."),
                                              Candidate(value=99.9, quote="Fleet uptime shall be 98%.")],
                           resolution_candidates=[Candidate(value=6, quote="Repairs must be done within 6 hours.")])
    clean, rejected = verify_extraction(ext, text)
    assert [c.value for c in clean.uptime_candidates] == [98]
    assert clean.resolution_candidates == []
    assert len(rejected) == 2


def test_verifier_accepts_days_converted_to_hours():
    text = "Critical Events are restored within 2 days."
    ext = ClauseExtraction(contract_id="T", resolution_candidates=[Candidate(value=48, quote=text)])
    assert verify_extraction(ext, text)[0].resolution_candidates


def test_verifier_drops_unbacked_penalty_rate():
    ext = ClauseExtraction(contract_id="T", penalty_usd_per_point=500, penalty_quote="no such sentence")
    clean, rejected = verify_extraction(ext, "Something else entirely.")
    assert clean.penalty_usd_per_point is None and rejected


# ---- enumeration


def test_conflict_without_precedence_fans_out_and_with_precedence_collapses():
    cands = [Candidate(value=12, quote="a 12 hours"), Candidate(value=24, quote="b 24 hours")]
    en = enumerate_readings(ClauseExtraction(contract_id="T", resolution_candidates=cands, uptime_candidates=[Candidate(value=98, quote="98%")]))
    assert len(en.readings) == 2
    gov = [cands[0], cands[1].model_copy(update={"governs": True})]
    en2 = enumerate_readings(ClauseExtraction(contract_id="T", resolution_candidates=gov, precedence_quote="b prevails",
                                              uptime_candidates=[Candidate(value=98, quote="98%")]))
    assert len(en2.readings) == 1 and en2.readings[0].rules.max_resolution_hours == 24


def test_vague_wording_alongside_a_number_keeps_an_unchecked_option():
    ext = ClauseExtraction(contract_id="T", uptime_candidates=[Candidate(value=98, quote="98%")],
                           resolution_candidates=[Candidate(value=720, quote="x 720")],
                           vague_terms=["Faults shall be addressed in a timely manner."])
    thresholds = {r.rules.max_resolution_hours for r in enumerate_readings(ext).readings}
    assert thresholds == {720, None}


def test_merge_is_a_union_and_never_drops_a_reading():
    a = ClauseExtraction(contract_id="T", resolution_candidates=[Candidate(value=12, quote="q")])
    b = ClauseExtraction(contract_id="T", resolution_candidates=[Candidate(value=12, quote="q"), Candidate(value=24, quote="r")])
    assert sorted(c.value for c in merge_extractions(a, b).resolution_candidates) == [12, 24]


# ---- AI reader with a fake client (no network)


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kw):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        msg = type("M", (), {"content": reply})()
        return type("R", (), {"choices": [type("Ch", (), {"message": msg})()]})()


def test_llm_reader_self_heals_after_bad_json():
    good = json.dumps({"contract_id": "T", "uptime_candidates": [{"value": 98, "quote": "98%"}]})
    client = FakeClient(["not json at all", good])
    ext = read_clauses_llm({"contract_id": "T", "contract_text": "x"}, max_retries=3, client=client)
    assert not ext.extractor_failed and client.calls == 2 and ext.uptime_candidates[0].value == 98


def test_llm_reader_fails_safe_and_pipeline_escalates():
    from core.pipeline import PipelineConfig
    from tests.helpers import CLEAN, audit, contract, telemetry
    client = FakeClient([RuntimeError("rate limited")] * 3)
    cfg = PipelineConfig(mode="llm", llm_client=client)
    result = audit(contract("T", CLEAN), telemetry("T", [{"up": 99.5, "res": 2}]), cfg)
    assert result.status == "ESCALATED" and "extractor failed" in result.reason


def test_llm_invented_threshold_is_discarded_before_it_can_matter():
    from core.pipeline import PipelineConfig
    from tests.helpers import CLEAN, audit, contract, telemetry
    # the AI "finds" a 6 hour limit that is not in the contract; the real one is 12
    reply = json.dumps({"contract_id": "T",
                        "uptime_candidates": [{"value": 98, "quote": "Monthly fleet uptime shall be maintained at not less than 98%."}],
                        "resolution_candidates": [{"value": 6, "quote": "Critical Events must be fixed within 6 hours."}]})
    cfg = PipelineConfig(mode="llm", llm_client=FakeClient([reply]))
    result = audit(contract("T", CLEAN), telemetry("T", [{"up": 99.5, "res": 9}]), cfg)
    assert result.status == "COMPLIANT"  # 9h is fine under the real 12h clause
    assert any("rejected" in t for t in result.execution_trace)


def test_merge_handles_credit_tiers_with_missing_bounds():
    from core.schemas import CreditTier
    rows = [CreditTier(max_uptime=99.0, min_uptime=95.0, credit_percent=10, quote="a"),
            CreditTier(max_uptime=95.0, credit_percent=100, quote="b")]  # open-ended lower tier: min_uptime is None
    a = ClauseExtraction(contract_id="T", credit_tiers=rows)
    same = merge_extractions(a, ClauseExtraction(contract_id="T", credit_tiers=list(reversed(rows))))
    assert len(same.credit_tiers) == 2
    differ = merge_extractions(a, ClauseExtraction(contract_id="T", credit_tiers=[rows[0]]))
    assert differ.credit_tiers == [] and any("credit table" in n for n in differ.notes)
