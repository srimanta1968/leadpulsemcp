#!/usr/bin/env node
/**
 * ProjexLight MCP Stdio Bridge
 *
 * Bridges MCP JSON-RPC protocol to ProjexLight HTTP API
 *
 * Usage:
 *   node mcp-bridge.js
 *
 * Environment Variables:
 *   MCP_SERVER_URL - HTTP API URL (default: http://localhost:8766)
 *   SESSION_TOKEN - Encrypted session token for authentication (required)
 *   PROJECT_ID - Project UUID for authentication (required)
 */

const http = require('http');
const https = require('https');
const readline = require('readline');
const fs = require('fs');
const path = require('path');

// Configuration
// MCP_SERVER_URL points to the LOCAL Docker MCP server (not the backend API)
// The Docker MCP server then connects to the backend API (api.projexlight.com)
const MCP_SERVER_URL = process.env.MCP_SERVER_URL || 'http://localhost:8766';
const DEBUG = process.env.MCP_DEBUG === 'true';

// Authentication credentials - loaded from environment or .projexlight/config.json
let SESSION_TOKEN = process.env.SESSION_TOKEN || '';
let PROJECT_ID = process.env.PROJECT_ID || '';

// Project path for multi-project support
// Converts Windows paths to Unix format for Docker compatibility
let PROJECT_PATH = '';

function getProjectPath() {
  if (PROJECT_PATH) return PROJECT_PATH;

  // Use bridge's parent directory (project root) instead of process.cwd()
  // __dirname = mcp-server/, parent = project root — reliable regardless of CWD
  let projectDir = path.dirname(__dirname);

  // Convert Windows path to Unix format (C:\Users\name -> /c/Users/name)
  projectDir = projectDir.replace(/\\/g, '/');
  if (projectDir.length >= 2 && projectDir[1] === ':') {
    const drive = projectDir[0].toLowerCase();
    projectDir = '/' + drive + projectDir.slice(2);
  }

  PROJECT_PATH = projectDir;
  if (DEBUG) {
    console.error(`[MCP Bridge] Detected project path: ${PROJECT_PATH}`);
  }
  return PROJECT_PATH;
}

// Try to load credentials from config file (NOT the API URL - that's always localhost)
// Search order: __dirname-relative paths first (reliable regardless of CWD),
// then process.cwd()-relative paths as fallback.
function loadCredentialsFromConfig() {
  // __dirname = mcp-server/ directory where this bridge lives
  // Parent of __dirname = project root
  const bridgeDir = __dirname;
  const projectRoot = path.dirname(bridgeDir);
  const configPaths = [
    // Primary: relative to bridge location (works regardless of CWD)
    path.join(projectRoot, '.projexlight', 'config.json'),
    path.join(bridgeDir, 'config.json'),
    path.join(bridgeDir, 'mcp-config.json'),
    // Fallback: relative to CWD (legacy support)
    path.join(process.cwd(), '.projexlight', 'config.json'),
    path.join(process.cwd(), 'mcp-server', 'config.json'),
  ];

  for (const configPath of configPaths) {
    try {
      if (fs.existsSync(configPath)) {
        const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

        // Load session token if not set via environment
        // Check both .projexlight/config.json format (sessionToken)
        // and mcp-config.json format (encryptedPlatformApiKey)
        if (!SESSION_TOKEN) {
          SESSION_TOKEN = config.sessionToken || config.encryptedPlatformApiKey || '';
        }

        // Load project ID if not set via environment
        if (config.projectId && !PROJECT_ID) {
          PROJECT_ID = config.projectId;
        }

        if (DEBUG) {
          console.error(`[MCP Bridge] Loaded credentials from ${configPath}`);
        }
        break;
      }
    } catch (e) {
      // Ignore config loading errors
    }
  }
}

loadCredentialsFromConfig();

