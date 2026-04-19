#!/usr/bin/env bash
# One-shot: create all MCP agent tasks via /api/tasks/create.
# Used because /api/tasks/create-bulk silently returns success:false for this project.
set -euo pipefail

API=http://localhost:8766/api/tasks/create
PP="/c/Users/srima/projex_verticals/LeadPulseMcp"
SPRINT="7f503ef9-3bf1-4ce9-b055-9f4824e676a1"

EPIC_RUNTIME="cf82e40d-b86b-4609-9fcb-f608b6b96af9"
EPIC_COMM="894a5b9c-06f2-4c0f-aaaa-7da7a7f86cbc"
EPIC_PLATFORM="8d5d6ba0-4dcf-47a0-b1a8-4ad117c11e7f"

F_EXT="9774fddb-0143-4f53-baeb-0239fce9ed14"
F_SND="df5e376c-6592-4803-b489-98697746165a"
F_HYG="4a79f158-601c-43b3-9b61-2b7d5c90c3e9"
F_HB="39cc0835-4641-4f49-b0b2-9614824dbef1"
F_OUT="3320f104-dd82-4749-90f0-a1efc34ec7e8"
F_IN="9b5799c5-910d-44e3-9fc0-7d608d4b4946"
F_SEC="7098c28b-e6e8-4f5e-b526-c1b83ec1a88d"
F_MG="ce706839-ccdd-40e0-848c-5a0302b1284d"
F_OBS="4e51e382-b526-4a74-bfc2-b8441dd5d408"
F_DEP="756d1e88-8e90-4e38-b06f-d7b0c4d21d87"

CREATED=0
FAILED=0

make_task() {
  local title="$1" desc="$2" type="$3" feat="$4" epic="$5"
  local payload
  payload=$(jq -n \
    --arg pp "$PP" --arg t "$title" --arg d "$desc" --arg ty "$type" \
    --arg f "$feat" --arg e "$epic" --arg s "$SPRINT" \
    '{projectPath:$pp, title:$t, description:$d, task_type:$ty, feature_id:$f, epic_id:$e, sprint_id:$s}')
  local resp
  resp=$(curl -sS -X POST "$API" -H "Content-Type: application/json" -d "$payload")
  if echo "$resp" | jq -e '.success == true' >/dev/null; then
    CREATED=$((CREATED + 1))
    local sid; sid=$(echo "$resp" | jq -r '.data.short_id')
    printf "  OK  %s  %s\n" "$sid" "$title"
  else
    FAILED=$((FAILED + 1))
    printf "  FAIL  %s  -> %s\n" "$title" "$resp"
  fi
}

echo "== Epic: MCP Agent Runtime =="
echo "-- Feature: Extraction Agent Loop --"
make_task "Extraction: campaign discovery poll loop (60s)" \
  "Background task that calls GET /api/mcp/campaigns?since=<last_check_ts> every 60 seconds. Filters campaigns that transitioned to running or have new files. Feeds candidates into the per-campaign ingestion worker queue. Persists last_check_ts per container." \
  backend "$F_EXT" "$EPIC_RUNTIME"
make_task "Extraction: Mongo campaign_leases with 10-min TTL" \
  "Acquire lease in campaign_leases collection keyed by campaign_id (TTL 10 min, renewed every 5 min). Prevents two containers racing on the same manifest. Releases on success/error. Includes contention tests." \
  database "$F_EXT" "$EPIC_RUNTIME"
make_task "Extraction: manifest fetch + S3 streaming downloader" \
  "GET /api/mcp/campaigns/:id/manifest -> receive presigned S3 URLs (1h TTL), sequence steps, schedule, sender_secret_ref. Stream chunk-by-chunk (never fully loaded). Retry once on expired presign. Wires into contact_parser (already built)." \
  api_integration "$F_EXT" "$EPIC_RUNTIME"
make_task "Extraction: upsert contacts + enqueue sends" \
  "For each parsed row: normalize email + phone, upsert campaign_contacts by (campaign_id, email), upsert refined_contacts by email using merge rules from design 6.1, enqueue one send_queue doc per (contact, step) with scheduled_for computed from campaign.start_date + step.send_offset_days + send_window_start in campaign.timezone. Idempotent." \
  backend "$F_EXT" "$EPIC_RUNTIME"
