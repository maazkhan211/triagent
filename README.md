# Triagent

**Paste an error log. Get back how bad it is, what probably caused it, and
whether this has happened before — and how it was fixed.**

A local-first triage agent for error logs and stack traces.
*Triage + agent = Triagent.*

Built for the job SRE/DevOps teams actually do at 3am: something is broken,
there's a wall of stack trace, and the first question is always *"is this
serious, and have we seen it before?"*

**Everything runs locally.** The LLM and the embedding model are served by
[Ollama](https://ollama.com) on your own machine. There are **no API keys and no
cloud calls anywhere in this project** — nothing you paste ever leaves your
laptop, and it costs nothing to run.

---

## What you get

```
🟠 HIGH                    Service: checkout-service    Exception: SocketTimeoutException

Why this severity
  Significant customer-facing impact — affects all EU-region users and prevents
  them from checking out, with no working fallback in place.
  ⚠ The keyword rule said "critical"; the LLM overrode it after reading context.

Likely root cause · network layer                                  confidence 80%
  The Stripe API is not responding within the expected time limit.

Similar past incidents
  90% match · ConnectionPoolExhausted on checkout-service · was CRITICAL
      ↳ How it was fixed: pool max size (10) was undersized for peak traffic
        after a campaign spike. Bumped HikariCP maximumPoolSize to 50 and added
        an alert on pool utilization > 80%.
  89% match · TimeoutError on checkout-service · was HIGH
      ↳ How it was fixed: missing index on orders(customer_id, created_at) meant
        a full table scan on a 40M-row table. Added composite index; p99 dropped
        from 15s to 40ms.
```

The last section is the point. Anyone can ask an LLM "what does this error
mean" — the value here is **matching against what your team already solved**.

---

## Quick start

```bash
# 1. install
python -m venv .venv
.venv\Scripts\activate              # Windows  (use source .venv/bin/activate on Mac/Linux)
pip install -r requirements.txt

# 2. get the local models (one-time, ~2GB)
ollama pull llama3.2:3b
ollama pull nomic-embed-text

# 3. build the knowledge base of past incidents (one-time)
python scripts/generate_synthetic_data.py
python scripts/build_index.py

# 4. run it
streamlit run streamlit_app.py
```

That opens the dashboard at <http://localhost:8501>. Pick one of the built-in
example logs, click **Triage this log**, and watch it work.

---

## The two ways to use it

### 1. Dashboard — for a human with a log in hand

```bash
streamlit run streamlit_app.py
```

A paste-box, a colour-coded severity badge, the reasoning behind it, the root
cause with a confidence bar, and expandable cards for each similar past
incident. Four example logs are built in, so you can try it without hunting for
a real stack trace.

### 2. HTTP API — for when no human is watching

```bash
uvicorn api.main:app --port 8000
```

Then open <http://localhost:8000/docs> for an interactive UI where you can try
every endpoint in the browser.

**This is the more useful one, and here's why:** with a dashboard (or a chat
bot), *a human still has to notice the error and paste it in*. That's the
slowest step in the whole loop. The API removes it — your alerting stack posts
the log the moment it fires:

```
Sentry / Grafana Alertmanager / Datadog / your log shipper
        │  POST the alert
        ▼
   /webhook  →  triage runs automatically  →  saved to the knowledge base
```

| Endpoint | What it does |
|---|---|
| `GET /health` | Liveness, which models are loaded, how many incidents are indexed |
| `POST /triage` | Send `{"raw_log": "..."}`, get the full report as JSON |
| `POST /webhook` | Accepts any alerting tool's JSON, triages in the background, returns `202` immediately |

Try it:

```bash
curl -X POST http://localhost:8000/triage ^
  -H "Content-Type: application/json" ^
  -d "{\"raw_log\": \"ERROR [payments-api] PSQLException: too many clients already\"}"
```

`/webhook` deliberately returns **202 Accepted** straight away instead of
waiting for the report. A triage takes ~50 seconds on CPU, and most alerting
tools time out well before that and then *retry*, which would give you duplicate
triages. It also doesn't care which tool sent it — it hunts through the common
payload shapes (`message`, `log`, Sentry's `event.message`, Alertmanager's
`alerts[0].annotations.description`) and falls back to the whole body.

### Also: the CLI

```bash
python scripts/demo_cli.py path/to/log.txt
python scripts/demo_cli.py --paste
```

---

## How it works

```
raw log / stack trace
      │
      ▼
 log_parser.py    →  regex pulls out timestamp, level, service, exception type,
      │               and stack frames. No LLM — this part is deterministic.
      ▼
 analyze.py       →  ONE LLM call returns severity AND root cause together.
      │               A keyword rule runs alongside it for comparison; when the
      │               two disagree, the LLM wins (see below).
      ▼
 vectorstore.py   →  the log is embedded with nomic-embed-text and matched
      │               against past incidents in ChromaDB by cosine similarity,
      │               bringing back their resolution notes.
      ▼
 triage.py        →  assembles the TriageReport, then writes this incident back
                      into the index — so the knowledge base grows every time.
```

Why regex instead of `drain3` for parsing: drain3 does online *template mining*,
which pays off when you're clustering thousands of near-identical lines in a
stream. Here every input is a single one-off incident, so plain regex is more
precise and drops a dependency.

---

## Why rules + LLM, not just rules

