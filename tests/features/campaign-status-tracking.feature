@feature_id:1765abe4-0621-46c2-9e82-0642fa6c4cf3
@epic_id:060d6488-2270-4889-96d5-0bcabbd9f83f
Feature: Campaign Status Tracking
  Track and manage the status of sent campaigns and their outcomes.

  @scenario_id:292e5aa4-6a9d-41cc-b8e6-c1696ccb9d9b
  @scenario_type:UI
  @ui_test
  Scenario: Users can view the status of sent campaigns
    # Scenario ID: 292e5aa4-6a9d-41cc-b8e6-c1696ccb9d9b
    # Feature ID: 1765abe4-0621-46c2-9e82-0642fa6c4cf3
    # Scenario Type: UI
    # Description: Users can view the status of sent campaigns
    Given The campaign has been sent and is processed by the system
    When The user is logged into the Leadpulse MCP system
    Then The user accesses the campaign status dashboard
    And The user selects a sent campaign
    And The system displays the current status of the selected campaign
    # Priority: high
    # Status: draft
    # Test Runner Info: feature_id=1765abe4-0621-46c2-9e82-0642fa6c4cf3, scenario_id=292e5aa4-6a9d-41cc-b8e6-c1696ccb9d9b, type=UI

  @scenario_id:a00d9d27-697d-48a4-89f5-c0bd1799f372
  @scenario_type:UI
  @ui_test
  Scenario: Campaign performance metrics are available
    # Scenario ID: a00d9d27-697d-48a4-89f5-c0bd1799f372
    # Feature ID: 1765abe4-0621-46c2-9e82-0642fa6c4cf3
    # Scenario Type: UI
    # Description: Campaign performance metrics are available
    Given At least one campaign has been sent and metrics have been recorded
    When The user is on the campaign performance dashboard
    Then The user navigates to the campaign performance metrics section
    And The system displays the relevant performance metrics for the selected campaign
    And The user can view metrics such as open rates, click rates, and conversions
    # Priority: high
    # Status: draft
    # Test Runner Info: feature_id=1765abe4-0621-46c2-9e82-0642fa6c4cf3, scenario_id=a00d9d27-697d-48a4-89f5-c0bd1799f372, type=UI

  @scenario_id:f932ffee-165e-468b-8edb-2c0be074ab45
  @scenario_type:UI
  @ui_test
  Scenario: Errors in sending are logged and retried if possible
    # Scenario ID: f932ffee-165e-468b-8edb-2c0be074ab45
    # Feature ID: 1765abe4-0621-46c2-9e82-0642fa6c4cf3
    # Scenario Type: UI
    # Description: Errors in sending are logged and retried if possible
    Given The campaign is in the process of being sent
    When A campaign sending process encounters an error
    Then The system logs any errors encountered during the sending process
    And The system attempts to retry sending the email if the error is retryable
    And The user can view the error logs for the campaign in the dashboard
    # Priority: high
    # Status: draft
    # Test Runner Info: feature_id=1765abe4-0621-46c2-9e82-0642fa6c4cf3, scenario_id=f932ffee-165e-468b-8edb-2c0be074ab45, type=UI