make_task "Extraction: report /api/mcp/file-ingested + row error policy" \
  "Report ingestion outcome: file_id, row_count, error_count, ingestion_status. Parser errors per row -> ingest_errors collection + skip. Fail file if >10% rows error. Release lease in both success and failure paths." \
  api_integration "$F_EXT" "$EPIC_RUNTIME"

echo "-- Feature: Sender Agent Loop --"
make_task "Sender: due-work poll + atomic lease (15s)" \
  "Every 15s query send_queue.find({status:pending, scheduled_for:{\$lte:now}, campaign_id:{\$in:activeCampaignIds}}).sort(scheduled_for:1).limit(BATCH). Then findOneAndUpdate each to status=leased, leased_by=<agent_id>, lease_expires_at=now+5min. activeCampaignIds refreshed every 60s." \
  backend "$F_SND" "$EPIC_RUNTIME"
make_task "Sender: hygiene gate + template render" \
  "Pre-send: check refined_contacts.unsubscribed_global and hard_bounce -> skipped_hygiene if either true. Render template placeholders ({{firstName}}, {{lastName}}, {{company}}, {{booking_link}}, {{target_url}}) and inject CRM tracking pixel + click-wrapped links pointing at /api/tracking/pixel/:trackerId and /api/tracking/click/:trackerId." \
  backend "$F_SND" "$EPIC_RUNTIME"
make_task "Sender: SendGrid HTTP send + SMTP fallback" \
  "Primary SendGrid HTTP API using credentials from resolve-secret. Fallback SMTP (provider, host, port, user, pass all from resolve-secret). Handle SendGrid 429 with exponential backoff. Record provider_message_id on success." \
  api_integration "$F_SND" "$EPIC_RUNTIME"
make_task "Sender: tracker-event reporting + status update" \
  "On sent: send_queue.status=sent + provider_message_id + POST /api/mcp/tracker-event{event:sent}. On bounce: status=bounced + POST tracker-event{event:bounced, bounce_type}. On hard failure: status=failed. refined_contacts.bounce_count updated on every bounce event." \
  api_integration "$F_SND" "$EPIC_RUNTIME"
make_task "Sender: per-campaign daily cap + per-provider rate limiter" \
  "Enforce campaign.daily_send_cap by atomically incrementing campaign_stats.sent_today; halt send for the day when cap hit. Per-provider token bucket keyed by (user_id, provider) cached in Mongo. Respect SendGrid 429 with exponential backoff." \
  backend "$F_SND" "$EPIC_RUNTIME"
make_task "Sender: lease sweeper + completion detection" \
  "Sweeper reverts send_queue docs with lease_expires_at < now back to pending (handles agent crash mid-send). After each successful send, check send_queue.count({campaign_id, status:pending}) == 0 -> POST /api/mcp/campaign-step-complete or /campaign-complete." \
  backend "$F_SND" "$EPIC_RUNTIME"

echo "-- Feature: Hygiene Agent Singleton --"
make_task "Hygiene: Mongo singleton lease election (90s TTL)" \
  "Single-writer election via mcp_hygiene_lease collection (90s TTL, renewed every 30s). Only lease holder runs hygiene. On lease expiry next candidate acquires it. Ensures bounce propagation + merges are single-threaded across the fleet." \
  backend "$F_HYG" "$EPIC_RUNTIME"
make_task "Hygiene: bounce propagation worker" \
  "Subscribe to bounce events (capped collection or change stream). On bounced event: increment refined_contacts.bounce_count. If bounce_type==hard -> set hard_bounce=true + hard_bounced_at=now. If soft bounces > N in 30d -> reduce deliverability_score per design 6.2." \
  backend "$F_HYG" "$EPIC_RUNTIME"
make_task "Hygiene: unsubscribe cascade" \
  "On CRM call POST /contacts/mark-unsubscribed: set refined_contacts.unsubscribed_global=true + cascade to all campaign_contacts (status=excluded, exclusion_reason=unsubscribed) + mark remaining send_queue items as skipped_unsubscribed. Audit every action." \
  backend "$F_HYG" "$EPIC_RUNTIME"
make_task "Hygiene: re-verification worker + score computation" \
  "Every 30d revalidate refined_contacts with stale last_verified_at via third-party API (ZeroBounce/NeverBounce/Hunter). Scoped to last_seen_in_campaign_at > 90d. Update verification_status + deliverability_score per design 6.2." \
  api_integration "$F_HYG" "$EPIC_RUNTIME"
