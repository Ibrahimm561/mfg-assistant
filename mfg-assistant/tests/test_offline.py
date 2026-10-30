"""Offline tests (no API key, no network).  Run:  python -m pytest -q   or   python tests/test_offline.py"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine
from eval import harness

GOLD = harness.load_golden()
BY_Q = {c["question"]: c for c in GOLD}

def oracle(messages):
    """Pretend LLM that always writes the reference SQL (and refuses the refusal cases)."""
    c = BY_Q[messages[-1]["content"]]
    if c["kind"] == "refuse":
        txt = {"answerable": False, "reason": "not in data"}
    else:
        txt = {"answerable": True, "interpretation": "oracle", "sql": c["reference_sql"], "chart": "none"}
    return json.dumps(txt), {"prompt_tokens": 1400, "completion_tokens": 120}

def sabotaged(messages):
    """Pretend LLM that is confidently wrong: shifts every month filter by one, never refuses."""
    c = BY_Q[messages[-1]["content"]]
    sql = c["reference_sql"] or "SELECT ROUND(SUM(cases),0) FROM production"
    sql = sql.replace("month=3", "month=4").replace("month=6", "month=7").replace("plant=1000", "plant=3000")
    return json.dumps({"answerable": True, "interpretation": "x", "sql": sql, "chart": "none"}), \
           {"prompt_tokens": 1400, "completion_tokens": 120}

def test_guard_blocks_attacks():
    bad = ["DROP TABLE production", "SELECT 1; DELETE FROM production", "UPDATE production SET cases=0",
           "SELECT * FROM production -- hi", "SELECT * FROM sqlite_master", "PRAGMA table_info(production)",
           "WITH x AS (SELECT 1) DELETE FROM production", "ATTACH DATABASE 'x' AS y", ""]
    for sql in bad:
        try:
            engine.run_sql(engine.validate_sql(sql)); raise AssertionError(f"NOT BLOCKED: {sql}")
        except (engine.SQLRejected, engine.sqlite3.Error):
            pass

def test_guard_allows_text_search_with_keywords_inside_quotes():
    sql = engine.validate_sql("SELECT COUNT(*) FROM production WHERE fault_detail LIKE '%REPLACE%'")
    assert engine.run_sql(sql)[1][0][0] > 0

def test_oracle_scores_100_on_answerable_and_refusals():
    res = harness.run(llm=oracle, verbose=False)
    s = harness.summarise(res)
    assert s["execution_accuracy"][0] == s["execution_accuracy"][1], [r for r in res if not r["pass"]]
    assert s["correct_breakdown_rate"][0] == s["correct_breakdown_rate"][1], [r for r in res if not r["pass"]]
    assert s["correct_refusal_rate"][0] == s["correct_refusal_rate"][1]
    assert s["confident_wrong"][0] == 0

def test_harness_catches_confidently_wrong_system():
    res = harness.run(llm=sabotaged, verbose=False)
    s = harness.summarise(res)
    assert s["correct_refusal_rate"][0] == 0                     # never refuses
    assert s["confident_wrong"][0] >= 12                         # wrong + no warning is counted
    assert not next(r for r in res if r["id"] == "S02")["pass"]  # month shifted -> must FAIL

def test_malformed_model_output_is_handled():
    r = engine.answer("How many cases in May?", llm=lambda m: ("sorry I cannot", {"prompt_tokens": 1, "completion_tokens": 1}))
    assert r["status"] == "error" and r["rows"] == []

def test_sql_error_triggers_exactly_one_repair():
    calls = []
    def llm(messages):
        calls.append(1)
        sql = "SELECT SUM(casez) FROM production" if len(calls) == 1 else "SELECT ROUND(SUM(cases),0) FROM production"
        return json.dumps({"answerable": True, "interpretation": "t", "sql": sql, "chart": "none"}), {"prompt_tokens": 10, "completion_tokens": 5}
    r = engine.answer("total cases", llm=llm)
    assert r["status"] == "ok" and r["repaired"] and len(calls) == 2 and r["usage"]["prompt_tokens"] == 20

def test_injection_via_model_sql_is_blocked():
    llm = lambda m: (json.dumps({"answerable": True, "interpretation": "", "sql": "DROP TABLE production", "chart": "none"}), {"prompt_tokens": 1, "completion_tokens": 1})
    r = engine.answer("drop it", llm=llm)
    assert r["status"] == "error" and engine.run_sql("SELECT COUNT(*) FROM production")[1][0][0] == 5000

def test_long_question_rejected_without_llm_call():
    r = engine.answer("x" * 1000, llm=lambda m: 1 / 0)
    assert r["status"] == "error"

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok  ", name)
