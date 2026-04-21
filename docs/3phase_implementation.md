PART 1 — Implementation Plan

Three phases. Phase 1 is what you build now; Phase 2 is what you build as traffic grows; Phase 3 is the 100k-user state.

> **Design correction (2026-04-20):** The Phase 1 design below has been reconciled with the code that now exists in `projex_crm/`. The original draft spoke of pool partitioning (`pool_id`), a `MULTI_TENANT` feature flag, and a `/api/mcp/pool-work` endpoint. None of those were implemented and none of them are needed. The shipped architecture is simpler:
>
> - **One ECS service** (`MCP_FLEET_SERVICE` env var), N identical containers
> - **All containers serve all tenants** — no pool partitioning; every container reads one discovery feed
> - The MCP code is **always** tenant-aware (there is no single-tenant code path to gate with a flag)
> - **Autoscaling is CRM-side only**, in `projex_crm/server/src/services/aws-fleet.service.ts` — calls `ECS UpdateService` with hard limits `{min:1, max:20}` from `system_settings.mcp.fleet.limits`, 15-minute anti-flap guardrail, scale-down gated on `campaign-forecast.service.ts` projected sends, and a feature flag `system_settings.mcp.fleet.autoscale.enabled`
> - **Real discovery endpoint** is `GET /api/mcp/campaigns?since=<iso>` (single feed across all running campaigns). `/api/mcp/active-work` and `/api/mcp/pool-work` were never implemented
>
> Everything below has been rewritten to match.

Phase 1 — Shared-Pool MVP (target: 1,000 users, ~10M sends/day)

Duration: 6–8 engineer-weeks. Deliverable: one fleet of identical MCP containers serving all tenants with fair scheduling.

1.1 MCP changes (Python/FastAPI — C:/Users/srima/projex_verticals/LeadPulseMcp/)

Change 1 — Container self-configuration from ECS task size

File: server/app/core/runtime_config.py + server/app/main.py + server/app/core/allocation.py

- Read `CPU_VCPU` and `RAM_MB` env vars at boot (ECS injects them from the task definition).
- Call `compute_allocation(cpu_vcpu, ram_mb)` → `AgentAllocation{senders, extraction, hygiene_eligible, daily_capacity}`. The Python calculator must stay bit-for-bit identical with `projex_crm/server/src/services/container-sizes.service.ts`.
- Use the returned `senders` count to drive supervisor loop registration. Only elect the hygiene singleton on containers where `hygiene_eligible=true` (total ≥ 5 slots).
- Include the `AgentAllocation` in both the `POST /api/mcp/register` and `POST /api/mcp/heartbeat` payloads so the CRM's Fleet Dashboard can reconcile expected vs actual.
- No MULTI_TENANT / POOL_ID / MAX_TENANTS_PER_CONTAINER flags — the shared-pool design has no notion of pools.

Change 2 — Tenant-fair lease_batch()

File: server/app/services/send_queue_service.py (lines 56–90)

Replace the current query:

# BEFORE: sorts only by scheduled_for — whale starves others

db.send_queue.find_one_and_update(
{"status": "pending", "scheduled_for": {"$lte": now}},
      {"$set": {"status": "leased", "lease_expires_at": now + 300}},
sort=[("scheduled_for", 1)]
)

With a round-robin over tenants:

# AFTER: pick next tenant by round-robin, then lease their due work

tenant = await \_next_tenant_in_round_robin() # reads tenant_cursors collection
batch = db.send_queue.find({
"status": "pending",
"tenant_user_id": tenant.user_id,
"scheduled_for": {"$lte": now}
}).sort([("scheduled_for", 1)]).limit(BATCH_SIZE)

# atomic lease same batch; skip tenant and advance cursor on empty

Add a single global helper collection `tenant_cursors`:
{ \_id: "global", last_tenant_id, last_picked_at }

Every MCP container in the ECS service shares the same cursor — that is what produces the round-robin across the fleet. No `pool_id` field, because there is only one pool.

Change 3 — Per-tenant daily send cap