make_task "Hygiene: refined_contacts merge policy pipeline" \
  "Mongo update pipeline implementing design 6.1 field precedence: first_name/last_name (longest non-null), phone (newest valid E.164), job_title (newest non-null + history in data_sources), company_* (newest non-null), hard_bounce + unsubscribed_global (sticky true), seen_in_campaigns / seen_in_tenants (append-unique)." \
  database "$F_HYG" "$EPIC_RUNTIME"

echo "-- Feature: Heartbeat + Instance Registry --"
make_task "Heartbeat: container registration on startup" \
  "After bootstrap, POST /api/mcp/register with container_arn + region. Receive instance_id + HMAC secret. Store in memory (never persist). Write initial mcp_instance_registry doc in Mongo." \
  api_integration "$F_HB" "$EPIC_RUNTIME"
make_task "Heartbeat: 30s loop with agents + cpu + mem" \
  "Post /api/mcp/heartbeat every 30s with instance_id, agent_slots_total, agent_slots_active, agents[] (role, status, current_campaign_id), cpu_pct, mem_pct, campaigns_in_flight[], mongo_lag_ms. Mirror into mcp_instance_registry." \
  api_integration "$F_HB" "$EPIC_RUNTIME"
make_task "Heartbeat: SIGTERM drain-and-shrink handler" \
  "Register SIGTERM handler for ECS scale-down. 60s grace: stop accepting new work, finish leased sends, release Mongo leases, post final heartbeat with status=draining, close Mongo, exit. tini (already in Dockerfile) forwards the signal." \
  backend "$F_HB" "$EPIC_RUNTIME"

echo "== Epic: MCP <-> CRM Communication =="
echo "-- Feature: HMAC-Signed CRM Client --"
make_task "CRM client: HMAC signature middleware" \
  "httpx async client hook that signs every outbound request: X-MCP-Timestamp, X-MCP-Signature = HMAC-SHA256(secret, timestamp + method + path + body_sha256). Rejects response if CRM counter-signature invalid. 5-minute timestamp skew tolerance." \
  backend "$F_OUT" "$EPIC_COMM"
make_task "CRM client: retries + circuit breaker" \
  "Exponential backoff (1s, 2s, 4s, 8s) on 5xx. Circuit breaker opens when CRM error rate >5% over 10 min; closes after 60s healthy probe. Logs circuit state transitions." \
  backend "$F_OUT" "$EPIC_COMM"
make_task "CRM client: pending_crm_events replay buffer" \
  "When CRM unreachable (circuit open), buffer outbound tracker-event / daily-rollup / file-ingested payloads into pending_crm_events Mongo collection. Replay worker drains the buffer on recovery in FIFO order; drops duplicates by (trackerId, event, ts) hash." \
  backend "$F_OUT" "$EPIC_COMM"

echo "-- Feature: CRM Callback Endpoints (CRM -> MCP) --"
make_task "CRM callback: GET /contacts/lookup" \
  "Expose GET /contacts/lookup?campaignId=&email= for the CRM to resolve full contact docs when a tracker click converts. HMAC-verify using CRM-side secret. Returns campaign_contacts document + refined_contacts.primary merged view." \
  api_endpoint "$F_IN" "$EPIC_COMM"
make_task "CRM callback: POST /contacts/mark-converted" \
  "Expose POST /contacts/mark-converted {campaignId, email, leadId}. Sets campaign_contacts.status=converted + annotates audit_log with leadId. Removes remaining pending send_queue entries for that contact." \
  api_endpoint "$F_IN" "$EPIC_COMM"
make_task "CRM callback: POST /contacts/mark-unsubscribed (campaign + global)" \
  "Expose POST /contacts/mark-unsubscribed. If campaignId present -> unsubscribe only that campaign. Without campaignId -> global suppression (fires hygiene cascade). Both variants write audit_log entry." \
  api_endpoint "$F_IN" "$EPIC_COMM"
make_task "CRM callback: GET /campaigns/:id/live-stats + POST /admin/scale-hint" \
  "Live-stats: real-time sends/bounces/opens snapshot (fallback when CRM rolled-up stats lag). Scale-hint: admin-initiated drain ({action:drain} stops accepting new campaigns) or resume ({action:resume}). HMAC required on both." \
  api_endpoint "$F_IN" "$EPIC_COMM"

