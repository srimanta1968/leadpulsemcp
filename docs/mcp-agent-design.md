# MCP Docker Agent — Design

**Document status:** Design draft for review (pre-implementation)
**Owner:** ProjexLight Platform
**Last updated:** 2026-04-17

---

## 1. Purpose and boundary

The MCP (Managed Campaign Processor) is an **external** Docker-based agent system that executes campaign email delivery on behalf of the ProjexLight CRM. It is deployed and scaled separately in AWS (ECS Fargate or EC2 ASG) and communicates with the CRM via HTTPS. The MCP does not share a database or codebase with the CRM.

**MCP owns:**
- Parsing uploaded contact datasheets (CSV / XLSX / JSON) from S3
- Per-campaign contact list storage in MongoDB
- A refined, cross-campaign, cross-tenant unique-contact dataset (for internal data hygiene and potential contact-list commerce)
- Scheduling which emails are due per campaign sequence
- Composing and sending emails via the campaign owner's SendGrid / SMTP provider
- Reporting delivery status, open/click events, bounces back to the CRM

**CRM owns** (documented in the companion doc):
- Campaign configuration, schedules, sequence templates
- S3 upload and file metadata
- Tracker-click handling, lead creation, appointment booking
- Fleet dashboard, forecasting, autoscaling recommendations
- All tenant/user authentication

**Hard rule:** the CRM is the source of truth for configuration and conversions (leads, appointments). The MCP is the source of truth for contact list content and per-send operational state. Neither side queries the other's database directly.

---

## 2. Deployment topology

```
                          AWS Region
 +---------------------------------------------------------------+
 |                                                               |
 |   ECS Service: mcp-worker                                     |
 |   +-----------------------------+  +------------------------+ |
 |   |  mcp-worker container (1)   |  | mcp-worker container N | |
 |   |                             |  |                        | |
 |   |  - extraction_agent         |  |  - extraction_agent    | |
 |   |  - sender_agent x K         |  |  - sender_agent x K    | |
 |   |  - heartbeat loop           |  |  - heartbeat loop      | |
 |   |                             |  |                        | |
 |   +-----------------------------+  +------------------------+ |
 |                                                               |
 |   ECS Service: mcp-hygiene    (singleton)                     |
 |   +-----------------------------------------+                 |
 |   |  hygiene_agent (refined-contacts steward)|                |
 |   +-----------------------------------------+                 |
 |                                                               |
 |   MongoDB Atlas cluster (mcpdb)                               |
 |   +-------------------------------+                           |
 |   |  collections (see section 4)  |                           |
 |   +-------------------------------+                           |
 |                                                               |
 +---------------------------------------------------------------+

          HTTPS (HMAC signed both directions)
                          |
                          v
                 ProjexLight CRM API
```

### 2.1 Container contents

Each `mcp-worker` container runs:

| Process | Count per container | Responsibility |
|---|---|---|
| Extraction agent | 1 (singleton per container, leased per campaign) | Pulls new datasheets from S3 and ingests into Mongo |
| Sender agent | `K` (configurable, default 4) | Drains `send_queue` collection, sends emails, reports status |
| Heartbeat loop | 1 | Posts container + agent health to CRM every 30s |

A separate `mcp-hygiene` ECS service runs **one** `hygiene_agent` across the fleet (elected via Mongo lease document) that maintains the refined-contacts collection. Hygiene is not per-container because it operates on the global dataset.

### 2.2 Scaling model

- **Workers** scale horizontally via ECS desired-count. CRM forecasting service recommends a target count; operators apply manually or via autoscale flag.
- **Agents within a container** scale vertically via a container env var `SENDER_AGENTS_PER_CONTAINER`. Default 4, max 16 (bound by CPU and provider rate limits).
- **Hygiene** never scales — singleton with HA via Mongo lease (TTL 90s, renewed every 30s).
- **Container right-sizing:** target ~60 sends/agent/hour at 4 agents = 240 sends/hour/container. Tune in production.

### 2.3 Regions and failure domains

Single region initially (us-east-1). Multi-AZ via ECS Fargate. Cross-region is a future concern; MongoDB Atlas replica set spans 3 AZs.

---

## 3. Agent architecture

### 3.1 Extraction agent

**Trigger:** polls CRM `GET /api/mcp/campaigns?since=<ts>` every 60 seconds. Each response contains campaigns that transitioned to `running` or uploaded new files since `ts`.