// Tool definitions for MCP protocol
// projectPath is optional but recommended for multi-project setups
const TOOLS = [
  {
    name: 'projexlight_init_session',
    description: 'Initialize code generation session and get assigned tasks. For multi-project setups, projectPath routes credentials correctly.',
    inputSchema: {
      type: 'object',
      properties: {
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root (e.g., /c/Users/name/project). Auto-detected if not provided.'
        }
      },
      required: []
    }
  },
  {
    name: 'projexlight_get_instruction',
    description: 'Get detailed implementation instructions for a specific task',
    inputSchema: {
      type: 'object',
      properties: {
        taskId: {
          type: 'string',
          description: 'The UUID of the task to get instructions for'
        },
        taskType: {
          type: 'string',
          description: 'Type of task (api_endpoint, frontend, database, etc.)',
          enum: ['api_endpoint', 'frontend', 'backend', 'database', 'service', 'ui_component', 'testing']
        },
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root for multi-project setups. Auto-detected if not provided.'
        }
      },
      required: ['taskId']
    }
  },
  {
    name: 'projexlight_validate',
    description: 'Validate generated code against quality rules',
    inputSchema: {
      type: 'object',
      properties: {
        taskId: {
          type: 'string',
          description: 'The UUID of the task being validated'
        },
        taskType: {
          type: 'string',
          description: 'Type of task'
        },
        codeSnippets: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              filePath: { type: 'string' },
              content: { type: 'string' }
            },
            required: ['filePath', 'content']
          },
          description: 'Array of code files to validate'
        },
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root for multi-project setups. Auto-detected if not provided.'
        }
      },
      required: ['taskId', 'codeSnippets']
    }
  },
  {
    name: 'projexlight_complete_task',
    description: 'Mark a task as complete and get the next task',
    inputSchema: {
      type: 'object',
      properties: {
        taskId: {
          type: 'string',
          description: 'The UUID of the completed task'
        },
        metrics: {
          type: 'object',
          properties: {
            filesGenerated: { type: 'number' },
            linesOfCode: { type: 'number' },
            violationsDetected: { type: 'number' },
            complianceScore: { type: 'number' }
          },
          required: ['filesGenerated', 'linesOfCode', 'complianceScore']
        },
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root for multi-project setups. Auto-detected if not provided.'
        }
      },
      required: ['taskId', 'metrics']
    }
  },
  {
    name: 'projexlight_get_rules',
    description: 'Get code generation rules and best practices',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_decision_tree',
    description: 'Get decision tree for specific coding scenarios',
    inputSchema: {
      type: 'object',
      properties: {
        scenario: {
          type: 'string',
          description: 'The scenario to get decision logic for',
          enum: ['file_creation', 'error_handling', 'database_query', 'api_design', 'validation']
        }
      },
      required: ['scenario']
    }
  },
  {
    name: 'projexlight_quality_gates',
    description: 'Get quality gate requirements for code validation',
    inputSchema: {
      type: 'object',
      properties: {
        taskType: {
          type: 'string',
          description: 'Type of task to get quality gates for'
        }
      },
      required: []
    }
  },
  {
    name: 'projexlight_get_template',
    description: 'Get code template for specific task type',
    inputSchema: {
      type: 'object',
      properties: {
        taskType: {
          type: 'string',
          description: 'Type of task to get template for'
        },
        framework: {
          type: 'string',
          description: 'Framework to use (express, react, etc.)'
        }
      },
      required: ['taskType']
    }
  },
  {
    name: 'projexlight_self_check',
    description: 'Get self-check validation checklist to run before writing files',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_submit_feature_validation',
    description: 'Submit feature validation results after completing all tasks in a feature',
    inputSchema: {
      type: 'object',
      properties: {
        featureId: {
          type: 'string',
          description: 'The UUID of the feature being validated'
        },
        status: {
          type: 'string',
          enum: ['validated', 'validation_failed', 'partial'],
          description: 'Validation status: validated (all pass), validation_failed (any fail), partial (some incomplete)'
        },
        overallResult: {
          type: 'string',
          enum: ['pass', 'fail', 'partial'],
          description: 'Overall result: pass (all criteria met), fail (criteria not met), partial (some criteria met)'
        },
        acceptanceCriteriaResults: {
          type: 'array',
          description: 'Results for each acceptance criterion',
          items: {
            type: 'object',
            properties: {
              criterion: { type: 'string' },
              status: { type: 'string', enum: ['implemented', 'partial', 'not_implemented'] },
              notes: { type: 'string' }
            }
          }
        },
        scenarioResults: {
          type: 'array',
          description: 'Results for each test scenario',
          items: {
            type: 'object',
            properties: {
              scenarioId: { type: 'string' },
              status: { type: 'string', enum: ['pass', 'fail', 'partial'] },
              issues: { type: 'array', items: { type: 'string' } }
            }
          }
        }
      },
      required: ['featureId', 'status', 'overallResult']
    }
  },
  {
    name: 'projexlight_get_pending_violations',
    description: 'Get pending coding violations from pre-commit hook. Call after git commit to check for issues that need fixing.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_clear_violations',
    description: 'Clear pending violations after fixing. Call after fixing all issues and before git commit --amend.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_get_pending_test_failures',
    description: 'Get pending test failures from pre-push hook. Call after git push attempt to check for failed tests. Returns auto-marked manual tests and failures requiring fix.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_clear_test_failures',
    description: 'Clear pending test failures after fixing. Call after all tests pass before pushing again.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_mark_test_manual',
    description: 'Mark a specific test as manual (requires user approval). Only use after 3+ failures and user confirmation.',
    inputSchema: {
      type: 'object',
      properties: {
        apiDefinitionPath: {
          type: 'string',
          description: 'Path to the api_definition JSON file'
        },
        testName: {
          type: 'string',
          description: 'Name of the test case to mark as manual'
        },
        reason: {
          type: 'string',
          description: 'Reason for marking as manual test'
        }
      },
      required: ['apiDefinitionPath', 'reason']
    }
  },
  {
    name: 'projexlight_reset_failure_counts',
    description: 'Reset failure counts for all tests. Use after major code changes or environment fixes.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_set_context',
    description: 'Set project context for multi-project setups. Call once at start of session to ensure correct credentials are used.',
    inputSchema: {
      type: 'object',
      properties: {
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root (e.g., /c/Users/name/project)'
        }
      },
      required: ['projectPath']
    }
  },
  {
    name: 'projexlight_get_context',
    description: 'Get current project context. Shows which project credentials are active.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  // ==================== LEGACY IMPORT TOOLS ====================
  // Tools for importing extracted Epic/Feature/Scenario structure to Projexlight
  {
    name: 'projexlight_import_validate',
    description: 'Validate import data (epics.json, features.json, scenarios.json) before importing. Checks for required fields, valid cross-references, and detects duplicates.',
    inputSchema: {
      type: 'object',
      properties: {
        epics: {
          type: 'object',
          description: 'Contents of epics.json (with "epics" array)',
          properties: {
            epics: { type: 'array', items: { type: 'object' } }
          }
        },
        features: {
          type: 'object',
          description: 'Contents of features.json (with "features" array)',
          properties: {
            features: { type: 'array', items: { type: 'object' } }
          }
        },
        scenarios: {
          type: 'object',
          description: 'Contents of scenarios.json (with "scenarios" array)',
          properties: {
            scenarios: { type: 'array', items: { type: 'object' } }
          }
        }
      },
      required: ['epics', 'features', 'scenarios']
    }
  },
  {
    name: 'projexlight_import_full',
    description: 'Import complete Epic → Feature → Scenario structure in one request. Automatically creates ID mappings and handles duplicates. Recommended for most imports.',
    inputSchema: {
      type: 'object',
      properties: {
        manifest: {
          type: 'object',
          description: 'Import manifest with metadata (optional)',
          properties: {
            manifest_version: { type: 'string' },
            project_name: { type: 'string' },
            source_type: { type: 'string' }
          }
        },
        epics: {
          type: 'object',
          description: 'Contents of epics.json',
          properties: {
            epics: { type: 'array', items: { type: 'object' } }
          }
        },
        features: {
          type: 'object',
          description: 'Contents of features.json',
          properties: {
            features: { type: 'array', items: { type: 'object' } }
          }
        },
        scenarios: {
          type: 'object',
          description: 'Contents of scenarios.json',
          properties: {
            scenarios: { type: 'array', items: { type: 'object' } }
          }
        }
      },
      required: ['epics', 'features', 'scenarios']
    }
  },
  {
    name: 'projexlight_import_epics',
    description: 'Import only epics from epics.json. Returns epic_id_mapping (temp_id → real_id) needed for feature import. Use when doing step-by-step import.',
    inputSchema: {
      type: 'object',
      properties: {
        epics: {
          type: 'array',
          description: 'Array of epic objects from epics.json',
          items: {
            type: 'object',
            properties: {
              temp_id: { type: 'string' },
              title: { type: 'string' },
              description: { type: 'string' },
              source_module: { type: 'string' }
            },
            required: ['temp_id', 'title', 'description']
          }
        }
      },
      required: ['epics']
    }
  },
  {
    name: 'projexlight_import_features',
    description: 'Import features with epic_id_mapping. Each feature\'s epic_temp_id is resolved to real epic_id. Use after projexlight_import_epics.',
    inputSchema: {
      type: 'object',
      properties: {
        features: {
          type: 'array',
          description: 'Array of feature objects from features.json',
          items: {
            type: 'object',
            properties: {
              temp_id: { type: 'string' },
              epic_temp_id: { type: 'string' },
              title: { type: 'string' },
              description: { type: 'string' },
              source_feature_file: { type: 'string' }
            },
            required: ['temp_id', 'epic_temp_id', 'title', 'description']
          }
        },
        epic_id_mapping: {
          type: 'object',
          description: 'Mapping from epic temp_id to real Projexlight ID (from projexlight_import_epics result)',
          additionalProperties: { type: 'string' }
        }
      },
      required: ['features', 'epic_id_mapping']
    }
  },
  {
    name: 'projexlight_import_scenarios',
    description: 'Import scenarios with feature_id_mapping. Each scenario\'s feature_temp_id is resolved to real feature_id. Use after projexlight_import_features.',
    inputSchema: {
      type: 'object',
      properties: {
        scenarios: {
          type: 'array',
          description: 'Array of scenario objects from scenarios.json',
          items: {
            type: 'object',
            properties: {
              temp_id: { type: 'string' },
              feature_temp_id: { type: 'string' },
              title: { type: 'string' },
              description: { type: 'string' },
              source_file: { type: 'string' }
            },
            required: ['temp_id', 'feature_temp_id', 'title']
          }
        },
        feature_id_mapping: {
          type: 'object',
          description: 'Mapping from feature temp_id to real Projexlight ID (from projexlight_import_features result)',
          additionalProperties: { type: 'string' }
        }
      },
      required: ['scenarios', 'feature_id_mapping']
    }
  },
  {
    name: 'projexlight_import_status',
    description: 'Get import history and status for the project. Shows previous imports, counts, and whether duplicates would be detected.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  // ==================== API TESTING TOOLS ====================
  // Tools for running and validating API tests from api_definitions
  // RECOMMENDED: Use projexlight_start_api_tests (async) for large projects
  {
    name: 'projexlight_run_api_tests',
    description: 'Run API tests SYNCHRONOUSLY (blocks until complete). For large projects with 100+ APIs, use projexlight_start_api_tests instead to avoid timeout. Use api_test_mode="incremental" for testing only changed APIs or "full" for all APIs.',
    inputSchema: {
      type: 'object',
      properties: {
        api_test_mode: {
          type: 'string',
          enum: ['incremental', 'full'],
          description: 'Test mode: "incremental" tests only changed/new APIs (default), "full" tests ALL APIs in api_definitions'
        },
        feature_id: {
          type: 'string',
          description: 'Optional: Test only APIs for a specific feature. Leave empty to test all.'
        },
        api_paths: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional: Array of specific API definition file paths to test'
        }
      },
      required: []
    }
  },
  {
    name: 'projexlight_start_api_tests',
    description: 'Start API tests ASYNCHRONOUSLY and return immediately with a testRunId. RECOMMENDED for large projects. Poll projexlight_get_api_test_status to check progress and get results when complete. Call projexlight_clear_api_test_result after processing results.',
    inputSchema: {
      type: 'object',
      properties: {
        api_test_mode: {
          type: 'string',
          enum: ['incremental', 'full'],
          description: 'Test mode: "incremental" tests only changed/new APIs (default), "full" tests ALL APIs in api_definitions'
        },
        feature_id: {
          type: 'string',
          description: 'Optional: Test only APIs for a specific feature. Leave empty to test all.'
        },
        api_paths: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional: Array of specific API definition file paths to test'
        }
      },
      required: []
    }
  },
  {
    name: 'projexlight_get_api_test_status',
    description: 'Check API test status. Returns: "running" (with elapsed time), "completed" (with full results in result field), or "idle". When status is "completed", the result field contains the test results.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_cancel_api_tests',
    description: 'Cancel currently running API tests if stuck. Use after checking status shows tests running for too long.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_clear_api_test_result',
    description: 'Clear stored test result after processing. Call this after projexlight_get_api_test_status returns "completed" and you have processed the results.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  {
    name: 'projexlight_check_server_health',
    description: 'CRITICAL: Check if the local application server is running BEFORE running API tests. Returns server status, detected port, and health endpoint response. If server is not running, API tests will fail.',
    inputSchema: {
      type: 'object',
      properties: {
        port: {
          type: 'number',
          description: 'Expected port the server should be running on (default: 3000)'
        },
        health_endpoint: {
          type: 'string',
          description: 'Health endpoint to check (default: /health). Common alternatives: /api/health, /healthz, /ping'
        },
        base_url: {
          type: 'string',
          description: 'Optional: Full base URL to check (e.g., http://localhost:3000). If provided, port is ignored.'
        }
      },
      required: []
    }
  },
  {
    name: 'projexlight_validate_api_definition',
    description: 'Validate API definition JSON structure before running tests. Checks required fields (endpoint, method, testCases) and ID references (taskId, featureId, epicId).',
    inputSchema: {
      type: 'object',
      properties: {
        api_definition: {
          type: 'object',
          description: 'The API definition object to validate',
          properties: {
            endpoint: { type: 'string' },
            method: { type: 'string' },
            taskId: { type: 'string' },
            featureId: { type: 'string' },
            epicId: { type: 'string' },
            sprintId: { type: 'string' },
            testCases: { type: 'array', items: { type: 'object' } }
          },
          required: ['endpoint', 'method', 'testCases']
        },
        file_path: {
          type: 'string',
          description: 'Optional: Path to the API definition file for context in error messages'
        }
      },
      required: ['api_definition']
    }
  },
  // ==================== LEGACY API EXTRACTION TOOLS ====================
  // Tools for extracting APIs from existing codebases
  {
    name: 'projexlight_legacy_detect_framework',
    description: 'Detect the framework used in an existing project. Returns framework name, language, and confidence score. Use this as the first step in legacy API extraction.',
    inputSchema: {
      type: 'object',
      properties: {
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root (e.g., /c/Users/name/project)'
        }
      },
      required: ['projectPath']
    }
  },
  {
    name: 'projexlight_legacy_scan_routes',
    description: 'Scan an existing project for API routes. Returns list of routes with method, path, source file, and line number. Supports Express, FastAPI, Flask, Django, Spring Boot, Gin, Rails, and Laravel.',
    inputSchema: {
      type: 'object',
      properties: {
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root'
        },
        framework: {
          type: 'string',
          description: 'Optional framework hint (express, fastapi, flask, django, springboot, gin, rails, laravel). Auto-detected if not provided.',
          enum: ['express', 'fastapi', 'flask', 'django', 'springboot', 'gin', 'rails', 'laravel']
        }
      },
      required: ['projectPath']
    }
  },
  {
    name: 'projexlight_legacy_extract_apis',
    description: 'Extract APIs from an existing project and generate API definition JSON files. This is the main endpoint for legacy API extraction - it detects framework, scans routes, and generates test-ready API definitions.',
    inputSchema: {
      type: 'object',
      properties: {
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root'
        },
        outputDir: {
          type: 'string',
          description: 'Optional output directory for API definitions. Default: {projectPath}/tests/api_definitions'
        },
        framework: {
          type: 'string',
          description: 'Optional framework hint for faster detection'
        }
      },
      required: ['projectPath']
    }
  },
  {
    name: 'projexlight_legacy_generate_definitions',
    description: 'Generate API definition files from previously scanned routes. Use after projexlight_legacy_scan_routes if you want more control over the generation.',
    inputSchema: {
      type: 'object',
      properties: {
        projectPath: {
          type: 'string',
          description: 'Unix-style path to project root'
        },
        routes: {
          type: 'array',
          description: 'Optional array of route objects from scan-routes. If not provided, will re-scan.',
          items: {
            type: 'object',
            properties: {
              method: { type: 'string' },
              path: { type: 'string' },
              file: { type: 'string' },
              line: { type: 'number' }
            }
          }
        },
        outputDir: {
          type: 'string',
          description: 'Output directory for API definitions'
        }
      },
      required: ['projectPath']
    }
  },
  {
    name: 'projexlight_legacy_bootstrap',
    description: 'Get the complete legacy extraction workflow. Use this instead of projexlight_init_session when working with existing projects that need API extraction rather than new feature development.',
    inputSchema: {
      type: 'object',
      properties: {},
      required: []
    }
  },
  // ==================== TASK CREATION TOOLS ====================
  // Tools for creating tasks programmatically (used in Task 8 of legacy automation)
  {
    name: 'projexlight_create_task',
    description: 'Create a new task in Projexlight. Used for creating API validation tasks per feature in legacy automation workflow.',
    inputSchema: {
      type: 'object',
      properties: {
        title: {
          type: 'string',
          description: 'Task title, e.g., "Validate User Authentication APIs"'
        },
        description: {
          type: 'string',
          description: 'Task description explaining what needs to be done'
        },
        task_type: {
          type: 'string',
          enum: ['api_endpoint', 'frontend', 'backend', 'database', 'service', 'testing', 'development'],
          description: 'Type of task'
        },
        feature_id: {
          type: 'string',
          description: 'UUID of parent feature (from import result)'
        },
        epic_id: {
          type: 'string',
          description: 'UUID of parent epic (from import result)'
        },
        sprint_id: {
          type: 'string',
          description: 'UUID of sprint to add task to'
        },
        priority: {
          type: 'string',
          enum: ['low', 'medium', 'high', 'critical'],
          description: 'Task priority (default: high)'
        },
        acceptance_criteria: {
          type: 'array',
          items: { type: 'string' },
          description: 'List of acceptance criteria'
        }
      },
      required: ['title', 'description', 'task_type', 'feature_id', 'epic_id', 'sprint_id']
    }
  },
  {
    name: 'projexlight_get_tasks_by_feature',
    description: 'Get all tasks for a specific feature. Useful for checking if API validation tasks already exist.',
    inputSchema: {
      type: 'object',
      properties: {
        feature_id: {
          type: 'string',
          description: 'UUID of the feature to get tasks for'
        }
      },
      required: ['feature_id']
    }
  },
  // ==================== IMPLEMENT TOOL ====================
  {
    name: 'projexlight_implement',
    description: 'Get complete implementation instructions for an epic, feature, or scenario. Returns a step-by-step workflow using Projexlight MCP tools.',
    inputSchema: {
      type: 'object',
      properties: {
        entityType: { type: 'string', enum: ['epic', 'feature', 'scenario'], description: 'Type of entity to implement' },
        entityId: { type: 'string', description: 'UUID of the epic, feature, or scenario' }
      },
      required: ['entityType', 'entityId']
    }
  },
  // ==================== ENTITY LOOKUP TOOLS ====================
  // Tools for getting epics/features/scenarios/tasks by ID and listing children.
  // Enables workflow: get epic → find gaps → create missing features/tasks → code
  {
    name: 'projexlight_get_epic',
    description: 'Get an epic by its ID with details. Use to inspect an epic before creating features or tasks for it.',
    inputSchema: {
      type: 'object',
      properties: {
        epicId: {
          type: 'string',
          description: 'UUID of the epic to retrieve'
        }
      },
      required: ['epicId']
    }
  },
  {
    name: 'projexlight_get_feature',
    description: 'Get a feature by its ID with details including scenarios and tasks summary. Use to inspect a feature before creating scenarios or tasks.',
    inputSchema: {
      type: 'object',
      properties: {
        featureId: {
          type: 'string',
          description: 'UUID of the feature to retrieve'
        }
      },
      required: ['featureId']
    }
  },
  {
    name: 'projexlight_get_scenario',
    description: 'Get a scenario by its ID with full details including BDD steps.',
    inputSchema: {
      type: 'object',
      properties: {
        scenarioId: {
          type: 'string',
          description: 'UUID of the scenario to retrieve'
        }
      },
      required: ['scenarioId']
    }
  },
  {
    name: 'projexlight_update_scenario_steps',
    description: 'Update a scenario BDD steps (Given/When/Then) with actual UI selectors after implementation. Makes the scenario automatable with Playwright/Cypress.',
    inputSchema: {
      type: 'object',
      properties: {
        scenarioId: { type: 'string', description: 'UUID of the scenario to update' },
        given_conditions: { type: 'string', description: 'Updated Given steps with real page paths and UI selectors' },
        when_actions: { type: 'string', description: 'Updated When steps with real UI interactions' },
        then_expectations: { type: 'string', description: 'Updated Then steps with real UI assertions' }
      },
      required: ['scenarioId', 'given_conditions', 'when_actions', 'then_expectations']
    }
  },
  {
    name: 'projexlight_create_scenario',
    description: 'Create a new BDD scenario under an existing feature with Given/When/Then steps. Use when implementing an epic or feature that needs new scenarios.',
    inputSchema: {
      type: 'object',
      properties: {
        featureId: { type: 'string', description: 'UUID of the parent feature' },
        title: { type: 'string', description: 'Scenario title' },
        description: { type: 'string', description: 'Scenario description' },
        scenario_type: { type: 'string', enum: ['UI', 'API'], description: 'Type (default: UI)' },
        given_conditions: { type: 'string', description: 'BDD Given steps' },
        when_actions: { type: 'string', description: 'BDD When steps' },
        then_expectations: { type: 'string', description: 'BDD Then steps' },
        priority: { type: 'string', enum: ['low', 'medium', 'high', 'critical'], description: 'Priority (default: medium)' }
      },
      required: ['featureId', 'title', 'given_conditions', 'when_actions', 'then_expectations']
    }
  },
  {
    name: 'projexlight_get_task',
    description: 'Get a task by its ID with full details.',
    inputSchema: {
      type: 'object',
      properties: {
        taskId: {
          type: 'string',
          description: 'UUID of the task to retrieve'
        }
      },
      required: ['taskId']
    }
  },
  {
    name: 'projexlight_list_epics',
    description: 'List all epics for the project. Use to find which epics exist before deciding what to create.',
    inputSchema: {
      type: 'object',
      properties: {
        status: {
          type: 'string',
          description: 'Filter by status (optional)'
        },
        priority: {
          type: 'string',
          description: 'Filter by priority (optional)'
        },
        search: {
          type: 'string',
          description: 'Search term to filter epics (optional)'
        },
        limit: {
          type: 'number',
          description: 'Number of results (default: 50)'
        },
        offset: {
          type: 'number',
          description: 'Pagination offset (default: 0)'
        }
      },
      required: []
    }
  },
  {
    name: 'projexlight_get_features_by_epic',
    description: 'Get all features for a specific epic. Use to identify which features already exist and which need to be created for this epic.',
    inputSchema: {
      type: 'object',
      properties: {
        epicId: {
          type: 'string',
          description: 'UUID of the epic to get features for'
        }
      },
      required: ['epicId']
    }
  },
  {
    name: 'projexlight_get_scenarios_by_feature',
    description: 'Get all scenarios for a specific feature. Use to identify which BDD scenarios already exist and which need to be created.',
    inputSchema: {
      type: 'object',
      properties: {
        featureId: {
          type: 'string',
          description: 'UUID of the feature to get scenarios for'
        },
        includeCompleted: {
          type: 'string',
          description: 'Include completed scenarios (default: true)',
          enum: ['true', 'false']
        }
      },
      required: ['featureId']
    }
  },
  {
    name: 'projexlight_get_tasks_by_feature_lookup',
    description: 'Get all tasks for a specific feature with optional status filter. Use to identify which tasks already exist and which need to be created.',
    inputSchema: {
      type: 'object',
      properties: {
        featureId: {
          type: 'string',
          description: 'UUID of the feature to get tasks for'
        },
        status: {
          type: 'string',
          description: 'Filter by task status (optional)'
        }
      },
      required: ['featureId']
    }
  },
  {
    name: 'projexlight_get_tasks_by_scenario',
    description: 'Get all tasks for a specific scenario. Use to identify which tasks already exist for a scenario.',
    inputSchema: {
      type: 'object',
      properties: {
        scenarioId: {
          type: 'string',
          description: 'UUID of the scenario to get tasks for'
        }
      },
      required: ['scenarioId']
    }
  },
  {
    name: 'projexlight_create_tasks_bulk',
    description: 'Create multiple tasks at once for a feature. More efficient than creating tasks one by one. Each task requires title, description, task_type, feature_id, epic_id, sprint_id.',
    inputSchema: {
      type: 'object',
      properties: {
        tasks: {
          type: 'array',
          description: 'Array of task objects to create',
          items: {
            type: 'object',
            properties: {
              title: { type: 'string', description: 'Task title' },
              description: { type: 'string', description: 'Task description' },
              task_type: {
                type: 'string',
                enum: ['api_endpoint', 'frontend', 'backend', 'database', 'service', 'testing', 'development'],
                description: 'Type of task'
              },
              feature_id: { type: 'string', description: 'UUID of parent feature' },
              epic_id: { type: 'string', description: 'UUID of parent epic' },
              sprint_id: { type: 'string', description: 'UUID of sprint' },
              priority: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] },
              acceptance_criteria: { type: 'array', items: { type: 'string' } }
            },
            required: ['title', 'description', 'task_type', 'feature_id', 'epic_id', 'sprint_id']
          }
        }
      },
      required: ['tasks']
    }
  }
];

