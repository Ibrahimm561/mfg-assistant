# Plant floor Q&A — use case C (AI manufacturing reporting)

Ask production questions in English, Urdu or Roman Urdu. A Groq-hosted model writes **one read-only SQL query**;
the app runs it on the production data and shows the number **plus the query that produced it**.
Not an agent: fixed pipeline, at most 2 model calls per question.

```
public/index.html   front end (single file, no build step)
app.py              FastAPI backend, Vercel entrypoint  (/api/ask, /api/overview, /api/feedback, /api/health)
engine.py           schema, prompt, Groq call, SQL guard, execution, token+cost accounting
data/production.csv cleaned + ANONYMISED data (this is what gets deployed)
scripts/prepare_data.py   raw CSV -> data/production.csv (cleaning + anonymisation + leak check)
eval/               golden_set.json (39 cases) + harness.py (scoring)
tests/              offline tests, no key needed
notebook.ipynb      the working record for Phase 2
HONESTY.md          the one-page honesty section
```

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # then put your key in it, or:  export GROQ_API_KEY=gsk_...
python tests/test_offline.py                              # 8 offline tests, no key needed
uvicorn app:app --reload                                  # open http://127.0.0.1:8000
```
(Windows PowerShell: `$env:GROQ_API_KEY="gsk_..."`.)

## Evaluate
```bash
python -m eval.harness --pause 10      # free tier: ~10 s between questions. Use --pause 0 on a paid key.
PROMPT_VERSION=1 python -m eval.harness --pause 10     # compare prompt v1 vs v2
```
or run the cells in `notebook.ipynb`.

## Deploy to Vercel
Streamlit cannot run on Vercel (it needs a long-lived server process), so this is FastAPI + a static page, which Vercel supports natively.
1. Push this folder to a **new GitHub repo** (the `private/` raw data is git-ignored; check `git status` shows no raw file).
2. vercel.com → *Add New → Project* → import the repo. Framework is auto-detected (FastAPI). No build command.
3. *Settings → Environment Variables*: add `GROQ_API_KEY` (Production **and** Preview). Redeploy.
4. Open `https://<project>.vercel.app/api/health` → expect `"key_configured": true`.
5. Open the root URL and ask a question. Put the URL in the notebook, the video, and Colaraz.

CLI alternative: `npm i -g vercel && vercel --prod`, then add the env var in the dashboard and redeploy.

**Protect the key:** in the Groq console set a spend/usage limit. The app has a per-IP limit (15 questions / 10 min), a daily cap, a 300-character question limit, and an answer cache, but those live in memory per server instance.

## Using the real (non-anonymised) data
`ANONYMISE=0 python scripts/prepare_data.py` keeps real names. **Do not deploy that file** while the data is confidential.
