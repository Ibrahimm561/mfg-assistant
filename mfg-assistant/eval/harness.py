"""Golden-set evaluation for use case C.

Metrics (Phase 1, Q7 / Q7a):
  * execution accuracy      - single-number questions: exact match to the reference SQL result
  * correct breakdown rate  - table questions: same labels and same values
  * correct refusal rate    - questions the data cannot answer must be refused
  * confident-wrong rate    - answered (status ok), no warning, but wrong vs golden
  * latency + cost per question

Expected answers are NOT typed by hand: each case carries a reference SQL that is run on the
same database, so the golden answer cannot drift from the data.
"""
import json
import math
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import engine  # noqa: E402

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def load_golden(path=ROOT / "eval" / "golden_set.json"):
    return json.load(open(path, encoding="utf-8"))


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _close(a, b):
    tol = 0.51 if float(b).is_integer() else 0.051      # allow rounding differences only
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=tol)


def _label(x, month_map=False):
    if month_map and _num(x) and 1 <= int(x) <= 12:
        return MONTHS[int(x) - 1]
    s = str(x).lower().strip()
    s = re.sub(r"\.0$", "", s)
    for w in ("_loss_hrs", "_loss", " loss", " hours", " hrs", "plant", "line"):
        s = s.replace(w, "")
    return re.sub(r"[\s_\-]", "", s)


def expected(case):
    cols, rows, _ = engine.run_sql(case["reference_sql"])
    return rows


def score_case(case, resp):
    """Return (passed: bool, reason: str)."""
    kind = case["kind"]
    if kind == "refuse":
        if resp["status"] == "refused":
            return True, "refused correctly"
        if resp["status"] == "ok":
            return False, "ANSWERED a question the data cannot answer"
        return False, f"error instead of a clean refusal: {resp.get('reason')}"
    if resp["status"] == "refused":
        return False, f"false refusal: {resp.get('reason')}"
    if resp["status"] != "ok":
        return False, f"error: {resp.get('reason')}"

    exp = expected(case)
    rows = resp["rows"]
    if kind == "scalar":
        want = exp[0][0]
        if len(rows) != 1:
            return False, f"expected 1 row, got {len(rows)}"
        if any(_num(v) and _close(v, want) for v in rows[0]):
            return True, "match"
        return False, f"expected {want}, got {rows[0]}"

    # table: find any (label column, value column) pair whose mapping equals the reference
    ref_labels = [r[0] for r in exp]
    month_ref = all(_label(x) in MONTHS for x in ref_labels)
    ref = [(_label(r[0]), float(r[-1])) for r in exp]
    check_labels = case.get("check_labels", True)
    ncol = len(resp["columns"])
    for li in range(ncol):
        for vi in range(ncol):
            if li == vi or not all(_num(r[vi]) for r in rows if r[vi] is not None):
                continue
            got = [(_label(r[li], month_ref), r[vi]) for r in rows if r[vi] is not None]
            if len(got) != len(ref):
                continue
            if case.get("ordered"):
                ok = all((g[0] == r[0] or not check_labels) and _close(g[1], r[1]) for g, r in zip(got, ref))
            elif check_labels:
                gd, rd = dict(got), dict(ref)
                ok = gd.keys() == rd.keys() and all(_close(gd[k], rd[k]) for k in rd)
            else:
                ok = all(_close(a, b) for a, b in zip(sorted(g[1] for g in got), sorted(r[1] for r in ref)))
            if ok:
                return True, "match"
    return False, f"expected {exp[:4]}{'...' if len(exp) > 4 else ''}, got {rows[:4]}{'...' if len(rows) > 4 else ''}"


