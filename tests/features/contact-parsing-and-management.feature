@feature_id:2d385824-caf3-4fb3-b44f-ec8c3dfd57f5
@epic_id:060d6488-2270-4889-96d5-0bcabbd9f83f
Feature: Contact Parsing and Management
  Functionality to parse contact lists and manage refined contacts for campaigns.

  @scenario_id:c08b7e10-01a0-4199-aca3-a2f63fc11a32
  @scenario_type:UI
  @ui_test
  Scenario: Users can upload contact lists
    # Scenario ID: c08b7e10-01a0-4199-aca3-a2f63fc11a32
    # Feature ID: 2d385824-caf3-4fb3-b44f-ec8c3dfd57f5
    # Scenario Type: UI
    # Description: Users can upload contact lists
    Given User is on the contact management page
    When User selects a contact list file to upload
    Then The contact list is successfully uploaded
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=2d385824-caf3-4fb3-b44f-ec8c3dfd57f5, scenario_id=c08b7e10-01a0-4199-aca3-a2f63fc11a32, type=UI

  @scenario_id:136e5b3a-f033-4408-b9b9-4da88565c069
  @scenario_type:UI
  @ui_test
  Scenario: Contacts are parsed and validated
    # Scenario ID: 136e5b3a-f033-4408-b9b9-4da88565c069
    # Feature ID: 2d385824-caf3-4fb3-b44f-ec8c3dfd57f5
    # Scenario Type: UI
    # Description: Contacts are parsed and validated
    Given User has uploaded a contact list
    When The system parses the uploaded contact list
    Then The contacts are validated and errors are shown for invalid entries
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=2d385824-caf3-4fb3-b44f-ec8c3dfd57f5, scenario_id=136e5b3a-f033-4408-b9b9-4da88565c069, type=UI

  @scenario_id:1610808a-3eb3-47e2-91f9-1fe5ec1da114
  @scenario_type:API
  @api_test
  Scenario: Refined contacts are stored for future use
    # Scenario ID: 1610808a-3eb3-47e2-91f9-1fe5ec1da114
    # Feature ID: 2d385824-caf3-4fb3-b44f-ec8c3dfd57f5
    # Scenario Type: API
    # Description: Refined contacts are stored for future use
    Given User has validated the contacts
    When User saves the refined contacts
    Then The refined contacts are stored successfully for future use
    # Priority: low
    # Status: draft
    # Test Runner Info: feature_id=2d385824-caf3-4fb3-b44f-ec8c3dfd57f5, scenario_id=1610808a-3eb3-47e2-91f9-1fe5ec1da114, type=API

  @scenario_id:b31d9c0c-72db-40d0-b9d9-11b25a74554a
  @scenario_type:UI
  @ui_test
  Scenario: Users can delete or update contacts
    # Scenario ID: b31d9c0c-72db-40d0-b9d9-11b25a74554a
    # Feature ID: 2d385824-caf3-4fb3-b44f-ec8c3dfd57f5
    # Scenario Type: UI
    # Description: Users can delete or update contacts
    Given User has a list of stored contacts
    When User selects a contact to delete or update
    Then The selected contact is deleted or updated successfully
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=2d385824-caf3-4fb3-b44f-ec8c3dfd57f5, scenario_id=b31d9c0c-72db-40d0-b9d9-11b25a74554a, type=UI