File: server/app/services/throttle_service.py

Add a third bucket type alongside existing (campaign_id, date) and (user_id, provider):
async def try_consume_tenant_daily_cap(tenant_user_id, limit): # Atomic $inc on tenant_stats_daily keyed by (tenant_user_id, date) # Returns False when tenant hits plan quota — sender skips to next tenant

Plan-tier quotas load on container boot from `GET /api/mcp/tenant-quotas` (CRM endpoint — still backlog in CRM TK-2655). Until that ships, the per-tenant cap is a no-op (returns True on missing quota), and the already-shipped per-campaign `daily_send_cap` + per-hour `throttle_per_hour` (from `campaign.config_snapshot`) carry the throttling load.

Change 4 — Extraction agent uses the real discovery endpoint

File: server/app/agents/extraction.py

Keep polling the existing `GET /api/mcp/campaigns?since=<iso>` — no new endpoint is needed. The response already contains every running campaign across every tenant (the CRM filters by `status='running'` only, not by pool). Lease per `(tenant_user_id, campaign_id)` so two tenants with the same campaign UUID don't collide.

Change 5 — Tenant hash routing (for later sharding)

File: server/app/db/mongodb.py — new helper

def tenant_shard_key(tenant_user_id: UUID) -> str: # Deterministic hash for Mongo shard key # Pre-declared even before sharding is enabled
return hashlib.sha256(str(tenant_user_id).encode()).hexdigest()[:16]

Inject shard_key field on every insert into send_queue, campaign_contacts, refined_contacts, campaign_stats. This future-proofs Phase 3 sharding.

Change 6 — Bounded JSON streaming

File: server/app/services/contact_parser.py:80

Replace json.loads(file.read()) with ijson.items(file, "item") so 500 MB JSON files don't OOM a shared container.

Change 7 — Backpressure on pending_crm_events

File: server/app/db/mongodb.py + sender agent

When pending_crm_events.count() exceeds 80% of cap, sender pauses new leases for 60s. Prevents cascading failure when CRM is down.

1.2 CRM changes (TypeScript/Express — C:/Users/srima/projex_crm/server/src/)

Change 8 — [DROPPED] Pool-aware discovery endpoint

Not needed. The existing `GET /api/mcp/campaigns?since=<iso>` already serves as the single cross-tenant discovery feed. There is one pool.

Change 9 — [DROPPED] Pool assignment table

Not needed. No `mcp_pool_assignments` table; no per-user pool mapping. Every tenant's work is visible to every container.

Change 10 — Tenant quota endpoint (status: backlog — CRM TK-2655)

File: server/src/routes/mcp.routes.ts
GET /api/mcp/tenant-quotas
→ [{ tenant_user_id, daily_cap, plan_tier, per_hour_cap }]
MCP calls this on boot and every 15 minutes. No `pool_id` query param. Until this ships, MCP tasks that consume it (tenant quotas refresh loop) stay blocked.

Change 11 — Launch validation

File: server/src/routes/campaigns.routes.ts:499

Before flipping status='running', verify every file in campaigns.files has ingestion_status='complete'. Reject with 409 if pending/failed. Today it flips status regardless.

Change 12 — Stats archival job

File (new): server/src/jobs/archive-campaign-stats.job.ts

Nightly at 03:00 UTC, for each campaign, move any key in stats_daily older than 90 days into stats_monthly (summed by month). Add setInterval alongside the existing sendgrid-bounce-scheduler.

Change 13 — Forecast fallback

File: server/src/services/campaign-forecast.service.ts

If system_settings.mcp.fleet.forecast.updated_at is older than 6h, compute:
projected_sends[D] = Σ_campaign (file.row_count × active_step_count / duration_days)

1.3 Infrastructure — Phase 1