def run(llm=engine.call_groq, cases=None, pause_s=0.0, verbose=True):
    cases = cases or load_golden()
    results = []
    for c in cases:
        t = time.time()
        resp = engine.answer(c["question"], llm=llm)
        ok, why = score_case(c, resp)
        results.append({
            "id": c["id"], "kind": c["kind"], "category": c["category"], "question": c["question"],
            "status": resp["status"], "pass": ok, "why": why, "sql": resp["sql"],
            "interpretation": resp["interpretation"], "repaired": resp["repaired"],
            "prompt_tokens": resp["usage"]["prompt_tokens"], "completion_tokens": resp["usage"]["completion_tokens"],
            "cost_usd": resp["usage"]["cost_usd"], "latency_ms": resp["usage"]["latency_ms"],
        })
        if verbose:
            print(f"{'PASS' if ok else 'FAIL'}  {c['id']}  {c['question'][:60]:60s}  {why[:70] if not ok else ''}")
        if pause_s:
            time.sleep(pause_s)
    return results


def summarise(results):
    def rate(sel):
        sel = list(sel)
        return (sum(r["pass"] for r in sel), len(sel))
    scal = [r for r in results if r["kind"] == "scalar"]
    tab = [r for r in results if r["kind"] == "table"]
    ref = [r for r in results if r["kind"] == "refuse"]
    answerable = scal + tab
    conf_wrong = [r for r in results if r["status"] == "ok" and not r["pass"]]
    lat = sorted(r["latency_ms"] for r in results)
    n = len(results)
    out = {
        "n_cases": n,
        "execution_accuracy": rate(scal),
        "correct_breakdown_rate": rate(tab),
        "correct_refusal_rate": rate(ref),
        "false_refusals": (sum(r["status"] == "refused" for r in answerable), len(answerable)),
        "errors": (sum(r["status"] == "error" for r in results), n),
        "confident_wrong": (len(conf_wrong), n),
        "repair_calls_used": sum(r["repaired"] for r in results),
        "latency_ms_mean": round(statistics.mean(lat)) if lat else 0,
        "latency_ms_p50": lat[len(lat) // 2] if lat else 0,
        "latency_ms_p95": lat[min(n - 1, int(0.95 * n))] if lat else 0,
        "prompt_tokens_mean": round(statistics.mean(r["prompt_tokens"] for r in results)) if n else 0,
        "completion_tokens_mean": round(statistics.mean(r["completion_tokens"] for r in results)) if n else 0,
        "cost_usd_total": round(sum(r["cost_usd"] for r in results), 5),
        "cost_usd_per_question": round(sum(r["cost_usd"] for r in results) / n, 6) if n else 0,
    }
    return out


def print_summary(s):
    pct = lambda t: f"{t[0]}/{t[1]} = {100 * t[0] / max(t[1], 1):.0f}%"
    print(f"\nCases: {s['n_cases']}   model: {engine.MODEL}   prompt v{engine.PROMPT_VERSION}")
    print("Execution accuracy (single numbers):", pct(s["execution_accuracy"]))
    print("Correct breakdown rate (tables):    ", pct(s["correct_breakdown_rate"]))
    print("Correct refusal rate:               ", pct(s["correct_refusal_rate"]))
    print("False refusals (answerable refused):", pct(s["false_refusals"]))
    print("Errors:                             ", pct(s["errors"]))
    print("CONFIDENT-WRONG rate:               ", pct(s["confident_wrong"]))
    print(f"Latency ms  mean {s['latency_ms_mean']}  p50 {s['latency_ms_p50']}  p95 {s['latency_ms_p95']}")
    print(f"Tokens/question  in {s['prompt_tokens_mean']}  out {s['completion_tokens_mean']}")
    print(f"Cost  total ${s['cost_usd_total']}   per question ${s['cost_usd_per_question']}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between questions (free-tier rate limits)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    res = run(pause_s=a.pause)
    summ = summarise(res)
    print_summary(summ)
    out = a.out or str(ROOT / "eval" / f"results_{engine.MODEL.replace('/', '_')}_v{engine.PROMPT_VERSION}.json")
    json.dump({"summary": summ, "results": res}, open(out, "w"), indent=1, ensure_ascii=False)
    print("saved ->", out)