**Per-campaign workflow:**

1. Acquire a lease in Mongo (`campaign_leases` collection, TTL 10 minutes) keyed by `campaign_id`. Prevents two containers racing on the same file.
2. Fetch CRM: `GET /api/mcp/campaigns/:id/manifest` → returns presigned S3 GET URLs (1-hour TTL), file metadata, sequence step templates, schedule config.
3. For each file, stream from S3 using the appropriate parser (`csv-parse`, `xlsx`, `JSON.parse`). Never fully load into memory — stream row by row.
4. For each row:
   - Normalize: lowercase + trim email, parse phone to E.164, strip HTML from text fields.
   - Upsert into `campaign_contacts` (Mongo) keyed by `(campaign_id, email)`.
   - Upsert into `refined_contacts` (Mongo) keyed by `email` (merge rules in §6).
   - Enqueue one `send_queue` document per (contact, step) with `scheduled_for` computed from `campaign.start_date + step.send_offset_days + campaign.send_window_start` in `campaign.timezone`.
5. Update CRM: `POST /api/mcp/file-ingested` with `{ file_id, row_count, error_count, ingestion_status }`.
6. Release lease.

**Failure handling:**

- Parser errors on a row: log to `ingest_errors` collection, skip the row, continue. Fail the file only if >10% of rows error.
- S3 URL expired: request a fresh manifest and retry once.
- CRM unreachable: exponential backoff, resume on recovery. Lease prevents duplicate ingestion.

**Idempotency:** uses `(campaign_id, email)` upsert and `(campaign_id, email, step_index)` for `send_queue`. Re-ingesting the same file produces no duplicates.

### 3.2 Sender agent

**Trigger:** polls `send_queue` every 15 seconds for due work belonging to an active, non-paused campaign.

**Work selection query (Mongo):**
```js
send_queue.find({
  status: "pending",
  scheduled_for: { $lte: now },
  campaign_id: { $in: activeCampaignIds }   // refreshed from CRM every minute
})
.sort({ scheduled_for: 1 })
.limit(BATCH)
```

Then atomically `findOneAndUpdate` to set `status: "leased"`, `leased_by: <agent_id>`, `lease_expires_at: now + 5min`. Expired leases revert to `pending` via a sweeper.

**Per-send workflow:**

1. Load contact document from `campaign_contacts`.
2. Check `refined_contacts.unsubscribed_global` and `refined_contacts.hard_bounce` — if either true, mark send as `skipped_hygiene` and move on.
3. Resolve sender credentials: call CRM `POST /api/mcp/resolve-secret { secret_ref }` → returns decrypted SendGrid API key for the campaign owner.
4. Build email:
   - Load sequence step template from the campaign cache.
   - Render placeholders: `{{firstName}}`, `{{lastName}}`, `{{company}}`, `{{booking_link}}`, `{{target_url}}`. Booking and target links use the CRM-provided tracker token URL template.
   - Inject open-pixel and click-wrapped links pointing at the CRM tracking endpoints (`/api/tracking/pixel/:trackerId`, `/api/tracking/click/:trackerId`). The CRM tracking system is already built — MCP only inserts the URLs.
5. Send via SendGrid HTTP API (preferred) or SMTP fallback.
6. On success: update `send_queue.status = "sent"`, record `provider_message_id`, POST `/api/mcp/tracker-event { trackerId, event: "sent", ts }` to CRM.
7. On bounce or hard failure: update `send_queue.status = "bounced"` or `"failed"`, POST event to CRM, update `refined_contacts.bounce_count`.

**Throttling:**

- Per-campaign daily cap enforced by incrementing `campaign_stats.sent_today` atomically; halt once cap reached.
- Per-provider rate limit enforced by token bucket per `(user_id, provider)` cached in Mongo.
- Respect SendGrid 429 responses with exponential backoff.

**Completion detection:** after each send, check `send_queue.count({campaign_id, status: "pending"}) == 0`. If zero and all steps have been scheduled, POST `/api/mcp/campaign-step-complete` or `/api/mcp/campaign-complete` to the CRM so it can update the campaign status. The sender agent then moves to another campaign's due work — no explicit "move to next campaign" state machine; it's implicit in the polling query.

### 3.3 Hygiene agent (refined contacts steward)

Runs as a single-writer singleton. Responsibilities:

1. **Bounce propagation.** Subscribe to the `tracker_events` stream from `send_queue` writes or a dedicated `bounce_events` collection. For each `bounced` event:
   - Increment `refined_contacts.bounce_count`.
   - If `bounce_type === "hard"` → set `refined_contacts.hard_bounce = true`, `hard_bounced_at = now`.
   - If soft bounces > N in trailing 30 days → set `deliverability_score -= X`.

2. **Unsubscribe propagation.** When CRM records an unsubscribe (via existing RFC 8058 flow), it calls `POST /contacts/mark-unsubscribed` on MCP. Hygiene agent sets `refined_contacts.unsubscribed_global = true` and cascades to all `campaign_contacts` for that email, marking remaining `send_queue` items as `skipped_unsubscribed`.

3. **Re-verification.** Periodically (configurable, default every 30 days), revalidate `refined_contacts` with stale `last_verified_at`:
   - Call a third-party email verification API (ZeroBounce / NeverBounce / Hunter).
   - Update `deliverability_score`, `verification_status`, `last_verified_at`.
   - Scope: only re-verify contacts that have been referenced by a recent campaign (`last_seen_in_campaign_at > 90d ago`) to bound cost.

4. **Data merging.** When extraction agent upserts into `refined_contacts`, the hygiene agent's merge policy runs server-side via MongoDB update pipeline:
   - Prefer non-null incoming value over null existing value.
   - Prefer longer string for `full_name`.
   - Prefer newest `job_title` if `source_confidence` is higher or equal.
   - Never overwrite `hard_bounce=true` with `false`.
   - Never overwrite `unsubscribed_global=true` with `false`.
   - Append `campaign_id` to `seen_in_campaigns[]`.

5. **Contact-list commerce export.** Exposes a read-only query API (internal only) for filtering refined contacts by `job_title`, `industry`, `company_size`, `deliverability_score > threshold`. Future commercial feature. Gated behind admin auth.

---

## 4. MongoDB schema (mcpdb)

### 4.1 `campaign_contacts`

One document per (campaign, email). Populated by extraction agent.

```js
{
  _id: ObjectId,
  campaign_id: "uuid",          // CRM campaign id
  tenant_user_id: "uuid",       // CRM owner of the campaign
  email: "jane@acme.com",       // normalized lowercase
  first_name, last_name,
  phone,                        // E.164
  company, company_url, job_title,
  custom_fields: { ... },       // any extra columns from the datasheet
  source_file_id: "uuid",       // CRM campaign_files.id
  source_row_number: 42,
  imported_at: ISODate,
  status: "active" | "excluded" | "converted",
  exclusion_reason: "unsubscribed" | "hard_bounce" | "manual" | null
}
```

**Indexes:**
- `{ campaign_id: 1, email: 1 }` unique
- `{ email: 1 }`
- `{ campaign_id: 1, status: 1 }`
- Shard key: `{ campaign_id: "hashed" }`

### 4.2 `refined_contacts`

One document per unique email across the whole system.

```js
{
  _id: ObjectId,
  email: "jane@acme.com",       // unique
  primary: {
    first_name, last_name, full_name,
    phone, job_title,
    company, company_url, company_domain,
    linkedin_url
  },
  seen_in_campaigns: [ "campaign-uuid-1", "campaign-uuid-2" ],
  seen_in_tenants:   [ "user-uuid-1", "user-uuid-2" ],
  first_seen_at, last_seen_at,
  bounce_count: 0,
  soft_bounce_count_30d: 0,
  hard_bounce: false,
  hard_bounced_at: null,
  unsubscribed_global: false,
  unsubscribed_at: null,
  deliverability_score: 100,    // 0-100
  verification_status: "unverified" | "valid" | "invalid" | "risky" | "catch_all",
  last_verified_at,
  data_sources: [                // provenance
    { source_type: "datasheet", campaign_id, imported_at, field_contributions: {...} }
  ]
}
```

**Indexes:**
- `{ email: 1 }` unique
- `{ "primary.job_title": 1, "primary.company_domain": 1 }` for commerce queries
- `{ deliverability_score: -1, hard_bounce: 1 }`
- `{ last_verified_at: 1 }` for hygiene sweep

### 4.3 `send_queue`

One document per (contact, step).

