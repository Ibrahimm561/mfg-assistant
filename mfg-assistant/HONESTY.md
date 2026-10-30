## 9. Honesty section

**What this is.** A question-to-SQL assistant over a 5,000-row extract of one year (2018) of one plant group's production records, with brand and plant names anonymised. It is a prototype of Phase 1 use case C, not the SAP HANA system described there.

### What the system gets wrong
* **It can run the wrong query and look right.** The arithmetic is always correct (the app runs the SQL), but the question answered may not be the one asked. The only protection is that the query is shown; a plant manager who does not read SQL cannot check it. My confident-wrong rate on the golden set was **[FILL: x/39 = y%]**; every one of those had a number on screen and no warning.
* **Free text is the weakest spot.** Stoppage reasons are typed by people with spelling variants, and anonymisation replaced a utility name in that text, so keyword counts ("power failure") will not match what the real data would give. Failure found in testing: **[FILL: case ID, what happened, what v2 changed, v1→v2 score]**. The fix teaches one pattern I already knew about; my golden case for it was written by me, so the pass overstates how well it generalises.
* **"Line 2" is ambiguous** (it exists in several plants) and **"last month" has no calendar** (the data stops at 31 Dec 2018). The system states its interpretation, but it is guessing.
* **Data assumptions I made, not the file:** `?` in loss columns means "no stoppage" (set to 0); the `ELE` column is utilities (inferred); 14.4% of records have no quantity and are silently excluded from totals (the app prints this on every answer, but a total is still a lower bound). Eight loss cells corrupted into dates were nulled.
* **Urdu and Roman Urdu** were tested on **[FILL: n]** questions only. I cannot claim a rate.

### What I would not yet put in front of a customer
* Anything where a wrong number drives a shift or management decision, without a second check of the SQL by someone who reads it.
* **No access control.** Phase 1 says access follows each user's SAP role; here anyone with the link sees everything.
* **Not tested against real SAP data, real load, or hostile users.** Rate limits and the answer cache live in memory per server instance, so on Vercel they are best-effort, not a guarantee. The query log is in `/tmp` and disappears; it is also printed to Vercel's logs.
* **Refusal depends on the model following written rules.** There is no separate classifier. A persistent user could probably get an approximate "efficiency" figure by asking for the ingredients of one. Scrap, OEE, other shifts and other years are refused because the data does not contain them, not because they are blocked.
* **The golden set is small and mine.** 39 cases written by the person who built the prompt. It measures regressions, not real-world accuracy; I would want 200+ questions from actual plant staff.
* Data left Pakistan: the question and table definition go to Groq's API (US-hosted). Phase 1 flagged this as the condition that would turn the verdict into No-go if localisation were required. The data rows never leave the server.

### What it cost
* **Model:** `openai/gpt-oss-120b` on Groq, list price $0.15 in / $0.60 out per million tokens. Measured: **[FILL: mean tokens in/out per question, cost per question]**; the full 39-question eval cost **[FILL: $]**. Projected **[FILL: $]** per 10,000 questions a month. Cached-token discounts are ignored.
* **Latency:** mean **[FILL] ms**, p95 **[FILL] ms** per question.
* **Hosting:** Vercel Hobby plan and Groq free tier: $0 while demo traffic stays small. The free tier's tokens-per-minute limit is the real constraint: a handful of simultaneous markers could hit it. I added a per-IP limit, a daily cap, and an answer cache for repeated questions to protect the budget.
* **My time:** **[FILL: hours]**, plus AI assistance in writing the code.
