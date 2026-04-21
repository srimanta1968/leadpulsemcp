-- Delete duplicate tasks, keeping 1 per (feature_id, title) group.
-- Scope: ProjexLight project 6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8 (LeadPulseMcp).
-- Strategy: keep earliest created_at (tiebreak: smallest id); delete the rest.
--
-- Run inside a transaction so you can ROLLBACK if the preview looks wrong.

BEGIN;

-- 1. Preview: show every row that will be deleted.
WITH ranked AS (
    SELECT
        id,
        feature_id,
        title,
        status,
        short_id,
        created_at,
        ROW_NUMBER() OVER (
            PARTITION BY feature_id, title
            ORDER BY created_at ASC, id ASC
        ) AS rn
    FROM tasks
    WHERE project_id = '6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8'
)
SELECT id, short_id, feature_id, title, status, created_at
FROM ranked
WHERE rn > 1
ORDER BY feature_id, title, created_at;

-- 2. Delete every row that isn't the earliest in its (feature_id, title) group.
WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY feature_id, title
            ORDER BY created_at ASC, id ASC
        ) AS rn
    FROM tasks
    WHERE project_id = '6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8'
)
DELETE FROM tasks
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- 3. Sanity check: no remaining duplicates in this project.
SELECT feature_id, title, COUNT(*) AS copies
FROM tasks
WHERE project_id = '6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8'
GROUP BY feature_id, title
HAVING COUNT(*) > 1;
-- Expected: 0 rows.

-- If all looks correct:
-- COMMIT;
-- Otherwise:
-- ROLLBACK;


-- ==== Appendix: explicit delete list (as observed on 2026-04-20) ====
-- 15 rows across 5 groups. Use this instead of the CTE above if you want
-- a one-shot hard-coded script.
--
-- DELETE FROM tasks WHERE id IN (
--     -- Rich heartbeat: Per-tenant backlog counters in heartbeat
--     'd906ad9f-75d0-474d-a318-8abd36b74917',
--     '381c0406-275c-438a-8fad-9df948a9f598',
--     '9643dd01-8fb8-4f6b-8e3f-c8f01b6a1ee3',
--     -- Active-work polling: Tenant quotas refresh loop
--     '73e45e7c-f3ae-49ef-bd7c-4507449bed49',
--     '4defb7dc-61f1-42d1-9353-932d53b2be3a',
--     '0a122cca-c0b5-4cfc-856d-440a3fc7b2d5',
--     -- Container self-config: Include allocation in register payload
--     'ca04a3c8-0e96-4cf7-9c11-bb2a8aa0c246',
--     '6f329494-2cf4-4773-816c-8743993432d7',
--     '46bb9993-e3d7-41c1-9779-6e5db39551b5',
--     -- Multi-tenant fair: Per-tenant daily cap check in throttle_service
--     '1eaa1d91-e28f-40d5-b2f5-389be4559f1f',
--     '4d6fe195-98dd-46c2-99fe-1848a4bdbd87',
--     'a8e7e943-e519-4aa2-8a19-f525517a8e8e',
--     -- Multi-tenant fair: Create global tenant_cursors collection for round-robin
--     '354c7fa7-cc37-4ff2-bd5c-bef318460c38',
--     'cf11ce73-f68a-40bf-9b54-9db0926debe6',
--     '7846177a-f4c9-4987-af6c-8f791008c61a'
-- );