```js
{
  _id: ObjectId,
  campaign_id, tenant_user_id,
  contact_id,                   // ref campaign_contacts._id
  email,
  step_index: 0 | 1 | 2 | ...,
  tracker_id,                   // assigned by CRM and passed through manifest
  scheduled_for: ISODate,
  status: "pending" | "leased" | "sent" | "bounced" | "failed" | "skipped_hygiene" | "skipped_unsubscribed",
  leased_by: "agent-uid",
  lease_expires_at: ISODate,
  attempts: 0,
  last_error,
  sent_at, provider_message_id, provider,
  created_at, updated_at
}
```

**Indexes:**
- `{ campaign_id: 1, email: 1, step_index: 1 }` unique
- `{ status: 1, scheduled_for: 1 }` (hot path for sender polling)
- `{ lease_expires_at: 1 }` for sweeper
- Shard key: `{ campaign_id: "hashed" }`

### 4.4 `campaign_leases`

```js
{
  _id: "campaign-uuid",         // campaign_id as _id
  held_by: "container-id",
  acquired_at,
  expires_at                    // TTL index
}
```

TTL index on `expires_at`.

### 4.5 `campaign_stats`

Per-campaign running counters, flushed to CRM nightly as `campaign_daily_rollups`.

```js
{
  _id: ObjectId,
  campaign_id,
  date: "2026-04-17",
  sends, opens, clicks, bounces_soft, bounces_hard, unsubscribes,
  skipped_hygiene, skipped_unsubscribed,
  last_updated_at
}
```

### 4.6 `mcp_instance_registry`

Tracks this container's own heartbeat (mirror of what's sent to CRM).

```js
{
  _id: "container-id",
  started_at, last_heartbeat_at,
  agent_slots_total, agent_slots_active,
  current_campaigns: [ "uuid" ],
  cpu_pct, mem_pct
}
```

### 4.7 `ingest_errors`, `bounce_events`, `audit_log`

Operational collections. Capped where appropriate.

---

## 5. API contract with CRM

All HTTPS, all HMAC-signed (shared secret per MCP instance, rotated quarterly). Request body is JSON; signature covers `timestamp + method + path + body_sha256`.

### 5.1 MCP calls CRM

| Endpoint | Purpose |
|---|---|
| `POST /api/mcp/register` | New container registers, receives `instance_id` and config |
| `POST /api/mcp/heartbeat` | Every 30s: `{ instance_id, agents, cpu, mem, current_campaigns }` |
| `GET  /api/mcp/campaigns?since=<ts>` | Discover new running campaigns and new files |
| `GET  /api/mcp/campaigns/:id/manifest` | Presigned S3 GET URLs, schedule, steps, sender config ref |
| `POST /api/mcp/file-ingested` | Report ingestion outcome for a file |
| `POST /api/mcp/tracker-event` | `{ trackerId, event, ts, meta }` — one call per sent/delivered/bounced |
| `POST /api/mcp/daily-rollup` | Nightly batch push of `campaign_stats` |
| `POST /api/mcp/campaign-step-complete` | All contacts processed for a step |
| `POST /api/mcp/campaign-complete` | All steps done — CRM flips status to completed |
| `POST /api/mcp/resolve-secret` | Unwrap encrypted SendGrid key reference |

### 5.2 CRM calls MCP

MCP exposes a small read-and-mutate surface, gated by HMAC with a CRM-side secret.

| Endpoint | Purpose |
|---|---|
| `GET  /contacts/lookup?campaignId=&email=` | Used when a tracker click converts — returns full contact for lead creation |
| `POST /contacts/mark-converted` | `{ campaignId, email, leadId }` — contact becomes a lead |
| `POST /contacts/mark-unsubscribed` | `{ campaignId, email }` or `{ email }` (global) |
| `GET  /campaigns/:id/live-stats` | Optional real-time fallback; otherwise UI uses CRM's rolled-up stats |
| `POST /admin/scale-hint` | Admin-initiated: "drain and stop accepting new campaigns" / "resume" |

### 5.3 Authentication and secrets

- Each container has a unique `instance_id` and HMAC secret issued during `/api/mcp/register`.
- Secrets stored in AWS Secrets Manager, mounted into container via ECS task role.
- CRM stores the hashed secret in `mcp_worker_instances.api_key_hash`.
- SendGrid keys per tenant are encrypted with the CRM's `encryption.service.ts` (AES-256-GCM, per-user PBKDF2 key). MCP receives a `secret_ref` in the manifest and resolves to plaintext only at send time via `/api/mcp/resolve-secret`. MCP never persists decrypted keys; cached in memory with 10-minute TTL.

