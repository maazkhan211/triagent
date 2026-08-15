"""
Generates a synthetic historical incident dataset for the Bug/Log Triage Agent's
knowledge base. Each record is a past incident: the raw log/stack trace as it would
have appeared in production, plus the human-written resolution notes that a real
on-call engineer would have logged after fixing it.

Run:
    python scripts/generate_synthetic_data.py
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "historical_incidents.json"

SERVICES = [
    "checkout-service", "payment-gateway", "auth-service", "inventory-service",
    "notification-service", "search-service", "user-profile-service",
    "order-service", "analytics-pipeline", "image-processing-service",
]

# Each template defines a realistic incident "family": how the raw log looks,
# what actually caused it, and how it typically got resolved. severity here is
# the ground-truth label an SRE would assign after full investigation.
TEMPLATES = [
    {
        "error_type": "ConnectionPoolExhausted",
        "layer": "database",
        "severity": "critical",
        "log": (
            "{ts} ERROR [{svc}] db.pool.HikariPool - HikariPool-1 - Connection is not available, "
            "request timed out after 30000ms.\n"
            "java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, "
            "request timed out after 30000ms\n"
            "\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)\n"
            "\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:197)\n"
            "\tat {svc}.repository.OrderRepository.save(OrderRepository.java:88)\n"
            "\tat {svc}.service.CheckoutService.placeOrder(CheckoutService.java:142)"
        ),
        "resolution": (
            "Root cause: connection pool max size (10) was undersized for peak traffic after a "
            "marketing campaign spike. Bumped HikariCP maximumPoolSize to 50 and added a "
            "PagerDuty alert on pool utilization > 80%. Also found a leaked connection in a "
            "batch job that wasn't closing on exception; fixed with try-with-resources."
        ),
    },
    {
        "error_type": "DeadlockDetected",
        "layer": "database",
        "severity": "high",
        "log": (
            "{ts} ERROR [{svc}] db.transaction - Deadlock found when trying to get lock; "
            "try restarting transaction\n"
            "org.springframework.dao.DeadlockLoserDataAccessException: PreparedStatementCallback; "
            "SQL [UPDATE inventory SET qty = qty - ? WHERE sku = ?]; Deadlock found when trying to "
            "get lock; try restarting transaction\n"
            "\tat {svc}.dao.InventoryDao.decrementStock(InventoryDao.java:61)\n"
            "\tat {svc}.service.ReservationService.reserve(ReservationService.java:33)"
        ),
        "resolution": (
            "Two competing transactions were updating inventory rows in different lock orders "
            "during a flash sale. Reordered updates to always acquire locks by ascending SKU id, "
            "and added a bounded retry-with-backoff around the transaction. Deadlocks dropped to "
            "zero after deploy."
        ),
    },
    {
        "error_type": "DNSResolutionFailure",
        "layer": "network",
        "severity": "high",
        "log": (
            "{ts} ERROR [{svc}] http.client - Failed to resolve host: api.partner-shipping.com\n"
            "java.net.UnknownHostException: api.partner-shipping.com\n"
            "\tat java.base/java.net.InetAddress$CachedAddresses.get(InetAddress.java:797)\n"
            "\tat {svc}.client.ShippingClient.getRates(ShippingClient.java:47)\n"
            "\tat {svc}.service.OrderService.calculateShipping(OrderService.java:210)"
        ),
        "resolution": (
            "Upstream partner rotated their DNS records and our internal resolver's cache TTL was "
            "stale for 20 minutes cluster-wide. Not fixable on our side beyond adding a circuit "
            "breaker + fallback shipping estimate so checkout doesn't hard-fail when the partner "
            "API is unreachable."
        ),
    },
    {
        "error_type": "ConnectionRefused",
        "layer": "network",
        "severity": "critical",
        "log": (
            "{ts} FATAL [{svc}] http.client - Connect to payments-internal:8443 failed: "
            "Connection refused\n"
            "java.net.ConnectException: Connection refused\n"
            "\tat java.base/sun.nio.ch.Net.connect0(Native Method)\n"
            "\tat {svc}.client.PaymentClient.charge(PaymentClient.java:19)\n"
            "\tat {svc}.service.CheckoutService.completeCheckout(CheckoutService.java:88)"
        ),
        "resolution": (
            "payments-internal pods were killed by a bad rollout (readiness probe misconfigured, "
            "traffic routed to pods still starting). Rolled back the deploy and fixed the readiness "
            "probe to check an actual DB connection instead of just process liveness."
        ),
    },
    {
        "error_type": "TLSHandshakeFailure",
        "layer": "network",
        "severity": "high",
        "log": (
            "{ts} ERROR [{svc}] http.client - SSL handshake failed: certificate has expired\n"
            "javax.net.ssl.SSLHandshakeException: PKIX path validation failed: "
            "java.security.cert.CertPathValidatorException: certificate has expired\n"
            "\tat {svc}.client.FraudCheckClient.verify(FraudCheckClient.java:55)"
        ),
        "resolution": (
            "Internal mTLS cert for the fraud-check sidecar expired; cert-manager renewal job had "
            "been silently failing for 3 weeks due to a quota limit on the ACME issuer. Renewed "
            "manually and added an alert on cert expiry < 7 days."
        ),
    },
    {
        "error_type": "OutOfMemoryError",
        "layer": "application",
        "severity": "critical",
        "log": (
            "{ts} FATAL [{svc}] jvm - java.lang.OutOfMemoryError: Java heap space\n"
            "java.lang.OutOfMemoryError: Java heap space\n"
            "\tat java.base/java.util.Arrays.copyOf(Arrays.java:3745)\n"
            "\tat {svc}.pipeline.ImageResizer.loadFullResolution(ImageResizer.java:74)\n"
            "\tat {svc}.worker.ResizeWorker.run(ResizeWorker.java:29)\n"
            "Container was OOMKilled by kubelet."
        ),
        "resolution": (
            "Image resize worker was loading full-resolution uploads (up to 80MB) entirely into "
            "memory before downscaling, and a batch of high-res uploads from a single customer "
            "triggered repeated OOMKills. Switched to streaming/tiled decoding and capped max "
            "input dimensions; also raised container memory limit as a secondary safety margin."
        ),
    },
    {
        "error_type": "NullPointerException",
        "layer": "application",
        "severity": "medium",
        "log": (
            "{ts} ERROR [{svc}] app - Unhandled exception while processing request\n"
            "java.lang.NullPointerException: Cannot invoke \"User.getEmail()\" because "
            "\"user\" is null\n"
            "\tat {svc}.service.NotificationService.sendReceipt(NotificationService.java:41)\n"
            "\tat {svc}.controller.OrderController.confirm(OrderController.java:97)"
        ),
        "resolution": (
            "Guest checkout orders don't have a User record, but the receipt-sending path assumed "
            "one always existed. Added a null check and fall back to sending the receipt to the "
            "guest email captured at checkout instead of the User entity."
        ),
    },
    {
        "error_type": "RetryExhausted",
        "layer": "external-api",
        "severity": "medium",
        "log": (
            "{ts} WARN [{svc}] http.client - Retry attempts exhausted (3/3) calling "
            "tax-calculation-api, last status 503\n"
            "\tat {svc}.client.TaxClient.getRate(TaxClient.java:63)\n"
            "\tat {svc}.service.PricingService.applyTax(PricingService.java:22)\n"
            "Falling back to cached tax table (last updated 4h ago)."
        ),
        "resolution": (
            "Upstream tax API had a brief maintenance window. Our fallback-to-cache path worked as "
            "designed, so no customer-visible impact — orders used slightly stale (but valid) tax "
            "rates for ~12 minutes. No code change needed; confirmed cache fallback logic is sound."
        ),
    },
    {
        "error_type": "DeprecatedAPIWarning",
        "layer": "application",
        "severity": "low",
        "log": (
            "{ts} WARN [{svc}] app - Call to /v1/legacy/search is deprecated and will be removed "
            "on 2026-12-01. Client: internal-cron-job. Please migrate to /v2/search.\n"
            "\tat {svc}.controller.LegacySearchController.search(LegacySearchController.java:15)"
        ),
        "resolution": (
            "Informational only — an internal cron job was still calling the deprecated v1 search "
            "endpoint. Filed a low-priority ticket to migrate the cron job before the v1 sunset "
            "date; no immediate action required."
        ),
    },
    {
        "error_type": "RateLimitExceeded",
        "layer": "external-api",
        "severity": "medium",
        "log": (
            "{ts} WARN [{svc}] http.client - 429 Too Many Requests from geolocation-api, "
            "Retry-After: 60s\n"
            "\tat {svc}.client.GeoClient.lookup(GeoClient.java:29)\n"
            "\tat {svc}.service.FraudScoringService.score(FraudScoringService.java:71)"
        ),
        "resolution": (
            "A new batch fraud-rescan feature was calling the geolocation API per-row instead of "
            "batching, blowing through our rate limit tier. Added request batching (100 addresses "
            "per call) which cut call volume by ~95% and stayed well under the limit."
        ),
    },
    {
        "error_type": "StackOverflowError",
        "layer": "application",
        "severity": "high",
        "log": (
            "{ts} ERROR [{svc}] app - java.lang.StackOverflowError\n"
            "\tat {svc}.model.CategoryTree.buildPath(CategoryTree.java:34)\n"
            "\tat {svc}.model.CategoryTree.buildPath(CategoryTree.java:34)\n"
            "\tat {svc}.model.CategoryTree.buildPath(CategoryTree.java:34)\n"
            "\t... 4200 more"
        ),
        "resolution": (
            "A data-entry error created a circular parent-category reference (category A's parent "
            "was set to a descendant of itself), which sent the recursive path-builder into "
            "infinite recursion for any product under that category. Added a cycle check with a "
            "max-depth guard, and fixed the bad row in the categories table."
        ),
    },
    {
        "error_type": "SerializationError",
        "layer": "application",
        "severity": "medium",
        "log": (
            "{ts} ERROR [{svc}] app - Failed to deserialize message from queue "
            "'order.events'\n"
            "com.fasterxml.jackson.databind.exc.UnrecognizedPropertyException: Unrecognized field "
            "\"loyaltyTier\" (class OrderEvent), not marked as ignorable\n"
            "\tat {svc}.consumer.OrderEventConsumer.onMessage(OrderEventConsumer.java:26)"
        ),
        "resolution": (
            "order-service started publishing a new 'loyaltyTier' field before analytics-pipeline's "
            "consumer schema was updated (deploy ordering mistake). Added @JsonIgnoreProperties"
            "(ignoreUnknown = true) as a safety net and updated the deploy runbook to require "
            "consumer-first schema rollout for additive fields."
        ),
    },
    {
        "error_type": "DiskSpaceWarning",
        "layer": "infrastructure",
        "severity": "low",
        "log": (
            "{ts} WARN [{svc}] infra.disk - Disk usage at 71% on volume /data (staging replica), "
            "threshold alert set at 90%\n"
            "No action required; monitoring only."
        ),
        "resolution": (
            "Staging replica disk usage climbing gradually from log retention, well under the 90% "
            "alert threshold. Scheduled log rotation cleanup job; no production impact, not "
            "urgent."
        ),
    },
    {
        "error_type": "UnhandledPromiseRejection",
        "layer": "application",
        "severity": "medium",
        "log": (
            "{ts} ERROR [{svc}] node - UnhandledPromiseRejectionWarning: Error: Cannot read "
            "properties of undefined (reading 'items')\n"
            "    at CartService.getTotal (/app/src/services/cart.js:52:19)\n"
            "    at async CartController.view (/app/src/controllers/cart.js:14:22)"
        ),
        "resolution": (
            "Race condition: cart total was computed before the cart-items fetch promise resolved "
            "on cold cache. Awaited the fetch properly and added a loading guard in the "
            "controller."
        ),
    },
    {
        "error_type": "KeyError",
        "layer": "application",
        "severity": "medium",
        "log": (
            "{ts} ERROR [{svc}] app - Traceback (most recent call last):\n"
            "  File \"/app/pipeline/transform.py\", line 88, in normalize_event\n"
            "    region = payload[\"geo\"][\"region\"]\n"
            "KeyError: 'region'"
        ),
        "resolution": (
            "A subset of mobile clients on an old app version send events without the 'geo.region' "
            "field. Added .get() with a default of 'unknown' and back-filled affected rows in a "
            "one-off batch correction."
        ),
    },
    {
        "error_type": "TimeoutError",
        "layer": "database",
        "severity": "high",
        "log": (
            "{ts} ERROR [{svc}] db - Query timeout after 15000ms: SELECT * FROM orders "
            "WHERE customer_id = ? ORDER BY created_at DESC\n"
            "\tat {svc}.repository.OrderRepository.findByCustomer(OrderRepository.java:102)"
        ),
        "resolution": (
            "Missing index on orders(customer_id, created_at) meant the query was doing a full "
            "table scan on a table that had grown past 40M rows. Added composite index; p99 query "
            "latency dropped from 15s+ to 40ms."
        ),
    },
    {
        "error_type": "IndexOutOfBounds",
        "layer": "application",
        "severity": "low",
        "log": (
            "{ts} ERROR [{svc}] app - IndexError: list index out of range\n"
            "  File \"/app/reports/weekly_summary.py\", line 30, in top_categories\n"
            "    return sorted_categories[:5][top_n]\n"
            "IndexError: list index out of range"
        ),
        "resolution": (
            "Internal weekly reporting script (not customer-facing) failed when fewer than 5 "
            "categories had sales in a given week. Fixed slicing logic; script reran successfully "
            "on manual trigger, no data was lost since it's idempotent."
        ),
    },
]


def gen_timestamp(base, i):
    dt = base - timedelta(hours=random.randint(1, 4000), minutes=random.randint(0, 59))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def main():
    base = datetime(2026, 8, 10, 12, 0, 0)
    records = []
    log_id = 1
    # multiple variations per template across different services
    for template in TEMPLATES:
        n_variants = random.randint(2, 4)
        chosen_services = random.sample(SERVICES, k=min(n_variants, len(SERVICES)))
        for svc in chosen_services:
            ts = gen_timestamp(base, log_id)
            raw_log = template["log"].format(ts=ts, svc=svc)
            records.append(
                {
                    "log_id": f"INC-{log_id:04d}",
                    "timestamp": ts,
                    "service": svc,
                    "error_type": template["error_type"],
                    "layer": template["layer"],
                    "severity": template["severity"],
                    "raw_log": raw_log,
                    "resolution": template["resolution"],
                }
            )
            log_id += 1

    records.sort(key=lambda r: r["timestamp"])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print(f"Wrote {len(records)} historical incidents to {OUT_PATH}")


if __name__ == "__main__":
    main()
