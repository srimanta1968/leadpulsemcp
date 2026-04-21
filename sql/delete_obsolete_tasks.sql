-- Delete obsolete MCP tasks.
-- Scope: ProjexLight project 6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8 (LeadPulseMcp).
--
-- These tasks were written against the speculative Phase 1 design in
-- docs/3phase_implementation.md (pool_id partitioning, MULTI_TENANT flag,
-- /api/mcp/active-work endpoint). The actual CRM implementation
-- (projex_crm/server/src/services/aws-fleet.service.ts +
-- projex_crm/server/src/routes/mcp.routes.ts) uses a single ECS service
-- with N identical containers serving ALL tenants, autoscaled by CRM via
-- ECS UpdateService. No pool_id, no MULTI_TENANT flag, no active-work
-- endpoint (real discovery is GET /api/mcp/campaigns?since=<iso>).
--
-- Run inside a transaction so you can ROLLBACK after previewing.

BEGIN;

-- 1. Preview what will be deleted.
SELECT id, short_id, title, status, created_at
FROM tasks
WHERE id IN (
    -- TK-2665: Add MULTI_TENANT, POOL_ID, MAX_TENANTS_PER_CONTAINER config flags
    -- Reason: CRM has no pool partitioning; the system is always multi-tenant
    -- by design. aws-fleet.service.ts scales a single ECS service with
    -- {min:1, max:20} hard limits. No flag is needed.
    '73f919f6-b2cd-4d12-831d-1d1b00747775',

    -- TK-26?? "Refactor extraction agent to poll /api/mcp/active-work"
    -- Reason: /api/mcp/active-work does not exist on CRM. The real discovery
    -- endpoint is GET /api/mcp/campaigns?since=<iso> which extraction already
    -- consumes via leadpulse_client.get_active_campaigns().
    'b6c97abb-1795-4df3-9309-e9ea8874fd7d'
)
ORDER BY created_at;

-- 2. Delete.
DELETE FROM tasks
WHERE id IN (
    '73f919f6-b2cd-4d12-831d-1d1b00747775',
    'b6c97abb-1795-4df3-9309-e9ea8874fd7d'
);

-- 3. Sanity check.
SELECT id, short_id, title
FROM tasks
WHERE id IN (
    '73f919f6-b2cd-4d12-831d-1d1b00747775',
    'b6c97abb-1795-4df3-9309-e9ea8874fd7d'
);
-- Expected: 0 rows.

-- If the preview + delete look correct:
--   COMMIT;
-- Otherwise:
--   ROLLBACK;


-- ==== Optional: also drop the blocked task ====
-- TK-2625 "Tenant quotas refresh loop" (id 3454b2d8-7659-4e6c-8882-12f5ad2abc1b)
-- is blocked on CRM TK-2655 which has not shipped the
-- GET /api/mcp/tenant-quotas endpoint. Delete only if you want to re-add
-- it later once the CRM endpoint lands; otherwise leave it in the backlog.
--
-- DELETE FROM tasks WHERE id = '3454b2d8-7659-4e6c-8882-12f5ad2abc1b';