echo "-- Feature: Sender Credential Resolution + Cache --"
make_task "Sender creds: flesh out leadpulse_client.resolve_sender_credentials" \
  "Complete the in-memory 10-minute TTL cache (already stubbed). Per-(campaign_id, tenant_user_id) eviction API. Invalidation hook that CRM can call when SendGrid key rotated. Handle revoked-key 4xx -> sender marks send failed + error surfaced via tracker-event so CRM shows reconnect-provider UI." \
  api_integration "$F_SEC" "$EPIC_COMM"
make_task "Sender creds: unit tests + cache contract" \
  "pytest suite for cache-hit / cache-miss / TTL expiry / explicit invalidate / concurrent resolver races. Document contract: MCP NEVER persists decrypted creds; survives only in RAM for this process instance." \
  testing "$F_SEC" "$EPIC_COMM"

echo "== Epic: MCP Platform Reliability =="
echo "-- Feature: MongoDB Operational Schema --"
make_task "Mongo schema: TTL index on campaign_leases.expires_at" \
  "Create TTL index on campaign_leases.expires_at (expireAfterSeconds:0) so Mongo auto-removes expired leases. Extraction agent uses this to reliably fail over when a container dies holding a lease." \
  database "$F_MG" "$EPIC_PLATFORM"
make_task "Mongo schema: lease sweeper for send_queue" \
  "Mongo doesn't support partial-document TTL, so implement a background sweeper (every 30s) that findOneAndUpdate's send_queue docs where lease_expires_at < now AND status=leased back to status=pending. Idempotent; logs counts." \
  database "$F_MG" "$EPIC_PLATFORM"
make_task "Mongo schema: operational collections + capped sizes" \
  "Provision: ingest_errors (standard), bounce_events (capped 100 MB), audit_log (standard, append-only), pending_crm_events (standard), mcp_hygiene_lease, mcp_instance_registry. Document shard-key strategy (campaign_id hashed) for Atlas provisioning." \
  database "$F_MG" "$EPIC_PLATFORM"

echo "-- Feature: Structured Logging + Metrics + Alerts --"
make_task "Observability: structured JSON logs + correlation ids" \
  "Replace ad-hoc logging. All log lines are JSON to stdout with: instance_id, agent_uid, campaign_id, email (sha256-hashed), trace_id (from X-Request-Id or uuid), msg, level. ECS ships to CloudWatch Logs." \
  backend "$F_OBS" "$EPIC_PLATFORM"
make_task "Observability: metrics + CloudWatch alerts" \
  "Emit counters + histograms per design 7.2 (rows_ingested_total, emails_sent_total, send_latency_ms p50/p95/p99, queue.pending_depth{campaign_id}, crm_api.call_latency_ms, crm_api.errors_total). CloudWatch log-metric filters + alarms: heartbeat missed >2m, queue pending >1h, hygiene lease miss >5m, CRM err >5% 10m." \
  devops "$F_OBS" "$EPIC_PLATFORM"

echo "-- Feature: Nuitka Build + ECS Deploy Hardening --"
make_task "Deploy: Nuitka compiled-binary smoke test" \
  "After 'nuitka --standalone --onefile' step in the Dockerfile, add a verification stage that invokes the binary with --help / spins up the server on an ephemeral port / curls /health. Fails the build on import errors hidden by Nuitka's dynamic-resolution quirks." \
  devops "$F_DEP" "$EPIC_PLATFORM"
make_task "Deploy: CRM admin portal -> aws/deploy.sh hook" \
  "In projex_crm admin fleet dashboard, wire 'Deploy MCP version X' button to invoke aws/deploy.sh (or call ECS UpdateService directly via aws-fleet.service.ts). Passes DOCKERHUB_USERNAME, IMAGE_TAG, ECS_CLUSTER, ECS_SERVICE. Streams deploy logs back to the admin UI." \
  devops "$F_DEP" "$EPIC_PLATFORM"
make_task "Deploy: image hardening + security scan" \
  "Add trivy scan step in the Docker build (fail on HIGH/CRITICAL CVEs). Verify non-root user. Verify tini handles PID 1. Verify healthcheck runs within 40s startup. Document SBOM generation (docker sbom)." \
  devops "$F_DEP" "$EPIC_PLATFORM"

echo ""
echo "Done. Created: $CREATED   Failed: $FAILED"