// Map MCP tool names to HTTP endpoints
// These paths match the Docker MCP server routes (without /mcp/ prefix)
// The Docker server handles forwarding to the backend API if needed
const TOOL_ENDPOINTS = {
  'projexlight_init_session': { method: 'POST', path: '/api/instruction/init' },
  'projexlight_get_instruction': { method: 'POST', path: '/api/instruction/get' },
  'projexlight_validate': { method: 'POST', path: '/api/instruction/validate' },
  'projexlight_complete_task': { method: 'POST', path: '/api/instruction/complete' },
  'projexlight_get_rules': { method: 'GET', path: '/api/instruction/rules' },
  'projexlight_decision_tree': { method: 'POST', path: '/api/instruction/decision-tree' },
  'projexlight_quality_gates': { method: 'POST', path: '/api/instruction/quality-gates' },
  'projexlight_get_template': { method: 'POST', path: '/api/instruction/template' },
  'projexlight_self_check': { method: 'GET', path: '/api/instruction/self-check' },
  'projexlight_submit_feature_validation': { method: 'POST', path: '/api/instruction/submit-feature-validation' },
  'projexlight_get_pending_violations': { method: 'GET', path: '/api/instruction/pending-violations' },
  'projexlight_clear_violations': { method: 'POST', path: '/api/instruction/clear-violations' },
  'projexlight_get_pending_test_failures': { method: 'GET', path: '/api/instruction/pending-test-failures' },
  'projexlight_clear_test_failures': { method: 'POST', path: '/api/instruction/clear-test-failures' },
  'projexlight_mark_test_manual': { method: 'POST', path: '/api/instruction/mark-test-manual' },
  'projexlight_reset_failure_counts': { method: 'POST', path: '/api/instruction/reset-failure-counts' },
  'projexlight_set_context': { method: 'POST', path: '/api/context/set' },
  'projexlight_get_context': { method: 'GET', path: '/api/context/current' },
  // Legacy Import Tools - Forward to Projexlight Backend API
  'projexlight_import_validate': { method: 'POST', path: '/api/import/validate' },
  'projexlight_import_full': { method: 'POST', path: '/api/import/full' },
  'projexlight_import_epics': { method: 'POST', path: '/api/import/epics' },
  'projexlight_import_features': { method: 'POST', path: '/api/import/features' },
  'projexlight_import_scenarios': { method: 'POST', path: '/api/import/scenarios' },
  'projexlight_import_status': { method: 'GET', path: '/api/import/status' },
  // API Testing Tools - Run tests from api_definitions
  'projexlight_run_api_tests': { method: 'POST', path: '/api/test' },
  'projexlight_start_api_tests': { method: 'POST', path: '/api/test/start' },
  'projexlight_get_api_test_status': { method: 'GET', path: '/api/test/status' },
  'projexlight_cancel_api_tests': { method: 'POST', path: '/api/test/cancel' },
  'projexlight_clear_api_test_result': { method: 'POST', path: '/api/test/clear' },
  'projexlight_check_server_health': { method: 'POST', path: '/api/test/server-health' },
  'projexlight_validate_api_definition': { method: 'POST', path: '/api/validate-definition' },
  // Task Creation Tools - Forward to Projexlight Backend API
  'projexlight_create_task': { method: 'POST', path: '/api/tasks/create' },
  'projexlight_get_tasks_by_feature': { method: 'POST', path: '/api/tasks/by-feature' },
  // Legacy API Extraction Tools - Extract APIs from existing codebases
  'projexlight_legacy_detect_framework': { method: 'POST', path: '/api/legacy/detect-framework' },
  'projexlight_legacy_scan_routes': { method: 'POST', path: '/api/legacy/scan-routes' },
  'projexlight_legacy_extract_apis': { method: 'POST', path: '/api/legacy/extract-apis' },
  'projexlight_legacy_generate_definitions': { method: 'POST', path: '/api/legacy/generate-definitions' },
  'projexlight_legacy_bootstrap': { method: 'GET', path: '/api/legacy/bootstrap' },
  // Entity Lookup Tools - Get by ID and list children
  'projexlight_get_epic': { method: 'POST', path: '/api/epics/get' },
  'projexlight_get_feature': { method: 'POST', path: '/api/features/get' },
  'projexlight_get_scenario': { method: 'POST', path: '/api/scenarios/get' },
  'projexlight_get_task': { method: 'POST', path: '/api/tasks/get' },
  'projexlight_list_epics': { method: 'POST', path: '/api/epics/list' },
  'projexlight_get_features_by_epic': { method: 'POST', path: '/api/epics/features' },
  'projexlight_get_scenarios_by_feature': { method: 'POST', path: '/api/features/scenarios' },
  'projexlight_get_tasks_by_feature_lookup': { method: 'POST', path: '/api/features/tasks' },
  'projexlight_get_tasks_by_scenario': { method: 'POST', path: '/api/scenarios/tasks' },
  'projexlight_create_tasks_bulk': { method: 'POST', path: '/api/tasks/create-bulk' },
  'projexlight_implement': { method: 'POST', path: '/api/implement' },
  'projexlight_update_scenario_steps': { method: 'POST', path: '/api/scenarios/update-steps' },
  'projexlight_create_scenario': { method: 'POST', path: '/api/scenarios/create' }
};