---

## 6. Data merging rules (refined_contacts)

Invoked by extraction agent on every row upsert and by hygiene agent on events.

### 6.1 Field precedence

| Field | Rule |
|---|---|
| `email` | immutable once set |
| `first_name`, `last_name` | prefer longest non-null; mixed case preserved |
| `phone` | prefer newest valid E.164 |
| `job_title` | prefer newest non-null; track previous values in `data_sources[].field_contributions` |
| `company`, `company_url`, `company_domain` | prefer newest non-null |
| `linkedin_url` | prefer any non-null; first-wins then newest if both non-null |
| `hard_bounce` | sticky true — once true, never reset |
| `unsubscribed_global` | sticky true — once true, never reset |
| `deliverability_score` | computed, not merged |
| `seen_in_campaigns`, `seen_in_tenants` | append-unique |

### 6.2 Deliverability score

Base 100. Adjustments:
- Verified by third-party as `valid`: +0 (baseline)
- Verified as `risky` / `catch_all`: -20
- Verified as `invalid`: -100 (clamped to 0)
- Each soft bounce in last 30d: -5
- One hard bounce: -100 (effectively dead)
- No verification in >30d: decay -5 per 30d

### 6.3 Privacy, compliance, cross-tenant concerns

This is the most legally sensitive part of the design and must be reviewed before launch.

1. **Cross-tenant data sharing.** The `refined_contacts` collection intentionally aggregates contacts across all tenants. This is a cross-tenant data flow and must be disclosed in the platform's Terms of Service and DPA. Tenants must contractually consent to their uploads contributing to the refined pool.
2. **Right to erasure.** On a DSAR delete request (per email), hygiene agent removes the `refined_contacts` document AND all matching `campaign_contacts` AND all matching `send_queue` entries. Audit trail in `audit_log`.
3. **Suppression.** A global suppression API (`POST /contacts/mark-unsubscribed { email }` without `campaignId`) blacklists the email from ever appearing in a sent email again, across all tenants.
4. **Contact-list commerce gate.** Selling refined contacts to third parties requires the source tenants to have opted into a commercial-use tier. The query API must filter out contacts whose source tenants did not consent. Enforce via `refined_contacts.commercial_use_allowed` (computed from `seen_in_tenants` intersected with consented tenants).
5. **Geographic scoping.** Do not export to jurisdictions forbidden by the source tenant's region (e.g. an EU-sourced contact should not be sold to a non-adequate-country buyer without SCCs).
6. **PII minimization.** Do not store additional PII beyond what the datasheet supplies. No behavioral tracking beyond delivery signals.

---

## 7. Observability

### 7.1 Heartbeat payload

Posted to CRM every 30 seconds:
```json
{
  "instance_id": "arn:aws:ecs:...:task/...",
  "region": "us-east-1",
  "status": "active",
  "agent_slots_total": 4,
  "agent_slots_active": 3,
  "agents": [
    { "agent_uid": "ext-1", "role": "extraction", "status": "idle" },
    { "agent_uid": "snd-1", "role": "sender", "status": "sending", "current_campaign_id": "..." }
  ],
  "cpu_pct": 42.1,
  "mem_pct": 55.3,
  "campaigns_in_flight": ["campaign-uuid-a", "campaign-uuid-b"],
  "mongo_lag_ms": 12
}
```

### 7.2 Metrics emitted (CloudWatch / Prometheus)

- `mcp.extraction.rows_ingested_total`
- `mcp.extraction.errors_total`
- `mcp.sender.emails_sent_total`, `.emails_bounced_total`, `.emails_failed_total`
- `mcp.sender.send_latency_ms` (p50, p95, p99)
- `mcp.hygiene.refined_contacts_updated_total`
- `mcp.hygiene.suppressions_total`
- `mcp.queue.pending_depth{campaign_id}`
- `mcp.crm_api.call_latency_ms{endpoint}`
- `mcp.crm_api.errors_total{endpoint,code}`

### 7.3 Logs

Structured JSON to stdout; ECS ships to CloudWatch Logs. Fields always include `instance_id`, `agent_uid`, `campaign_id`, `email` (hashed for PII), `trace_id`.

### 7.4 Alerts (owned by platform team)

