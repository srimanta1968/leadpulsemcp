-- Delete misplaced frontend tasks from the LeadPulseMcp project.
-- Scope: ProjexLight project 6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8 (LeadPulseMcp).
--
-- LeadPulseMcp is a headless Python/FastAPI Docker agent. It has no
-- React/TypeScript client, no UI, no frontend build. The tasks below
-- are UI work that belongs in projex_crm, where the screens already
-- exist and are marked done:
--   - client/src/pages/campaigns/CampaignNewPage.tsx  (campaign wizard)
--   - client/src/pages/campaigns/CampaignDetailPage.tsx (status display)
--   - contact upload flow via presigned S3 in the CRM
--
-- Run inside a transaction so you can ROLLBACK after previewing.

BEGIN;

-- 1. Preview.
SELECT id, short_id, title, status, task_type, created_at
FROM tasks
WHERE id IN (
    '2b6d88e6-ae72-4e31-840f-eeac566540b6',  -- Frontend Campaign Creation Form
    'b0c7e8fa-60fd-4901-a353-c9ec453a6cb6',  -- Frontend Contact Upload Interface
    '12d6d109-8df0-43a0-b7a8-fa66ed0ef00d'   -- Frontend Campaign Status Display
)
ORDER BY created_at;

-- 2. Delete.
DELETE FROM tasks
WHERE id IN (
    '2b6d88e6-ae72-4e31-840f-eeac566540b6',
    'b0c7e8fa-60fd-4901-a353-c9ec453a6cb6',
    '12d6d109-8df0-43a0-b7a8-fa66ed0ef00d'
);

-- 3. Sanity check.
SELECT id, short_id, title
FROM tasks
WHERE id IN (
    '2b6d88e6-ae72-4e31-840f-eeac566540b6',
    'b0c7e8fa-60fd-4901-a353-c9ec453a6cb6',
    '12d6d109-8df0-43a0-b7a8-fa66ed0ef00d'
);
-- Expected: 0 rows.

-- COMMIT;
-- ROLLBACK;


-- ==== Also consider deleting (ambiguous — your call) ====
-- These are filed on the MCP project but conceptually CRM-scope
-- (user registration, campaign-management HTTP API, contact upload
-- endpoints, UI-facing status tracking). The MCP itself authenticates
-- containers via HMAC — it has no end-user registration. The CRM
-- already owns campaign CRUD, file upload via presigned S3, and the
-- /api/campaigns/:id/stats aggregator.
--
-- DELETE FROM tasks WHERE id IN (
--     'ad43f1af-23b1-4686-8316-64e399c1d6a5',  -- Setup User Registration Endpoint
--     '1046988c-c16e-4744-bb7b-504a03fe70f5',  -- Create Campaign Management API
--     '27a2f322-1812-4c9b-a879-592c0300e07c',  -- Implement Contact Upload Endpoint
--     'e09ca6c5-236a-465a-bef6-cb7f8d4ea3c2',  -- Create Campaign Status Tracking API
--     'afb6f68b-e6d4-4527-aadc-3bd36049e65b'   -- Testing Campaign Status Tracking
-- );