- MCP fleet: one ECS service (`MCP_FLEET_SERVICE`), N identical m6i.xlarge containers (large tier per `container-sizes.service.ts`: ≈ 10 sender agents each). `desiredCount` is owned by the CRM's `aws-fleet.service.ts` and driven by the forecast. Hard limits `{min:1, max:20}` stored in `system_settings.mcp.fleet.limits`. No MULTI_TENANT env var, no POOL_ID.
- Each container size (`nano`…`2xlarge`) is defined in `container-sizes.service.ts` (CRM) and mirrored bit-for-bit in `server/app/core/allocation.py` (MCP). ECS task definitions inject `CPU_VCPU` and `RAM_MB` so MCP can self-size on boot.
- MongoDB: single 3-node replica set on r6i.large. `shard_key = sha256(tenant_user_id)[:16]` pre-declared on `send_queue`, `campaign_contacts`, `refined_contacts`, `campaign_stats` (so Phase 2 sharding needs no backfill).
- Redis: NOT needed yet. In-process HMAC nonce cache is fine for ≤20 containers.
- Secrets: existing `/api/mcp/resolve-secret` stays; each container caches decrypted SendGrid keys in-memory with 10-min TTL.

  1.4 Rollout sequence

1. Week 1–2: MCP changes 1–5 on a feature branch, unit-tested.
2. Week 3: CRM changes 10–13 (tenant-quotas endpoint, launch validation, archival job, forecast fallback), migration tested on staging DB clone.
3. Week 4: Deploy to staging; shadow-run with 5 synthetic tenants + 1 real pilot tenant. Validate fairness with a deliberate whale test (pilot uploads 1M rows; other tenants must keep progressing).
4. Week 5: Production deploy behind the `system_settings.mcp.fleet.autoscale.enabled` feature flag. Onboard first 100 tenants.
5. Week 6–8: Onboard remaining 900 tenants in batches of 100/day; monitor per-tenant backlog via the Fleet Dashboard. MCP changes 6, 7 during this window.

1.5 Acceptance criteria for Phase 1

- Any single tenant sending 50k/day uses at most X% of fleet capacity where X = daily_cap ÷ total_pool_capacity.
- 99th-percentile latency from campaigns.launched_at → first send ≤ 90 seconds.
- Whale test: one tenant uploads 5M rows, others sustain unaffected throughput (±10%).
- SendGrid key never leaves MCP memory (verify via access_logs audit trail of /resolve-secret).
- All mongo collections carry the shard_key field on every document.

---

Phase 2 — Hardening for 10,000 users (~100M sends/day)

Duration: 4–6 engineer-weeks. Starts: when the single-pool fleet hits 70% sustained utilization at its `max=20` ceiling.

1. Raise the hard ceiling. Bump `system_settings.mcp.fleet.limits.max` from 20 to 50 and let `aws-fleet.service.ts` scale a single service to the new ceiling. Pool partitioning is reconsidered only if a single Mongo replica set can no longer absorb the write load.
2. Redis for nonce + provider cache. Replaces in-process nonce store — lets the larger fleet share HMAC replay protection. ElastiCache cache.t3.small.
3. Mongo sharding enabled. Shard key was pre-declared in Phase 1; now actually enable sharding and add shard 2 and 3. Zero backfill needed.
4. Tenant-scoped forecasting. CRM forecast service computes per-tenant demand and feeds it to the autoscaler for smarter scale-up timing.
5. Circuit breaker on tenant bad-actors. If a tenant triggers >5% hard-bounce rate in 24h, auto-pause their sends. Add a `tenant_health` table in admindb.
6. Observability. Prometheus/OTEL exporter on each MCP container (already scaffolded in server/app/core/observability.py — verify).

Phase 3 — Scale to 100,000 users (~1B sends/day)

Duration: ongoing. Starts: Year 2.

1. Multi-AZ / multi-region fleets. Split the single ECS service into per-region services (e.g. `mcp-fleet-us-east-1`, `mcp-fleet-eu-west-1`) with latency-based routing at the CRM. This is when `pool_id` genuinely becomes necessary — each region is one "pool". Introduce it then, not before.
2. Mongo 10-shard cluster on r6i.2xlarge × 30 (10 shards × 3-node replica sets). Storage budget ~5 TB.
3. Re-introduce BYOC as Pro/Enterprise upgrade. Implement byoc-launch.service.ts per CRM design doc §20.5. Move regulated/whale tenants off shared pool.
4. Tenant promotion logic. Auto-suggest BYOC upgrade when a tenant exceeds 500k/day sustained.
5. Hygiene sharding. Hygiene agent is currently a fleet singleton — split into one singleton per region to scale write throughput on refined_contacts.