// Timeout configuration per endpoint type
const ENDPOINT_TIMEOUTS = {
  '/api/test': 180000,           // Sync API tests: 3 minutes (use async instead for large projects)
  '/api/test/start': 10000,      // Async start: 10 seconds (returns immediately)
  '/api/test/status': 10000,     // Status check: 10 seconds
  '/api/test/cancel': 10000,     // Cancel: 10 seconds
  '/api/test/clear': 10000,      // Clear result: 10 seconds
  'default': 30000               // Default: 30 seconds
};

function getTimeoutForPath(path) {
  for (const [endpoint, timeout] of Object.entries(ENDPOINT_TIMEOUTS)) {
    if (endpoint !== 'default' && path.startsWith(endpoint)) {
      return timeout;
    }
  }
  return ENDPOINT_TIMEOUTS.default;
}

// HTTP request helper
function makeHttpRequest(method, path, body = null) {
  return new Promise((resolve, reject) => {
    // Check if we have required credentials for authenticated endpoints
    const isAuthenticatedEndpoint = (path.includes('/instruction/') || path.includes('/import/') || path.includes('/api/epics/') || path.includes('/api/features/') || path.includes('/api/scenarios/') || path.includes('/api/tasks/')) && !path.includes('/health');
    if (isAuthenticatedEndpoint && (!SESSION_TOKEN || !PROJECT_ID)) {
      reject(new Error('Missing SESSION_TOKEN or PROJECT_ID. Please ensure credentials are set via environment variables or .projexlight/config.json'));
      return;
    }

    const url = new URL(path, MCP_SERVER_URL);
    const isHttps = url.protocol === 'https:';
    const lib = isHttps ? https : http;

    const options = {
      hostname: url.hostname,
      port: url.port || (isHttps ? 443 : 80),
      path: url.pathname + url.search,
      method: method,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      }
    };

    // Get appropriate timeout for this endpoint
    const timeout = getTimeoutForPath(path);

    if (DEBUG) {
      console.error(`[MCP Bridge] ${method} ${url.href} (timeout: ${timeout}ms)`);
    }

    // Add authentication credentials and project context to request body
    let requestBody = body || {};
    if (isAuthenticatedEndpoint && method !== 'GET') {
      // Auto-detect project path if not provided in the request
      const projectPath = requestBody.projectPath || getProjectPath();

      requestBody = {
        ...requestBody,
        sessionToken: SESSION_TOKEN,
        projectId: PROJECT_ID,
        projectPath: projectPath  // For multi-project credential routing
      };

      if (DEBUG) {
        console.error(`[MCP Bridge] Request includes projectPath: ${projectPath}`);
      }
    }

    const req = lib.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve(json);
          } else {
            reject(new Error(json.error || json.message || `HTTP ${res.statusCode}`));
          }
        } catch (e) {
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve({ raw: data });
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${data}`));
          }
        }
      });
    });

    req.on('error', reject);
    req.setTimeout(timeout, () => {
      req.destroy();
      reject(new Error(`Request timeout after ${timeout/1000}s. For large test suites, use projexlight_start_api_tests (async) instead.`));
    });

    if (method !== 'GET') {
      req.write(JSON.stringify(requestBody));
    }
    req.end();
  });
}

// Check if MCP server is healthy
async function checkHealth() {
  try {
    await makeHttpRequest('GET', '/health');
    return true;
  } catch (e) {
    return false;
  }
}

// Handle MCP JSON-RPC requests
async function handleRequest(request) {
  const { jsonrpc, id, method, params } = request;

  if (jsonrpc !== '2.0') {
    return { jsonrpc: '2.0', id, error: { code: -32600, message: 'Invalid JSON-RPC version' } };
  }

  try {
    switch (method) {
      case 'initialize':
        return {
          jsonrpc: '2.0',
          id,
          result: {
            protocolVersion: '2024-11-05',
            capabilities: {
              tools: {}
            },
            serverInfo: {
              name: 'projexlight-mcp-bridge',
              version: '1.0.0'
            }
          }
        };

      case 'initialized':
        // Notification, no response needed
        return null;

      case 'tools/list':
        return {
          jsonrpc: '2.0',
          id,
          result: {
            tools: TOOLS
          }
        };

      case 'tools/call':
        const { name, arguments: args } = params;
        const endpoint = TOOL_ENDPOINTS[name];

        if (!endpoint) {
          return {
            jsonrpc: '2.0',
            id,
            error: { code: -32601, message: `Unknown tool: ${name}` }
          };
        }

        try {
          const result = await makeHttpRequest(endpoint.method, endpoint.path, args);
          return {
            jsonrpc: '2.0',
            id,
            result: {
              content: [
                {
                  type: 'text',
                  text: JSON.stringify(result, null, 2)
                }
              ]
            }
          };
        } catch (error) {
          return {
            jsonrpc: '2.0',
            id,
            result: {
              content: [
                {
                  type: 'text',
                  text: `Error calling ${name}: ${error.message}`
                }
              ],
              isError: true
            }
          };
        }

      case 'ping':
        return { jsonrpc: '2.0', id, result: {} };

      default:
        return {
          jsonrpc: '2.0',
          id,
          error: { code: -32601, message: `Method not found: ${method}` }
        };
    }
  } catch (error) {
    return {
      jsonrpc: '2.0',
      id,
      error: { code: -32603, message: error.message }
    };
  }
}

// Main stdio loop
async function main() {
  // Check server health on startup
  const healthy = await checkHealth();
  if (!healthy) {
    console.error('[MCP Bridge] Warning: ProjexLight MCP server not reachable at ' + MCP_SERVER_URL);
    console.error('[MCP Bridge] Make sure the Docker container is running: cd mcp-server && ./setup-all.sh');
  }

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false
  });

  rl.on('line', async (line) => {
    if (!line.trim()) return;

    try {
      const request = JSON.parse(line);
      const response = await handleRequest(request);

      if (response) {
        console.log(JSON.stringify(response));
      }
    } catch (error) {
      console.log(JSON.stringify({
        jsonrpc: '2.0',
        id: null,
        error: { code: -32700, message: 'Parse error: ' + error.message }
      }));
    }
  });

  rl.on('close', () => {
    process.exit(0);
  });
}

main().catch(error => {
  console.error('[MCP Bridge] Fatal error:', error);
  process.exit(1);
});
