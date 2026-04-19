-- =====================================================================
-- BYOC revert: delete features and their tasks created on 2026-04-19
-- Run inside a transaction; verify counts before COMMIT.
-- Table names: features, tasks. tasks.feature_id → features.id.
-- =====================================================================
 BEGIN;

-- ---------------------------------------------------------------------
-- LeadPulseMcp project   (project_id = '6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8')
-- 9 features → delete their tasks first, then the features.
-- ---------------------------------------------------------------------
 -- Sanity-check: how many tasks will be removed?

SELECT 'LeadPulseMcp tasks to delete' AS label,
       COUNT(*) AS n
FROM tasks
WHERE feature_id IN ( 'c2d5b154-0b4d-483f-97af-85ff9dd18c04', -- Tenant MCP Container Image (BYOC)
 'f9e68d45-5aad-4902-a661-082bc72f2135', -- Shard-Based Send Queue + Campaign State Machine
 '1ddb167c-08a2-4da5-91ce-43c3f0f84c5e', -- Tenant-Side Provider Credential Vault
 'cb0f7452-2096-45fd-8a8b-536665f5c820', -- Heartbeat-Response Command Channel
 'f1073e32-334d-4b2f-9b1a-355ed74fc77f', -- Image Version Compatibility Gate
 '6eb6bc71-7ef5-4f5e-a8e7-749c6d374f89', -- BYOC Tenant Provisioning (Connect AWS)
 '2f3b4739-377a-437d-b4aa-2406244d5f7a', -- MCP Status Dashboard (Tenant Panel)
 'c6fb37a5-c567-422d-bb08-d2ff75c6b1df', -- Master Refined-Contacts Pool (Push, Hashed)
 '2106e1d0-3ca0-4d54-a8a7-2ad8ee4f82e8' -- SSE-KMS Upload Pipeline + Auto-Delete
);


DELETE
FROM tasks
WHERE feature_id IN ( 'c2d5b154-0b4d-483f-97af-85ff9dd18c04',
                      'f9e68d45-5aad-4902-a661-082bc72f2135',
                      '1ddb167c-08a2-4da5-91ce-43c3f0f84c5e',
                      'cb0f7452-2096-45fd-8a8b-536665f5c820',
                      'f1073e32-334d-4b2f-9b1a-355ed74fc77f',
                      '6eb6bc71-7ef5-4f5e-a8e7-749c6d374f89',
                      '2f3b4739-377a-437d-b4aa-2406244d5f7a',
                      'c6fb37a5-c567-422d-bb08-d2ff75c6b1df',
                      '2106e1d0-3ca0-4d54-a8a7-2ad8ee4f82e8')
    AND project_id = '6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8';


DELETE
FROM features
WHERE id IN ( 'c2d5b154-0b4d-483f-97af-85ff9dd18c04',
              'f9e68d45-5aad-4902-a661-082bc72f2135',
              '1ddb167c-08a2-4da5-91ce-43c3f0f84c5e',
              'cb0f7452-2096-45fd-8a8b-536665f5c820',
              'f1073e32-334d-4b2f-9b1a-355ed74fc77f',
              '6eb6bc71-7ef5-4f5e-a8e7-749c6d374f89',
              '2f3b4739-377a-437d-b4aa-2406244d5f7a',
              'c6fb37a5-c567-422d-bb08-d2ff75c6b1df',
              '2106e1d0-3ca0-4d54-a8a7-2ad8ee4f82e8')
    AND project_id = '6f3a50a3-8701-42ae-b1c5-aa6e8a4ad0d8';

-- ---------------------------------------------------------------------
-- projex_crm project   (project_id = '6c0fc1d6-5174-498b-a0c6-eb0498b3f92c')
-- 11 features → tasks may have been duplicated by an interrupted bulk
-- create call; the WHERE on feature_id catches both copies.
-- ---------------------------------------------------------------------

SELECT 'projex_crm tasks to delete' AS label,
       COUNT(*) AS n
FROM tasks
WHERE feature_id IN ( 'f3a26da9-24ae-4429-ac0f-7fbdff77dfdf', -- AWS & MCP Settings Tab (per-user)
 '96a373ec-beae-4a78-a7b4-9bfd83d83f17', -- Per-Tenant MCP Launch Orchestrator
 '938b3bdb-e7b0-46f0-9b9a-5fb0ccca7775', -- mcp_instances Table + State Machine
 '3bcac246-7aac-4b5f-bb91-7aaaf12eebf9', -- Per-Tenant MCP Status Dashboard
 '2f9890b3-a59e-438d-b156-d6d3ef947942', -- Per-Step Campaign Progress + Cross-Campaign Aggregate
 'ec7d9999-7a7b-4ca9-ad2a-d29cb1b3938b', -- Heartbeat Command Channel (CRM → MCP)
 'c63b9ab2-7adc-43b4-83a0-3e43d17ad027', -- Image Version Compatibility Matrix + 426 Gate
 '8c440f8c-57d7-4a92-901b-6938d3b3cc1f', -- Master Refined-Contacts Pool (Hashed, Push-Only)
 '77080649-1309-4180-9a5e-3d247311bb4b', -- SSE-KMS Direct-to-S3 Upload + Auto-Delete
 'db29e472-b2d3-4158-8467-8e078f64af14', -- Starter Tier Fallback (Shared Hosting Boundary)
 '327941db-2570-42ab-aae0-9d62e110811e' -- Admin BYOC Fleet Overview (LeadPulse-side)
);


