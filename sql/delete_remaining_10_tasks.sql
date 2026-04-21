-- Delete the 10 remaining obsolete/out-of-scope tasks from LeadPulseMcp.
-- Project: ProjexLight project 6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8.
--
-- Verified against the live DB on 2026-04-20: all 10 IDs below are still
-- present (the earlier duplicate-cleanup removed a different 15 tasks).
--
-- Three buckets:
--   A. Built against the wrong Phase-1 design (2)
--   B. Frontend work for a headless FastAPI container (3)
--   C. CRM-scope tasks mis-filed on the MCP project (5)
--
-- Run inside a transaction so you can ROLLBACK after previewing.

BEGIN;

-- 1. Preview: should show exactly 10 rows.
SELECT id, short_id, title, status, task_type, created_at
FROM tasks
WHERE id IN (
    -- Bucket A: obsolete design (see correction note at top of
    -- docs/3phase_implementation.md)
    '73f919f6-b2cd-4d12-831d-1d1b00747775',  -- TK-2665 Add MULTI_TENANT, POOL_ID, MAX_TENANTS_PER_CONTAINER config flags
    'b6c97abb-1795-4df3-9309-e9ea8874fd7d',  -- Refactor extraction agent to poll /api/mcp/active-work (endpoint doesn't exist)

    -- Bucket B: frontend work; LeadPulseMcp has no UI (CRM CampaignNewPage / CampaignDetailPage already ship)
    '2b6d88e6-ae72-4e31-840f-eeac566540b6',  -- Frontend Campaign Creation Form
    'b0c7e8fa-60fd-4901-a353-c9ec453a6cb6',  -- Frontend Contact Upload Interface
    '12d6d109-8df0-43a0-b7a8-fa66ed0ef00d',  -- Frontend Campaign Status Display

    -- Bucket C: CRM-scope tasks mis-filed here
    'ad43f1af-23b1-4686-8316-64e399c1d6a5',  -- Setup User Registration Endpoint (MCP uses HMAC container auth, no end-users)
    '1046988c-c16e-4744-bb7b-504a03fe70f5',  -- Create Campaign Management API (CRM owns campaign CRUD)
    '27a2f322-1812-4c9b-a879-592c0300e07c',  -- Implement Contact Upload Endpoint (CRM owns S3 presigned-put)
    'e09ca6c5-236a-465a-bef6-cb7f8d4ea3c2',  -- Create Campaign Status Tracking API (CRM has /api/campaigns/:id/stats)
    'afb6f68b-e6d4-4527-aadc-3bd36049e65b'   -- Testing Campaign Status Tracking (CRM-side feature test)
)
ORDER BY created_at;

-- 2. Delete.
DELETE FROM tasks
WHERE id IN (
    '73f919f6-b2cd-4d12-831d-1d1b00747775',
    'b6c97abb-1795-4df3-9309-e9ea8874fd7d',
    '2b6d88e6-ae72-4e31-840f-eeac566540b6',
    'b0c7e8fa-60fd-4901-a353-c9ec453a6cb6',
    '12d6d109-8df0-43a0-b7a8-fa66ed0ef00d',
    'ad43f1af-23b1-4686-8316-64e399c1d6a5',
    '1046988c-c16e-4744-bb7b-504a03fe70f5',
    '27a2f322-1812-4c9b-a879-592c0300e07c',
    'e09ca6c5-236a-465a-bef6-cb7f8d4ea3c2',
    'afb6f68b-e6d4-4527-aadc-3bd36049e65b'
);

-- 3. Sanity check: should return 0 rows.
SELECT id, short_id, title
FROM tasks
WHERE id IN (
    '73f919f6-b2cd-4d12-831d-1d1b00747775',
    'b6c97abb-1795-4df3-9309-e9ea8874fd7d',
    '2b6d88e6-ae72-4e31-840f-eeac566540b6',
    'b0c7e8fa-60fd-4901-a353-c9ec453a6cb6',
    '12d6d109-8df0-43a0-b7a8-fa66ed0ef00d',
    'ad43f1af-23b1-4686-8316-64e399c1d6a5',
    '1046988c-c16e-4744-bb7b-504a03fe70f5',
    '27a2f322-1812-4c9b-a879-592c0300e07c',
    'e09ca6c5-236a-465a-bef6-cb7f8d4ea3c2',
    'afb6f68b-e6d4-4527-aadc-3bd36049e65b'
);

-- COMMIT;
-- ROLLBACK;
