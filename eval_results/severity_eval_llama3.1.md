# Severity Classifier Eval: Rules vs LLM on Edge Cases

- Cases: 8
- Rule-based accuracy: **0/8 (0.0%)**
- LLM-based accuracy: **5/8 (62.5%)**
- Improvement: **+62.5 points**

These cases were deliberately built so that a naive keyword rule (e.g. `FATAL` -> critical, `retry` -> low) is misled by a keyword that doesn't match the real-world severity once you read the full context (environment, blast radius, whether it self-recovered).

| Case | True | Rule | Rule OK? | Matched keyword | LLM | LLM OK? | Why rules fail |
|---|---|---|---|---|---|---|---|
| EDGE-01 | low | critical | ❌ | `fatal` | low | ✅ | Keyword 'FATAL' triggers a critical rule, but context (staging, misconfigured threshold, no customer impact) makes this a non-issue. |
| EDGE-02 | critical | high | ❌ | `timeout` | high | ❌ | Keywords 'retry' and 'retrying' typically map to low severity, but the full context (retries exhausted, checkout failing for all EU users, no fallback) is a critical outage. |
| EDGE-03 | low | high | ❌ | `error` | low | ✅ | Keyword 'ERROR' plus '500' looks alarming, but 'deprecated' + 'succeeded on 2nd attempt' + 'zero data loss' means this is a low-priority cleanup item, not an incident. |
| EDGE-04 | low | critical | ❌ | `oom` | low | ✅ | Keyword 'OOM' strongly maps to critical, but log level is INFO and the surrounding context describes an auto-recovered, non-customer-facing batch job. |
| EDGE-05 | low | medium | ❌ | `warn` | low | ✅ | Contains 'deprecated' which a naive rule already gets right by luck, but also demonstrates that surrounding context ('succeeded normally', 'expected') confirms low severity rather than an error. |
| EDGE-06 | critical | high | ❌ | `error` | medium | ❌ | A rule sees 'timeout' -> critical by luck here, but a shallow rule could also read 'approved' and downgrade it; the real signal that makes this critical is the anomalous spike (40x baseline) correlated with fraud risk, which requires reasoning over the whole log, not a keyword. |
| EDGE-07 | low | medium | ❌ | `warn` | low | ✅ | Rule sees 'retry exhausted' as a strong critical/high signal, but this is a non-transactional marketing email with a graceful fallback and no user-facing impact. |
| EDGE-08 | medium | high | ❌ | `error` | low | ❌ | Rule sees bare 'ERROR' + exception name and may default to high/critical, but the order itself succeeded (payment went through) and only a non-critical side-effect (email) failed for a bounded subset of traffic -- this is a real but medium-priority bug, not an outage. |

## LLM reasoning per case

**EDGE-01** (true: low, LLM said: low)
> The issue is isolated to a non-production environment and does not pose any risk to customers or data integrity.

**EDGE-02** (true: critical, LLM said: high)
> The failure has significant customer-facing impact as it's causing checkouts to fail for all users in the EU region, and there is no fallback path configured.

**EDGE-03** (true: low, LLM said: low)
> The error was in a deprecated endpoint, and the system automatically retried with backoff, succeeding without data loss; this is an expected behavior before the endpoint's scheduled sunset.

**EDGE-04** (true: low, LLM said: low)
> The OOM killer invocation was for a non-customer-facing, short-lived process that automatically recovered without any impact on users or data.

**EDGE-05** (true: low, LLM said: low)
> The log indicates an expected behavior for a small percentage of accounts, with no customer impact or degradation.

**EDGE-06** (true: critical, LLM said: medium)
> The issue has a partial impact on payment processing, but the system is designed to fail-open and approve transactions without a fraud score, limiting immediate customer-facing consequences.

**EDGE-07** (true: low, LLM said: low)
> The log indicates a non-critical warning with a working fallback plan in place, and no customer-facing impact is mentioned.

**EDGE-08** (true: medium, LLM said: low)
> The order was still processed successfully, and only a small percentage of guest checkouts are affected, indicating minimal customer impact.