A keyword classifier (`FATAL` → critical, `retry` → low) is instant and free,
but **context-blind**. It cannot tell a real outage from a misconfigured alert
on a staging box, because both contain the word `FATAL`.

So the agent runs **both**, and lets the LLM override the rule when they
disagree — showing you both verdicts so the override is visible, never silent.

`scripts/eval_severity.py` measures this on 8 deliberately adversarial logs
(`data/eval_edge_cases.json`) written so a naive keyword rule gets fooled:

| Classifier | Accuracy on edge cases |
|---|---|
| Rule-based only | **0 / 8 (0%)** |
| LLM | **5 / 8 (62.5%)** |

Example — the rule sees `FATAL` and screams *critical*:

> `FATAL [monitoring-agent] Disk usage threshold exceeded: 91%`
> `Environment: staging-2 (non-production, no customer traffic)`
> `threshold was misconfigured during last week's migration... No customer impact.`

The LLM correctly returns **low**: *"isolated to a non-production environment
and does not pose any risk to customers or data integrity."*

**Being honest about the 3 it gets wrong:** they're the cases needing a numeric
anomaly weighed against business risk — e.g. a 40x spike in a fraud-check
fail-open path, which the model rates `medium` because the system "fails open
and approves transactions", missing that *that is exactly what makes it
critical*. Full per-case reasoning is in
[`eval_results/severity_eval.md`](eval_results/severity_eval.md).

That's why this is a **triage aid for a human, not an autopilot**.

---

## Performance: 173s → 50s

The first working version took **~3 minutes per report**, which is useless
during an incident. Three changes, each measured:

| Change | Time | Why |
|---|---|---|
| Starting point | **173s** | 2 LLM calls (severity, then root cause) + 2 embeddings |
| Merge into one LLM call | 151s | Both answers come from one pass — saves a model load and a prompt eval |
| Reuse the embedding | ~141s | The same text was being embedded twice per triage |
| Switch to `llama3.2:3b` | **50s** | 3B instead of 8B parameters |

**3.5x faster — and the eval score didn't change** (`llama3.2:3b` also scores
5/8, identical to `llama3.1`). Both eval reports are kept in `eval_results/` so
you can compare.

> **Why it's still ~50s:** Ollama's GPU backend segfaults on this machine
> (`exit status 0xc0000005` on every model tested), so `triage_agent/ollama_client.py`
> forces CPU inference with `"num_gpu": 0`. On a machine with working GPU
> offload, remove that option and expect a very large speedup.

To trade accuracy for more speed, or the reverse, just change one line in `.env`:

```
OLLAMA_CHAT_MODEL=llama3.2:3b     # fast (default)
OLLAMA_CHAT_MODEL=llama3.1        # 8B, slower, same eval score here
```

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running (`ollama serve`, or the desktop app)
- Two models pulled: `llama3.2:3b` and `nomic-embed-text`

No accounts, no keys, no billing.

---

## Project layout

```
streamlit_app.py     the dashboard  (streamlit run streamlit_app.py)
api/
  main.py            FastAPI app: /health, /triage, /webhook
triage_agent/
  config.py          reads .env
  log_parser.py      regex log parsing (no LLM)
  ollama_client.py   local Ollama chat (JSON mode) + embeddings
  analyze.py         combined severity + root cause in ONE call  ← used by the app
  severity.py        rule-based pass + isolated LLM pass         ← used by the eval
  root_cause.py      isolated root-cause prompt + result types
  vectorstore.py     ChromaDB similarity index over past incidents
  triage.py          orchestrates the pipeline into a TriageReport
scripts/
  generate_synthetic_data.py   builds data/historical_incidents.json
  build_index.py               embeds + indexes it  (--reset to wipe first)
  demo_cli.py                  run a triage from the terminal
  eval_severity.py             the rules-vs-LLM accuracy eval
data/
  historical_incidents.json    59 past incidents with resolution notes
  eval_edge_cases.json         8 adversarial cases for the eval
eval_results/
  severity_eval.md             latest eval (llama3.2:3b)
  severity_eval_llama3.1.md    same eval on llama3.1, for comparison
tests/
  test_log_parser.py           parser unit tests
```

**Why `analyze.py` and `severity.py` both exist:** the app uses the merged
one-call prompt because it's twice as fast. The eval uses the isolated severity
prompt, because if the model were also reasoning about root cause in the same
breath, the eval would no longer be measuring the severity classifier on its
own. Different jobs, different prompts, on purpose.

---

## Housekeeping

Every triage saves itself into the index, which is the point — the knowledge
base grows. But after a demo you'll have near-duplicates of your own test logs
showing up as spurious ~100% matches. Reset to just the curated incidents:

```bash
python scripts/build_index.py --reset
```

Run the tests and the eval:

```bash
python -m pytest tests/ -q
python scripts/eval_severity.py
```

---

## Known limitations

- **~50s per triage** on CPU. Fine for a webhook, slow for a human waiting.
- **5/8 on adversarial edge cases.** A decent assistant, not a decision-maker.
- **The knowledge base is synthetic.** 59 generated incidents with realistic
  resolution notes. Pointing it at real closed tickets is what would make it
  genuinely useful.
- **New incidents are saved with an empty `resolution`.** There's no UI yet for
  an engineer to come back and fill in how it was actually fixed — that's the
  most valuable next feature.
