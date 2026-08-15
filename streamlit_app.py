"""
Triagent -- local Streamlit dashboard.

Paste a log, click Triage, read the report. Runs entirely on your machine
against local Ollama; no API keys, no cloud calls, nothing leaves the laptop.

Run:
    streamlit run streamlit_app.py
"""

import time
from pathlib import Path

import streamlit as st

from triage_agent.config import OLLAMA_CHAT_MODEL, OLLAMA_EMBED_MODEL, OLLAMA_HOST
from triage_agent.triage import triage_log
from triage_agent.vectorstore import count as kb_count

SEVERITY_STYLE = {
    "critical": ("#7f1d1d", "#fecaca", "CRITICAL", "Production outage, data loss risk, or widespread customer impact."),
    "high": ("#7c2d12", "#fed7aa", "HIGH", "Significant customer-facing impact, or likely to escalate."),
    "medium": ("#713f12", "#fde68a", "MEDIUM", "A real bug with bounded or partial impact."),
    "low": ("#14532d", "#bbf7d0", "LOW", "No customer impact, or already auto-resolved."),
}

SAMPLE_LOGS = {
    "-- pick an example --": "",
    "Database: connection pool exhausted": """2026-08-14T09:12:44Z ERROR [payments-api] Connection pool exhausted while acquiring connection
org.postgresql.util.PSQLException: FATAL: sorry, too many clients already
\tat org.postgresql.core.v3.ConnectionFactoryImpl.doAuthentication(ConnectionFactoryImpl.java:693)
\tat com.acme.payments.OrderRepository.save(OrderRepository.java:88)
Impact: checkout write path failing for ~35% of production requests over the last 6 minutes, no fallback configured.""",
    "Network: upstream API timeout": """2026-08-14T14:22:07Z ERROR [checkout-service] Payment authorization request failed
java.net.SocketTimeoutException: Read timed out after 30000ms calling https://api.stripe.com/v1/charges
\tat com.acme.checkout.PaymentClient.authorize(PaymentClient.java:142)
Retries exhausted after 3 attempts. Checkout failing for all EU-region users for the last 12 minutes. No fallback processor configured.""",
    "Edge case: scary keyword, harmless reality": """2026-08-14T03:04:11Z FATAL [monitoring-agent] Disk usage threshold exceeded: 91% on /dev/sda1
Environment: staging-2 (non-production, no customer traffic)
Note: threshold was misconfigured to 90% during last week's migration; actual provisioned capacity is 2TB with 180GB free.
No customer impact. No action required outside business hours.""",
    "Python app crash": """2026-08-14T11:47:02Z ERROR [recommendation-pipeline] Batch scoring job failed
Traceback (most recent call last):
  File "/app/pipeline/score.py", line 214, in run_batch
    features = self._build_features(user_rows)
  File "/app/pipeline/features.py", line 88, in _build_features
    return np.stack([r.embedding for r in rows])
ValueError: all input arrays must have the same shape
Job aborted after processing 12,400 of 480,000 users. Recommendations serving stale results from cache.""",
}


st.set_page_config(page_title="Triagent", page_icon="🩺", layout="wide")


def severity_badge(severity: str) -> str:
    bg, fg, label, blurb = SEVERITY_STYLE.get(severity, ("#374151", "#e5e7eb", severity.upper(), ""))
    return f"""
    <div style="background:{bg};color:{fg};padding:14px 18px;border-radius:10px;margin-bottom:6px;">
      <div style="font-size:26px;font-weight:700;letter-spacing:0.5px;">{label}</div>
      <div style="font-size:13px;opacity:0.9;">{blurb}</div>
    </div>
    """


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("How it works")
    st.markdown(
        """
1. **Parse** — regex pulls out timestamp, level, service, exception, stack frames.
2. **Severity** — a keyword rule *and* the LLM both judge it. The LLM wins when
   they disagree, because it reads context the keywords can't see.
3. **Root cause** — the LLM names the likely cause and which layer it starts in.
4. **Similar incidents** — the log is embedded and matched against past
   incidents, bringing back how each was fixed.
5. **Learn** — the new incident is saved, so the knowledge base grows.
        """
    )
    st.divider()
    st.header("Running locally")
    st.markdown(
        f"""
- **Chat model:** `{OLLAMA_CHAT_MODEL}`
- **Embeddings:** `{OLLAMA_EMBED_MODEL}`
- **Ollama:** `{OLLAMA_HOST}`

No API keys. Nothing leaves this machine.
        """
    )
    try:
        st.metric("Past incidents in knowledge base", kb_count())
    except Exception:
        st.warning("Knowledge base unreachable. Run `python scripts/build_index.py`.")


