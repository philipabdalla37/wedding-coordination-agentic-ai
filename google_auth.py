# google_auth.py
#
# PURPOSE:
# This module handles all Google OAuth2 authentication for the project.
# It produces a valid credentials object that is passed to the Gmail,
# Sheets, and Drive API clients in the reader modules.
#
# The first time this runs, it opens a browser window asking the user to
# log in to their Google account and grant the requested permissions. Once
# approved, the credentials are saved locally to token.pickle so subsequent
# runs skip the browser step entirely and authenticate silently.
#
# If the saved credentials have expired (access tokens typically last 1 hour),
# they are refreshed automatically using the stored refresh token without
# requiring another browser login. A new browser login is only needed if the
# token.pickle file is deleted, the refresh token is revoked, or the SCOPES
# list is changed.
#
# CREDENTIALS FILE:
# credentials.json must be present in the project root. It is downloaded from
# the Google Cloud Console (APIs & Services -> Credentials -> OAuth 2.0 Client)
# and identifies this application to Google. It is not a user token — it does
# not grant access on its own. It should be kept out of version control.
#
# DEPENDENCIES:
#   - google-auth               : Credentials class and Request (for refresh)
#   - google-auth-oauthlib      : InstalledAppFlow for the browser-based login
#   - pickle                    : serializing and deserializing the credentials
#   - os                        : checking whether token.pickle exists on diskfrom google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os, pickle

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

def get_credentials():
    """
    Returns a valid set of Google OAuth2 credentials, handling all three
    possible authentication states automatically:

      1. No token on disk  : opens a browser for the user to log in and
                              grant permissions, then saves the result.
      2. Valid token       : loads it from disk and returns immediately,
                              no network call or browser needed.
      3. Expired token     : refreshes it silently using the stored refresh
                              token, saves the updated token, and returns.

    This function is called once at the start of any data fetch operation
    in agent.py, and the returned credentials object is passed down to
    each reader module (gmail_reader, sheets_reader, contracts_reader).

    Returns:
        google.oauth2.credentials.Credentials:
            A valid, non-expired credentials object scoped to the
            permissions defined in SCOPES above.

    Side effects:
        - On first run or after token deletion: opens a browser window
          for Google login and writes a new token.pickle to the project root.
        - On expired token: makes a network request to Google's token
          endpoint and overwrites token.pickle with refreshed credentials.
        - On valid token: reads token.pickle, no writes or network calls.

    Files:
        token.pickle     : stores the user's OAuth2 credentials between runs.
                            Created automatically on first successful login.
                            Safe to delete to force a fresh login.
        credentials.json : the app's OAuth2 client configuration, downloaded
                            from Google Cloud Console. Must exist before
                            running this function for the first time.
    """
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as f:
            pickle.dump(creds, f)
    return creds