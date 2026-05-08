# readers/sheets_reader.py
#
# PURPOSE:
# This module reads all tabs from a Google Sheets spreadsheet and returns
# their contents as a structured dictionary. The spreadsheet acts as the
# central planning document for each client wedding — it contains tabs like
# "Overall Schedule", "Vendor Info", "Bridal Party Info", "Reception Info",
# "Outstanding Info", and others that Claude uses to build the day-of schedule.
#
# The data returned here is injected into the Claude prompt in agent.py as
# Section 2 (wedding planning sheet data), and is also made available to the
# chat.py Q&A mode where Claude generates Python code to query it at runtime.
#
# DEPENDENCIES:
#   - google-api-python-client  : for Google Sheets API access
from googleapiclient.discovery import build

def get_all_sheets_data(creds, spreadsheet_id):
    """
    Reads every tab in a Google Sheets spreadsheet and returns all rows
    from each tab as a dictionary keyed by tab name.

    The function makes two types of API calls:
      1. A metadata call to retrieve the list of all tab names in the workbook.
      2. One values call per tab to fetch its row data.

    Empty tabs are skipped and not included in the output. This avoids
    polluting the prompt or the in-memory data with blank sheets.

    Args:
        creds (google.oauth2.credentials.Credentials):
            OAuth2 credentials from google_auth.py. Must include the
            'https://www.googleapis.com/auth/spreadsheets.readonly' scope.

        spreadsheet_id (str):
            The Google Sheets document ID, found in the spreadsheet URL:
            docs.google.com/spreadsheets/d/<spreadsheet_id>/edit

    Returns:
        dict[str, list[list[str]]]: A dictionary where each key is a tab
            name (str) and each value is a list of rows. Each row is itself
            a list of cell values as strings.

            The first row of each tab is typically the header row.
            Trailing empty cells in a row are omitted by the Sheets API.

        Example:
            {
                "Overall Schedule": [
                    ["Time", "Event", "Location", ...],  # header row
                    ["9:00 AM", "Coordinators Arrive", "Hotel", ...],
                    ...
                ],
                "Vendor Info": [
                    ["Vendor", "Contact", "Phone", ...],
                    ["JustKlik", "John", "416-555-0100", ...],
                    ...
                ]
            }

    Notes:
        - Tab order in the returned dict follows the order tabs appear in
          the spreadsheet, since sheet_names is built from the API's ordered
          response.
        - TODO: Try reading each sheet in the best was possible (ex, vendor info)
    """
    # Build the Google Sheets API v4 client using the authenticated credentials
    service = build('sheets', 'v4', credentials=creds)

    # Get the list of all sheet/tab names in this workbook
    spreadsheet = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id
    ).execute()

    # Extract just the title of each tab from the metadata response
    sheet_names = [
        sheet['properties']['title']
        for sheet in spreadsheet['sheets']
    ]

    print(f"Found {len(sheet_names)} sheet(s): {', '.join(sheet_names)}")

    # Read data from every tab
    all_data = {}
    for name in sheet_names:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=name
        ).execute()

        rows = result.get('values', [])
        if rows:
            all_data[name] = rows
            print(f"   '{name}' — {len(rows)} rows read")
        else:
            # Skip empty tabs entirely rather than storing an empty list,
            # to keep the prompt and in-memory data clean.
            print(f"   '{name}' — empty, skipping")

    return all_data