# ---------------------------------------------------------------- header
st.title("🩺 Triagent")
st.caption(
    "Paste an error log or stack trace. You get severity, likely root cause, and "
    "similar past incidents with the fixes that worked — all computed locally."
)

choice = st.selectbox("Start from an example, or paste your own below:", list(SAMPLE_LOGS.keys()))
raw_log = st.text_area(
    "Log / stack trace",
    value=SAMPLE_LOGS[choice],
    height=240,
    placeholder="2026-08-14T09:12:44Z ERROR [payments-api] ...",
)

col_a, col_b = st.columns([1, 4])
with col_a:
    go = st.button("Triage this log", type="primary", use_container_width=True)
with col_b:
    remember = st.checkbox(
        "Save to knowledge base", value=True,
        help="Adds this incident to the index so future logs can match against it.",
    )

st.info(
    f"Heads-up: each triage runs a local LLM on CPU, so expect **1–3 minutes**. "
    f"Currently using `{OLLAMA_CHAT_MODEL}` — a smaller model is much faster.",
    icon="⏳",
)

# ---------------------------------------------------------------- run
if go:
    if not raw_log.strip():
        st.error("Paste a log first.")
        st.stop()

    started = time.perf_counter()
    with st.spinner("Parsing, classifying severity, reasoning about root cause, searching past incidents..."):
        try:
            report = triage_log(raw_log, persist=remember)
        except Exception as exc:
            st.error(f"Triage failed: {exc}")
            st.caption("Is Ollama running? Try `ollama serve`, and check the models are pulled.")
            st.stop()
    elapsed = time.perf_counter() - started

    sev = report.severity
    st.divider()
    st.subheader("Triage report")

    left, right = st.columns([1, 2])

    with left:
        st.markdown(severity_badge(sev.severity), unsafe_allow_html=True)
        if sev.overridden:
            st.warning(
                f"The keyword rule said **{sev.rule_severity}**, the LLM overrode it to "
                f"**{sev.llm_severity}** after reading the full context.",
                icon="🔁",
            )
        elif sev.rule_severity:
            st.caption(f"Keyword rule agreed: `{sev.rule_severity}`")
        if sev.llm_confidence is not None:
            st.caption(f"Model confidence: {sev.llm_confidence:.0%}")

    with right:
        p = report.parsed
        c1, c2, c3 = st.columns(3)
        c1.metric("Service", p.get("service") or "unknown")
        c2.metric("Level", p.get("level") or "unknown")
        c3.metric("Exception", p.get("exception_type") or "unknown")
        st.markdown("**Why this severity**")
        st.write(sev.reasoning or "_no reasoning returned_")

    st.divider()

    if report.root_cause:
        rc = report.root_cause
        st.subheader(f"Likely root cause · `{rc.layer}` layer")
        st.write(rc.root_cause)
        st.progress(min(max(rc.confidence, 0.0), 1.0), text=f"Confidence {rc.confidence:.0%}")
    else:
        st.subheader("Likely root cause")
        st.write("_Unavailable — the LLM could not be reached._")

    st.divider()
    st.subheader("Similar past incidents")

    if report.similar_incidents:
        for m in report.similar_incidents:
            header = (
                f"{m['similarity']:.0%} match · {m['error_type']} on {m['service']} "
                f"· was {str(m['severity']).upper()}"
            )
            with st.expander(header, expanded=bool(m.get("resolution"))):
                if m.get("resolution"):
                    st.markdown("**How it was fixed**")
                    st.write(m["resolution"])
                else:
                    st.caption("No resolution recorded for this incident yet.")
                if m.get("raw_log"):
                    st.code(m["raw_log"][:1200], language="log")
    else:
        st.info("No similar incidents found — this may be a novel issue.", icon="🆕")

    st.divider()
    footer = f"Completed in {elapsed:.0f}s · report id `{report.log_id}`"
    if remember:
        footer += " · saved to the knowledge base"
    st.caption(footer)

    with st.expander("Raw report (JSON)"):
        st.json(report.to_dict())
