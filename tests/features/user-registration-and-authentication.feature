@feature_id:8709509f-2c5b-45c2-ba84-1c564d34a736
@epic_id:060d6488-2270-4889-96d5-0bcabbd9f83f
Feature: User Registration and Authentication
  Implement user registration, login, and session management to allow users to access the platform securely.

  @scenario_id:8aedf85c-1c33-4b20-838b-2ff252a9e163
  @scenario_type:UI
  @ui_test
  Scenario: Users can register with email and password
    # Scenario ID: 8aedf85c-1c33-4b20-838b-2ff252a9e163
    # Feature ID: 8709509f-2c5b-45c2-ba84-1c564d34a736
    # Scenario Type: UI
    # Description: Users can register with email and password
    Given User is on the registration page
    When User enters a valid email and password
    Then User is successfully registered and redirected to the login page
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=8709509f-2c5b-45c2-ba84-1c564d34a736, scenario_id=8aedf85c-1c33-4b20-838b-2ff252a9e163, type=UI

  @scenario_id:1c43cfa4-d426-4e95-935d-18f215b141af
  @scenario_type:UI
  @ui_test
  Scenario: Users can log in with correct credentials
    # Scenario ID: 1c43cfa4-d426-4e95-935d-18f215b141af
    # Feature ID: 8709509f-2c5b-45c2-ba84-1c564d34a736
    # Scenario Type: UI
    # Description: Users can log in with correct credentials
    Given User is on the login page
    When User enters valid email and password
    Then User is successfully logged in and redirected to the dashboard
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=8709509f-2c5b-45c2-ba84-1c564d34a736, scenario_id=1c43cfa4-d426-4e95-935d-18f215b141af, type=UI

  @scenario_id:0fab7146-ce7e-4305-ad9d-32a5b1271cec
  @scenario_type:UI
  @ui_test
  Scenario: Users receive error messages for invalid inputs
    # Scenario ID: 0fab7146-ce7e-4305-ad9d-32a5b1271cec
    # Feature ID: 8709509f-2c5b-45c2-ba84-1c564d34a736
    # Scenario Type: UI
    # Description: Users receive error messages for invalid inputs
    Given User is on the registration page
    When User enters an invalid email or password
    Then User sees an error message indicating invalid input
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=8709509f-2c5b-45c2-ba84-1c564d34a736, scenario_id=0fab7146-ce7e-4305-ad9d-32a5b1271cec, type=UI

  @scenario_id:ee962d4d-b01a-4579-9bd9-99354e49a575
  @scenario_type:UI
  @ui_test
  Scenario: User sessions are maintained securely
    # Scenario ID: ee962d4d-b01a-4579-9bd9-99354e49a575
    # Feature ID: 8709509f-2c5b-45c2-ba84-1c564d34a736
    # Scenario Type: UI
    # Description: User sessions are maintained securely
    Given User has logged in successfully
    When User navigates away from the page and returns later
    Then User is still logged in and redirected to the dashboard
    # Priority: medium
    # Status: draft
    # Test Runner Info: feature_id=8709509f-2c5b-45c2-ba84-1c564d34a736, scenario_id=ee962d4d-b01a-4579-9bd9-99354e49a575, type=UI