DELETE
FROM tasks
WHERE feature_id IN ( 'f3a26da9-24ae-4429-ac0f-7fbdff77dfdf',
                      '96a373ec-beae-4a78-a7b4-9bfd83d83f17',
                      '938b3bdb-e7b0-46f0-9b9a-5fb0ccca7775',
                      '3bcac246-7aac-4b5f-bb91-7aaaf12eebf9',
                      '2f9890b3-a59e-438d-b156-d6d3ef947942',
                      'ec7d9999-7a7b-4ca9-ad2a-d29cb1b3938b',
                      'c63b9ab2-7adc-43b4-83a0-3e43d17ad027',
                      '8c440f8c-57d7-4a92-901b-6938d3b3cc1f',
                      '77080649-1309-4180-9a5e-3d247311bb4b',
                      'db29e472-b2d3-4158-8467-8e078f64af14',
                      '327941db-2570-42ab-aae0-9d62e110811e')
    AND project_id = '6c0fc1d6-5174-498b-a0c6-eb0498b3f92c';


DELETE
FROM features
WHERE id IN ( 'f3a26da9-24ae-4429-ac0f-7fbdff77dfdf',
              '96a373ec-beae-4a78-a7b4-9bfd83d83f17',
              '938b3bdb-e7b0-46f0-9b9a-5fb0ccca7775',
              '3bcac246-7aac-4b5f-bb91-7aaaf12eebf9',
              '2f9890b3-a59e-438d-b156-d6d3ef947942',
              'ec7d9999-7a7b-4ca9-ad2a-d29cb1b3938b',
              'c63b9ab2-7adc-43b4-83a0-3e43d17ad027',
              '8c440f8c-57d7-4a92-901b-6938d3b3cc1f',
              '77080649-1309-4180-9a5e-3d247311bb4b',
              'db29e472-b2d3-4158-8467-8e078f64af14',
              '327941db-2570-42ab-aae0-9d62e110811e')
    AND project_id = '6c0fc1d6-5174-498b-a0c6-eb0498b3f92c';

-- ---------------------------------------------------------------------
-- Verify both projects are clean.
-- ---------------------------------------------------------------------

SELECT 'remaining BYOC features (LeadPulseMcp)' AS label,
       COUNT(*) AS n
FROM features
WHERE id IN ( 'c2d5b154-0b4d-483f-97af-85ff9dd18c04',
              'f9e68d45-5aad-4902-a661-082bc72f2135',
              '1ddb167c-08a2-4da5-91ce-43c3f0f84c5e',
              'cb0f7452-2096-45fd-8a8b-536665f5c820',
              'f1073e32-334d-4b2f-9b1a-355ed74fc77f',
              '6eb6bc71-7ef5-4f5e-a8e7-749c6d374f89',
              '2f3b4739-377a-437d-b4aa-2406244d5f7a',
              'c6fb37a5-c567-422d-bb08-d2ff75c6b1df',
              '2106e1d0-3ca0-4d54-a8a7-2ad8ee4f82e8');


SELECT 'remaining BYOC features (projex_crm)' AS label,
       COUNT(*) AS n
FROM features
WHERE id IN ( 'f3a26da9-24ae-4429-ac0f-7fbdff77dfdf',
              '96a373ec-beae-4a78-a7b4-9bfd83d83f17',
              '938b3bdb-e7b0-46f0-9b9a-5fb0ccca7775',
              '3bcac246-7aac-4b5f-bb91-7aaaf12eebf9',
              '2f9890b3-a59e-438d-b156-d6d3ef947942',
              'ec7d9999-7a7b-4ca9-ad2a-d29cb1b3938b',
              'c63b9ab2-7adc-43b4-83a0-3e43d17ad027',
              '8c440f8c-57d7-4a92-901b-6938d3b3cc1f',
              '77080649-1309-4180-9a5e-3d247311bb4b',
              'db29e472-b2d3-4158-8467-8e078f64af14',
              '327941db-2570-42ab-aae0-9d62e110811e');

-- If the COUNTs above are 0, COMMIT. Otherwise ROLLBACK and investigate.
-- COMMIT;
-- ROLLBACK;
