"""FastAPI backend (Vercel entrypoint: `app`).  Static front end lives in /public.

Endpoints
  POST /api/ask       {question}  -> answer object (see engine.answer)
  GET  /api/overview              -> fixed KPIs/charts, no LLM, no cost
  POST /api/feedback  {question, sql, verdict}  -> appended to the query log
  GET  /api/health
Protections for a public, key-less demo: per-IP rate limit, daily call cap, 300-char questions,
read-only SQL guard, answer cache for repeated questions.
"""
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import engine

app = FastAPI(title="Manufacturing Q&A", docs_url=None, redoc_url=None)

RATE_LIMIT = int(os.getenv("RATE_LIMIT", "15"))          # questions per IP per window
RATE_WINDOW_S = int(os.getenv("RATE_WINDOW_S", "600"))
DAILY_CAP = int(os.getenv("DAILY_CAP", "1500"))          # LLM calls per server instance per day
_hits = defaultdict(deque)
_cache = {}
_day = {"date": time.strftime("%Y-%m-%d"), "n": 0}
LOG_FILE = Path("/tmp/query_log.jsonl")                  # ephemeral on Vercel; also printed to runtime logs


def _log(event: dict):
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = json.dumps(event, ensure_ascii=False)
    print(line)                                           # visible in Vercel -> Logs
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _client_ip(req: Request):
    return (req.headers.get("x-forwarded-for") or (req.client.host if req.client else "?")).split(",")[0].strip()


class Ask(BaseModel):
    question: str


class Feedback(BaseModel):
    question: str
    sql: str | None = None
    verdict: str            # "up" | "down"
    comment: str | None = None


@app.post("/api/ask")
def ask(body: Ask, req: Request):
    q = body.question.strip()
    key = " ".join(q.lower().split())
    if key in _cache:
        r = dict(_cache[key]); r["cached"] = True
        r["usage"] = {**r["usage"], "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
        return r

    now = time.time()
    ip = _client_ip(req)
    dq = _hits[ip]
    while dq and now - dq[0] > RATE_WINDOW_S:
        dq.popleft()
    if len(dq) >= RATE_LIMIT:
        return JSONResponse(status_code=429, content={
            "status": "error", "reason": f"Rate limit: {RATE_LIMIT} questions per {RATE_WINDOW_S // 60} minutes. Please wait a bit."})
    today = time.strftime("%Y-%m-%d")
    if _day["date"] != today:
        _day.update(date=today, n=0)
    if _day["n"] >= DAILY_CAP:
        return JSONResponse(status_code=503, content={
            "status": "error", "reason": "The demo has reached its daily question budget. Try the Overview tab or come back tomorrow."})
    dq.append(now)
    _day["n"] += 1

    r = engine.answer(q)
    r["cached"] = False
    if r["status"] in ("ok", "refused"):
        _cache[key] = r
        if len(_cache) > 500:
            _cache.pop(next(iter(_cache)))
    _log({"type": "ask", "question": q, "status": r["status"], "sql": r["sql"], "reason": r["reason"],
          "rows": len(r["rows"]), **{k: r["usage"][k] for k in ("prompt_tokens", "completion_tokens", "cost_usd", "latency_ms")}})
    return r


@app.get("/api/overview")
def overview():
    return engine.overview()


@app.post("/api/feedback")
def feedback(fb: Feedback):
    _log({"type": "feedback", "verdict": fb.verdict, "question": fb.question[:300], "sql": fb.sql, "comment": (fb.comment or "")[:300]})
    return {"ok": True}


@app.get("/api/health")
def health():
    engine.load_db()
    return {"ok": True, "model": engine.MODEL, "prompt_version": engine.PROMPT_VERSION,
            "key_configured": bool(os.getenv("GROQ_API_KEY")), "rows": engine.META["rows"]}


# Local dev only (on Vercel the CDN serves /public/index.html itself)
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "public" / "index.html")
