# Support Engineer Assessment - On-Call Scenario



### This is what each signal suggests:

- `502 ... 5320ms upstream timeout` - The API gives up after about 5 seconds of waiting for the internal service. The 502 is a symptom, not a cause.
- `context deadline exceeded` - The internal service does not respond within its deadline.
- `WARN retrying request` - Each retry adds more load to a service that is already saturated.
- `connection pool size: 100 active: 100 idle: 0` - The database connection pool is exhausted, so new requests must wait for a free connection.
- `slow query detected: 4800ms` - One query uses almost the full 5 second budget and holds the connection the whole time.
- `/api/health 200 8ms` - The load balancer thinks that every instance is healthy and keeps sending traffic.
- `ERROR upstream request failed: context deadline exceeded` - The operation took longer than the time limit allowed for it and was automatically canceled.

## 1 - Investigation Plan

### 1 - Confirm the alert and open the incident (0 to 5 minutes)

Why: Every next step depends on knowing that the problem is real and when it started.

What to do:
- Check the metrics dashboard to confirm that the error rate is real and not a metrics problem.
- Acknowledge the alert, open an incident channel and record the start time (about 14:02).

### 2 - Determine scope and impact (5 to 10 minutes)

Why: The scope sets the severity. 7% errors on the orders endpoint, with an enterprise customer affected, is SEV2.

What to do:
- Check the endpoints: is it only `/api/orders`, or every endpoint that uses the database?
- Check the instances: are the 502 errors on the instances with high CPU?
- If the problem is only on those 2 instances, it is local.
- If it spreads over all 5 instances, the cause is a shared dependency, e.g. the database.
- Check the customer: is this customer the only one affected, or the most affected?
- Check the business impact: how critical is `/api/orders`, and how do failed orders affect revenue?

### 3 - Check what changed

Why: Even with no deployments in the last 24 hours, the cause can be a change that does not go through the deployment pipeline.

What to do:
- Check for configuration and feature flag changes.
- Check for infrastructure events and database maintenance.
- The alert fired 2 minutes after 14:00, which can point to a cron job.
- Check the traffic shape. The volume is normal, but that does not exclude heavy queries.

### 4 - Follow the request path

Why: The logs show that the API is only waiting. The bottleneck is further down the path.

What to do:
- Check the 2 instances that are at 95% CPU usage. Find what is consuming the CPU (retry loops, GC, actual work) and compare them to the healthy ones.
- Check the connection pool usage over time, and the ratio of incoming requests to the number of retries.
- Check the DB: active and long-running queries, locks and blocking sessions, CPU, I/O and connections, and the query plan of the slow query detected (4800ms).


### 5 - Mitigate while doing the investigation

Why: The goal is to reduce the customer impact first, then find the root cause.

What to do:
- Kill or throttle the slow query or job, if it is found.
- Reduce or disable retries to stop the amplification.
- Restart the 2 hot instances if the errors are concentrated on them.
- Rate-limit a single heavy tenant, if a tenant is the cause.

### Hypotheses

### 1. 
- Hypothesis: A slow DB query holds connections and exhausts the pool.
- Evidence: the query takes 4800ms, the pool is at 100/100 (size/active), and the deadline is exceeded.
- How to test it: slow query log.

### 2. 
- Hypothesis: Retries amplify the problem.
- Evidence: the warning in the logs `WARN retrying request to internal-service`.
- How to test it: compare the incoming API traffic with the requests sent to the internal service. Every timed-out request that is retried adds another call, so the internal service receives more requests per second than the API. 

### 3. 

- Hypothesis: The problem is local to the 2 instances.
- Evidence: CPU usage at 95%.
- How to test it: check the errors per instance and the node metrics.

### 4.
- Hypothesis: There was a change outside of the deployment pipeline.
- Evidence: the problem started around 14:00, which makes a scheduled job suspicious.
- How to test it: check the change logs, the scheduler and the database events.



---

## 2 - Escalation Decision

Yes. After 30 minutes the problem is still not resolved, so I would escalate.

**At what point**: I would escalate as soon as the evidence points to the database or the internal service and the mitigations that I can apply are not working. Waiting until the 30 minute mark does not help the customer or the team solve the problem.

**To whom**: I would reach the on-call lead, declare SEV2 and coordinate a response to solve the problem. I would also page the owners of the internal service and the database (the DBA on-call), because the evidence points to them, and inform the account manager of the enterprise customer, who handles the communication with the customer.

**The information:** 


```
Title:  [SEV2] /api/orders 502 errors (5-8%) since 14:02 - not resolved after "x time"

Summary: since 14:02, 5-8% of /api/orders requests return a 502 error (upstream timeout).

Impact: error rate, affected endpoints and enterprise customer.

Timeline : <start time>
		   <alert time>
		   <actions taken, where and when>

Evidence:
		- Connection pool 100/100 active; 0 idle.
		- Slow query: <query fingerprint>, with a duration of 4800 ms
		- context deadline exceeded rate
		- The 2 affected instances (name, tier and CPU graph)
		- Dashboard and log links 

Ruled out: deployment, full outage.
Hypotheses: <hypotheses 1-4 from section 1>
Mitigations tried and results: for example, pods restarted, errors dropped and then came back.
Request: Ask the database administrator to review active queries and locks.
```

## 3 - Preventive Improvements

### Improvement 1 - Saturation alerts and health checks that reflect real health

**Problem:** `/api/health` returned 200 in 8 ms while requests were failing. The alert came from the error rate, after customers were already affected, not from the cause.

**Change:**
- Alert on connection pool usage > 80% for >= 2 minutes, p95 / p99 database query latency, upstream latency and retry rate.
- A readiness check that reflects whether the instance can really serve requests, so a degraded instance is removed from rotation.


**Why it helps:**
- The pool filled up before the 502s started. An alert on saturation would have given minutes of warning.

**Type:** improves detection.

### Improvement 2 - Timeout budgets, controlled retries and per-tenant limits

**Problem:** a 4.8 s query fits inside a 5 s timeout, so it holds a connection for its full duration. Retries add more load to the overloaded service, and one heavy tenant or job can use the whole pool.

**Change:**

- Layered timeouts: database statement timeout (~2 s) < internal service timeout < API timeout.
- Retries with exponential backoff, jitter and a retry budget.
- Circuit breaker between the API and the internal service.
- Per-tenant concurrency limits.

**Why it helps:** slow queries fail fast instead of blocking the pool, and one bad query or customer cannot affect all the others.

**Type:** reduces impact.

### Improvement 3 - Distributed tracing and slow query tracking per tenant

**Problem:** finding the cause meant moving manually between API logs, internal service logs and the database.

**Change:**

- Tracing across API -> internal service -> database, with tenant and endpoint tags.
- Query fingerprinting and a slow query dashboard.
- Regular review of query plans and indexes as tables grow.

**Why it helps:** the slow query and the tenant behind it are visible in one place, so diagnosis takes less time. Reviewing query plans catches regressions before they cause an incident.

**Type:** improves detection and prevents recurrence.