---

● PART 2 — Final Workflow (after Phase 1)

How the system operates end-to-end once the shared-pool rollout lands.

2.1 System topology

                 ┌────────────────────────── AWS VPC ────────────────────────────┐
                 │                                                                │
    Tenant ─────▶│  ALB ──▶ CRM API (Express/TS)        ◀─── Admin Console       │

(browser) │ │ │ │
│ │ └─▶ Postgres (appdata + admindb) │
│ │ │
│ └─ HMAC─▶ MCP Fleet (one ECS service, N containers, N owned by │
│ │ aws-fleet.service.ts autoscaler — min 1, max 20) │
│ │ │
│ ├── Extraction agent (60s poll of /api/mcp/campaigns) │
│ ├── Sender agents × K per container (K = compute_allocation)│
│ └── Hygiene singleton (one across the entire fleet, │
│ elected via mcp_hygiene_lease) │
│ │ │
│ MongoDB replica set (3 nodes) │
│ (send_queue, refined_contacts,│
│ campaign_contacts, stats) │
│ │
│ S3 (tenant-scoped prefixes, SSE-KMS) │
│ SendGrid / SMTP (per-tenant keys from user_integrations) │
└────────────────────────────────────────────────────────────────┘

Browser → click tracking → CRM (same ALB). Booking page served by CRM frontend.

2.2 Tenant journey — end to end

Step A — Signup

1. User signs up. No pool assignment row is written — the shared fleet is single-pool, so every tenant is visible to every container automatically on the next `/api/mcp/campaigns` poll.
2. When CRM TK-2655 ships `GET /api/mcp/tenant-quotas`, MCP refreshes plan-tier quotas every 15 min and new tenants get picked up automatically.

Step B — Create campaign

1. User opens Campaigns → New (CampaignNewPage.tsx). Wizard: config → sequence → upload → review.
2. Each wizard step hits the existing REST API (POST /api/campaigns, PUT /:id/sequence, etc.).
3. Upload page requests presigned S3 PUT from POST /api/uploads/presign-put (SSE-KMS with tenant-scoped key); browser uploads CSV/XLSX/JSON directly to S3 — never through the CRM app server.
4. On upload complete, client posts POST /api/campaigns/:id/files with the S3 key; CRM appends a descriptor to campaigns.files JSONB.

Step C — Launch

