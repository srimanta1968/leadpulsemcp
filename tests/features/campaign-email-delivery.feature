@feature_id:f10e9e1c-cd01-4d13-8120-4b701b4e5b38
@epic_id:060d6488-2270-4889-96d5-0bcabbd9f83f
Feature: Campaign Email Delivery
  Manage the process of creating, scheduling, and sending campaign emails to contacts.

  @scenario_id:7dec2e9f-8243-453d-a811-40af449e5852
  @scenario_type:UI
  @ui_test
  Scenario: Users can create a new campaign
    # Scenario ID: 7dec2e9f-8243-453d-a811-40af449e5852
    # Feature ID: f10e9e1c-cd01-4d13-8120-4b701b4e5b38
    # Scenario Type: UI
    # Description: Users can create a new campaign
    Given User is on the campaign creation page
    When User fills in the campaign details
    And User clicks on the create campaign button
    Then A new campaign is created successfully
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=f10e9e1c-cd01-4d13-8120-4b701b4e5b38, scenario_id=7dec2e9f-8243-453d-a811-40af449e5852, type=UI

  @scenario_id:a10379da-4921-4ced-8174-a33a05a2f112
  @scenario_type:UI
  @ui_test
  Scenario: Users can schedule campaigns for later sending
    # Scenario ID: a10379da-4921-4ced-8174-a33a05a2f112
    # Feature ID: f10e9e1c-cd01-4d13-8120-4b701b4e5b38
    # Scenario Type: UI
    # Description: Users can schedule campaigns for later sending
    Given User has created a campaign
    When User selects a date and time for scheduling the campaign
    And User clicks on the schedule button
    Then The campaign is scheduled for later sending
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=f10e9e1c-cd01-4d13-8120-4b701b4e5b38, scenario_id=a10379da-4921-4ced-8174-a33a05a2f112, type=UI

  @scenario_id:8fcfd522-5678-4739-8fd5-7adce35b7043
  @scenario_type:UI
  @ui_test
  Scenario: Campaigns are sent to selected contacts
    # Scenario ID: 8fcfd522-5678-4739-8fd5-7adce35b7043
    # Feature ID: f10e9e1c-cd01-4d13-8120-4b701b4e5b38
    # Scenario Type: UI
    # Description: Campaigns are sent to selected contacts
    Given User has scheduled a campaign
    When User selects contacts to send the campaign to
    And User clicks on the send campaign button
    Then The campaign is sent to the selected contacts
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=f10e9e1c-cd01-4d13-8120-4b701b4e5b38, scenario_id=8fcfd522-5678-4739-8fd5-7adce35b7043, type=UI

  @scenario_id:99f2b65e-6a41-4b85-bc5d-96d6945873f9
  @scenario_type:API
  @api_test
  Scenario: Success and error logs of sent campaigns are recorded
    # Scenario ID: 99f2b65e-6a41-4b85-bc5d-96d6945873f9
    # Feature ID: f10e9e1c-cd01-4d13-8120-4b701b4e5b38
    # Scenario Type: API
    # Description: Success and error logs of sent campaigns are recorded
    Given User has sent a campaign
    When The campaign is processed by the MCP
    Then Success and error logs are recorded
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=f10e9e1c-cd01-4d13-8120-4b701b4e5b38, scenario_id=99f2b65e-6a41-4b85-bc5d-96d6945873f9, type=API