- Heartbeat missing >2 minutes → page on-call.
- `send_queue` pending depth growing >1h → investigate stuck campaign.
- Hygiene agent lease not renewed >5 min → failover.
- CRM API error rate >5% for 10 min → circuit break + alert.

---

## 8. Scaling and cost controls

1. **Container-level scaling.** CRM forecasting service computes `recommended_instances` nightly and on significant schedule changes. Operator applies via Admin Dashboard; autoscale flag lets CRM call AWS ECS `UpdateService` directly.
2. **Send-rate throttling.** Per-campaign daily cap and per-provider rate-limit enforcement prevent runaway send bills.
3. **Mongo cost.** Shard on `campaign_id` to keep hot-working-set bounded. Archive `send_queue` rows older than 90 days to cold storage (S3 Glacier).
4. **Refined-contacts growth.** Bounded by unique-email universe. Soft cap: 50M distinct emails before reviewing retention policy.
5. **S3 access.** Extraction agent uses presigned URLs only — MCP never has long-lived S3 credentials. Limits blast radius.
6. **Drain-and-shrink.** When ASG scales down, container receives SIGTERM. Grace period (60s) lets active sends complete, releases Mongo leases, posts final heartbeat.

---

## 9. Failure scenarios

| Scenario | Behavior |
|---|---|
| CRM is down | Extraction pauses (no new manifests). Sender continues draining already-leased work. New events buffered to `pending_crm_events` for replay when CRM recovers. |
| Mongo primary failover | Atlas handles; agents retry with backoff. |
| SendGrid API key revoked | `resolve-secret` returns error; sender marks sends `failed` with error code; CRM surfaces to user as "reconnect SendGrid". |
| Datasheet unparseable | File marked `ingestion_failed` with first error; no partial ingestion. |
| Duplicate container startup | `mcp_instance_registry` uses unique `_id = container_arn`; duplicate registration is a no-op. |
| Lease expires mid-send | Send may duplicate once. Idempotency: SendGrid `X-Message-Id` dedup window + `send_queue.status="sent"` double-check before retry. Accept at-most-once via SendGrid `batch_id` if exactness required. |
| Hygiene singleton dies | Mongo TTL expires lease after 90s; next candidate acquires lease. Pending hygiene events replay from `bounce_events` capped collection. |

---

## 10. Future considerations

- **Per-region MCP clusters** for GDPR data residency.
- **SES integration** alongside SendGrid.
- **Inline email validation** before send (integrate existing 5-layer pipeline from CRM memory).
- **Contact list marketplace UI** (separate product surface, queries `refined_contacts`).
- **A/B step variants** in sequences (requires extending step schema).
- **Per-contact personalization at send time** using `refined_contacts.job_title` and inferred signals.

---

## Appendix A. Work discovery sequence diagram (new campaign)

```
User                CRM                    Extraction Agent           S3               Mongo                Sender Agent
 |                   |                           |                     |                  |                       |
 | create campaign   |                           |                     |                  |                       |
 |------------------>|                           |                     |                  |                       |
 | upload datasheet  |                           |                     |                  |                       |
 |------------------>|--- put object ----------->|                     |                  |                       |
 | launch campaign   |                           |                     |                  |                       |
 |------------------>|                           |                     |                  |                       |
 |                   |                           |                     |                  |                       |
 |                   |<-- GET /api/mcp/campaigns?since -----------     |                  |                       |
 |                   |--> { campaigns: [...] }                         |                  |                       |
 |                   |                           |                     |                  |                       |
 |                   |<-- GET /manifest -------------------            |                  |                       |
 |                   |--> { s3_url, steps, schedule, secret_ref }      |                  |                       |
 |                   |                           |                     |                  |                       |
 |                   |                           |---- stream rows --->|                  |                       |
 |                   |                           |<----- rows ---------|                  |                       |
 |                   |                           |---- upsert per row ------------------->|                       |
 |                   |                           |---- enqueue sends ---------------------|                       |
 |                   |                           |                                        |                       |
 |                   |<-- POST /file-ingested ---|                                        |                       |
 |                   |                           |                                        |                       |
 |                   |                           |                                        |<-- poll due work -----|
 |                   |                           |                                        |--- lease batch ------>|
 |                   |<-- POST /resolve-secret --|                                        |                       |
 |                   |                           |                                        |<--- send via SG ------|
 |                   |<-- POST /tracker-event ---------------------------------------------- (per send)            |
```
