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
| EDGE-06 | critical | high | ❌ | `error` | low | ❌ | A rule sees 'timeout' -> critical by luck here, but a shallow rule could also read 'approved' and downgrade it; the real signal that makes this critical is the anomalous spike (40x baseline) correlated with fraud risk, which requires reasoning over the whole log, not a keyword. |
| EDGE-07 | low | medium | ❌ | `warn` | low | ✅ | Rule sees 'retry exhausted' as a strong critical/high signal, but this is a non-transactional marketing email with a graceful fallback and no user-facing impact. |
| EDGE-08 | medium | high | ❌ | `error` | low | ❌ | Rule sees bare 'ERROR' + exception name and may default to high/critical, but the order itself succeeded (payment went through) and only a non-critical side-effect (email) failed for a bounded subset of traffic -- this is a real but medium-priority bug, not an outage. |

## LLM reasoning per case

**EDGE-01** (true: low, LLM said: low)
> The alert threshold misconfiguration does not pose a risk to customers, and the issue is isolated to a staging environment with no production failover path.

**EDGE-02** (true: critical, LLM said: high)
> The failure to retry and a lack of fallback path indicates a potential issue with the payment gateway or the service's ability to recover from failures, which could impact users in the EU region.

**EDGE-03** (true: low, LLM said: low)
> The deprecated endpoint was intentionally returned a 500 error and automatically retried successfully without data loss, indicating a planned maintenance or deprecation process rather than a critical failure.

**EDGE-04** (true: low, LLM said: low)
> The OOM killer was invoked due to a short-lived batch-resize pod, but it self-recovered and completed successfully without any customer-facing impact or data loss.

**EDGE-05** (true: low, LLM said: low)
> This log message indicates a known, expected behavior in an older user account migration path, and the fact that login succeeded normally suggests minimal impact on users.

**EDGE-06** (true: critical, LLM said: low)
> Although there's an increase in failed fraud checks, it's due to design and doesn't impact customer experience directly; the system has a fallback mechanism in place.

**EDGE-07** (true: low, LLM said: low)
> The notification service is experiencing a temporary issue with sending marketing email campaigns, but it's non-transactional and doesn't impact customer accounts or orders, allowing for a fallback to the next scheduled send window.

**EDGE-08** (true: medium, LLM said: low)
> Although a significant error occurred, it did not prevent the order from being placed or charged successfully, and only a small percentage of affected users are estimated to be impacted.