1. User clicks Launch → POST /api/campaigns/:id/launch.
2. CRM validates every file has ingestion_status='complete' (Phase 1 change #11). Rejects if not.
3. CRM copies outreach_sequences.steps into campaigns.sequence_snapshot and flips status to running.
4. Within 60s, the extraction agent (in every container across the single fleet) picks up the campaign via GET /api/mcp/campaigns?since=<last_poll_ts>. Whichever container acquires the Mongo `campaign_leases` lease first processes it.
5. Extraction acquires a campaign lease (campaign_leases collection), streams the S3 file row-by-row, and for each row does three writes:


    - Upsert campaign_contacts keyed by (tenant_user_id, campaign_id, email)
    - Upsert refined_contacts keyed by email (merges hard_bounce / unsubscribed_global)
    - Insert one send_queue doc per (contact, step_index) with scheduled_for = user_window_start + step_offset in user's timezone

6. On complete, POST /api/mcp/file-ingested { row_count, errors }; CRM updates campaigns.files[i].ingestion_status.

Step D — Sending (the fair-pool loop)

Every sender agent, every 15s:

1. Call lease_batch() which:


    - Reads the global tenant_cursors doc to find the next tenant in round-robin order (one cursor shared by every container in the fleet)
    - Checks tenant's daily cap (tenant_stats_daily); skips if at quota
    - Checks tenant's per-hour cap from plan
    - Atomically leases up to BATCH_SIZE (default 8) send_queue docs matching (tenant_user_id, status=pending, scheduled_for<=now)
    - Advances cursor

2. For each send in batch:


    - Skip if refined_contacts.unsubscribed_global or hard_bounce sticky
    - Check SendGrid provider token bucket (provider_rate_buckets) → hold if empty
    - Resolve sender credentials: POST /api/mcp/resolve-secret { secret_ref } → cached 10 min in-memory
    - Render step template: substitute {{firstName}}, {{booking_link}}, rewrite <a href> with click-tracker, append pixel
    - POST https://api.sendgrid.com/v3/mail/send
    - On 429: exponential backoff (up to 4 tries); on success: capture X-Message-Id
    - POST /api/mcp/tracker-event { type: sent, message_id, ... } → CRM increments campaigns.stats_daily[today].sends
    - Update send_queue.status = sent

3. If tenant has no more pending work, sender advances cursor and picks next tenant — this is what prevents whales starving small tenants.

Step E — Recipient interactions

Click: Recipient clicks a wrapped link → GET /api/tracking/click/:trackerId?t=target_url on CRM:

1. Decrypt token → { cid, sid, em, fn, ln, ph, co }.
2. Upsert leads with conversion_source='campaign:{cid}:click'; append activity_log entry.
3. Insert engagement_events row.
4. Increment campaigns.stats_daily[today].clicks via jsonb_set.
5. 302 to original URL (or to /book/:trackerId for booking links).

Book: On booking page, recipient picks slot → POST /api/campaigns/book/:trackerId:

1. Same upsert lead, upgrade conversion_source to ':booking'.
2. Insert calendar_appointments with attendees=[{ email, campaign_id, ... }], lead_id=<lead>.
3. Trigger calendar sync (Google/Outlook via calendar_connections).
4. Fire-and-forget POST mcp://contacts/mark-converted so MCP stops sending follow-ups.

Unsubscribe: RFC 8058 one-click → CRM inserts email_suppression_list row → calls MCP /contacts/mark-unsubscribed → hygiene agent cascades send_queue.status='skipped_unsubscribed' for all
pending sends.

Step F — Monitoring (what the user sees)

Tenant's CampaignDetailPage.tsx polls GET /api/campaigns/:id/stats every 30s, which returns:

- Today: live counters aggregated from stats_daily[today] keys (sent/delivered/opened/clicked/bounced)
- Last 7d / 30d: aggregates over stats_daily
- Per-step progress: progress.steps_complete / sequence_snapshot.length
- Leads & appointments: indexed queries on leads.conversion_source (partial index) and calendar_appointments.attendees (GIN)

Tenant never calls into MCP. All data lives in Postgres by the time the UI reads it.

Step G — Admin & autoscale

LeadPulse ops opens AdminCampaignsPage.tsx → Fleet panel:

- Reads mcp_worker_instances rows with last_heartbeat_at > now()-2min
- Shows fleet-wide: active instances, total agents, CPU/mem, backlog
- Forecast panel reads system_settings.mcp.fleet.forecast

Nightly at 02:00 UTC the forecast service runs:
for date D in [today..+7]:
agent_hours = projected_sends[D] / SENDS_PER_AGENT_PER_HOUR
recommended_instances = ceil(agent_hours / 24 / AGENTS_PER_INSTANCE)
If system_settings.mcp.fleet.autoscale.enabled=true, aws-fleet.service.ts calls ECS UpdateService with anti-flap guardrails (1 scale action per 15 min, min=1, max=20 for the single fleet).

Step H — Hygiene (continuous background)

Hygiene singleton (elected once across the fleet via mcp_hygiene_lease) runs every minute:

1. Drain bounce_events capped collection → update refined_contacts.bounce_count, hard_bounce, deliverability_score.
2. Every 30 days, re-verify addresses seen in last 90 days via ZeroBounce/NeverBounce.
3. Merge new campaign_contacts upserts into refined_contacts with field precedence.

2.3 Failure modes and recovery

┌────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────────┐
│ Failure │ What happens │ Recovery │
├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────┤
│ One MCP container dies │ ECS auto-replaces; leased send_queue docs expire after 5 min and the sweeper reverts them to │ Automatic; no data loss │
│ │ pending │ │
├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────┤
│ CRM API down │ MCP sender stops new leases; events buffer to pending_crm_events capped collection │ When CRM returns, events drain; backpressure prevents overflow │
│ │ │ (Phase 1 change #7) │
├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────┤
│ Mongo primary fails │ Replica set elects new primary in ~10s; writes stall then resume │ Automatic; sender retries │
├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────┤
│ SendGrid 429 │ Per-tenant provider bucket throttles; exponential backoff; eventually succeeds │ Automatic │
├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────┤
│ Whale tenant uploads │ Extraction queues work, but sender round-robin still serves other tenants between its slices │ Small tenants see no slowdown (the whole point of Phase 1 │
│ 5M rows │ │ change #2) │
├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────┤
│ Tenant exceeds daily │ try_consume_tenant_daily_cap returns false → sender skips to next tenant → remaining work │ Automatic; no intervention │
│ quota │ stays pending until midnight tz │ │
└────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────┘

2.4 Cost model (steady state)

┌────────────────────────┬───────────────────────────┬─────────────────────────────────────────┬───────┬─────────────┬────────────────┐
│ Scale │ MCP (large tier) │ Mongo │ Redis │ Total/month │ Per user/month │
├────────────────────────┼───────────────────────────┼─────────────────────────────────────────┼───────┼─────────────┼────────────────┤
│ 1,000 users, 10M/day │ 4× m6i.xlarge = $680 │ 3× r6i.large = $600 │ — │ ~$1,400 │ $1.40 │
├────────────────────────┼───────────────────────────┼─────────────────────────────────────────┼───────┼─────────────┼────────────────┤
│ 10,000 users, 100M/day │ 20× m6i.xlarge = $3,400 │ 3-shard × 3-node r6i.xlarge = $3,000 │ $100 │ ~$6,700 │ $0.67 │
├────────────────────────┼───────────────────────────┼─────────────────────────────────────────┼───────┼─────────────┼────────────────┤
│ 100,000 users, 1B/day │ 400× m6i.xlarge = $68,000 │ 10-shard × 3-node r6i.2xlarge = $10,000 │ $500 │ ~$80,000 │ $0.80 │
└────────────────────────┴───────────────────────────┴─────────────────────────────────────────┴───────┴─────────────┴────────────────┘

Unit economics improve with scale because Mongo and fleet overhead amortize across more tenants.

2.5 What you gain vs what you give up

┌─────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Gain │ Trade-off │
├─────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ ~30× cheaper than dedicated-per-tenant │ Tenants share infrastructure — strong isolation needs code discipline │
├─────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Seamless onboarding (no EC2 to provision) │ Noisy-neighbor is theoretical risk — mitigated by per-tenant caps + fair scheduler │
├─────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Single codebase, single deployment pipeline │ The tenant-fair code path must be well-tested               │
├─────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Unit economics that beat every competitor │ SendGrid keys briefly decrypted in shared container memory — mitigated by 10-min TTL and audit log │
└─────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────┘

---

● TL;DR — what to build first

Week 1 sprint: MCP changes #1 (container self-config from ECS task size), #2 (fair lease_batch + global tenant_cursors), #3 (per-tenant quota). These three unlock 90% of the shared-pool behavior; everything else is hardening.

Week 2–3: CRM change #10 (tenant-quotas endpoint — still CRM backlog TK-2655) and #11 (launch validation). Changes #8 and #9 from the original draft are dropped — see the correction note at the top of this document.

Then: deploy 4 large containers + 3-node Mongo, migrate 100 tenants, observe, continue.

After Phase 1 lands, you have a live 1k-user shared pool at ~$1.40/user/month, with a code path already primed for Mongo sharding (because shard_key is pre-declared) and a fleet layout that
extends linearly through 100k users.
