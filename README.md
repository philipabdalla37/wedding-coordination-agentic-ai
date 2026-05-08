# Wedding Coordinator Agent

An AI-powered tool for professional wedding coordinators. It pulls data from Gmail, Google Sheets, and Google Drive, then uses Claude to generate a detailed day-of wedding schedule — complete with conflict resolution, coordinator logic, buffer times, and flagged issues.

---

## What It Does

1. **Fetches data** from four sources:
   - Local example schedule PDFs (style references)
   - Client emails from Gmail (via label)
   - A Google Sheets wedding planning document
   - Vendor contract PDFs from Google Drive

2. **Caches everything** locally in `temp_data/` so you don't re-fetch from Google on every run.

3. **Generates a schedule** by sending all data to Claude, which acts as an experienced wedding coordinator. It resolves conflicts between emails and contracts, applies professional coordinator logic (buffers, vendor arrivals, sequencing), and outputs a structured JSON schedule with a human-readable .txt version.

4. **Answers questions** via an interactive chat mode where you can ask anything about the client's wedding and get data-driven answers in plain English.

---

## Project Structure

```
wedding-agent/
│
├── agent.py                  # Main entry point and interactive menu
├── chat.py                   # Q&A chat mode powered by Claude
├── google_auth.py            # Google OAuth2 authentication
│
├── readers/
│   ├── gmail_reader.py       # Fetches emails from Gmail by label
│   ├── sheets_reader.py      # Reads all tabs from a Google Sheet
│   ├── contracts_reader.py   # Downloads and extracts vendor contract PDFs from Drive
│   └── examples_reader.py    # Reads example schedule PDFs from local /examples folder
│
├── examples/                 # Place past schedule PDFs here (style references)
├── temp_data/                # Auto-created. Stores cached JSON data between runs
│
├── credentials.json          # Google OAuth2 app credentials (from Google Cloud Console)
├── token.pickle              # Auto-created after first login. Stores your OAuth token
├── .env                      # Stores your ANTHROPIC_API_KEY
└── requirements.txt          # Python dependencies
```

---

## Setup

### 1. Clone the repository and install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up your Anthropic API key

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_api_key_here
```

### 3. Set up Google API access

This project requires access to Gmail, Google Sheets, and Google Drive.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable the following APIs:
   - Gmail API
   - Google Sheets API
   - Google Drive API
4. Go to **APIs & Services -> Credentials -> Create Credentials -> OAuth 2.0 Client ID**
5. Select **Desktop App** as the application type
6. Download the credentials file and save it as `credentials.json` in the project root

On first run, a browser window will open asking you to log in to Google and approve the requested permissions. After approval, a `token.pickle` file is created automatically and used for all future runs without requiring another login.

### 4. Add example schedules

Place past wedding schedule PDFs in the `/examples` folder. These are used as style references — Claude will match the tone, structure, and level of detail found in these documents when generating new schedules.

### 5. Set up Gmail labels

For each client, create a Gmail label whose name exactly matches the `CLIENT_NAME` you will configure in `agent.py` (e.g. `Ereny & Mattew`). Apply that label to all relevant emails for that client — from the couple, vendors, and any related parties.

---

## Configuration

Open `agent.py` and update the three constants at the bottom of the file:

```python
CLIENT_NAME         = "Ereny & Mattew"
SPREADSHEET_ID      = "your_google_sheet_id_here"
CONTRACTS_FOLDER_ID = "your_google_drive_folder_id_here"
```

- **CLIENT_NAME** — must exactly match the Gmail label name for this client (case-insensitive)
- **SPREADSHEET_ID** — found in the Google Sheets URL: `docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`
- **CONTRACTS_FOLDER_ID** — found in the Google Drive folder URL: `drive.google.com/drive/folders/<CONTRACTS_FOLDER_ID>`

When switching to a new client, update these three values and clear the cache via menu option 6.

---

## Running the Agent

```bash
python agent.py
```

This opens the interactive menu:

```
Current temp_data cache status:
---------------------------------------------
  [cached]  Example schedules              (saved 2026-05-01 14:22, 12KB)
  [cached]  Client emails                  (saved 2026-05-03 09:45, 84KB)
  [missing] Google Sheet data              (not cached)
  [missing] Vendor contracts               (not cached)

=============================================
  WEDDING AGENT — Ereny & Mattew
=============================================
  1. Refresh example schedules
  2. Refresh client emails
  3. Refresh Google Sheet data
  4. Refresh vendor contracts
  5. Refresh ALL data sources
  6. Clear all cached data
  7. Generate wedding schedule
  8. Ask anything about this client
  9. Exit
=============================================
```

### Recommended first-run workflow

1. Choose **5** to fetch and cache all data sources
2. Review the cache status to confirm everything loaded
3. Choose **7** to generate the wedding schedule
4. Find the output files in the project root:
   - `schedule_ereny_and_mattew.json` — structured schedule data
   - `schedule_ereny_and_mattew.txt` — human-readable version for printing or sharing

---

## Generated Schedule Output

The schedule JSON contains four sections:

| Section | Description |
|---|---|
| `overrides_from_emails` | Changes Claude found where a later email overrides a contract or sheet |
| `schedule` | Every 30-minute slot from start to end of the day, including buffer time |
| `coordinator_summary` | A short paragraph summarising the day flow and key risks |
| `flagged_issues` | Items that need confirmation or have a potential timing problem |

Each schedule slot includes the time, event, location, assigned vendor or person, the source it came from (e.g. "Photography Contract", "Email 12"), coordinator notes, and buffer time after the slot.

---

## Chat Mode

Choose option **8** from the menu to ask plain English questions about the client's wedding:

```
You: What time does the ceremony start?
You: Who is the makeup artist?
You: Has the limo pickup time changed in any emails?
You: What info is still outstanding?
```

Claude writes a Python query against the cached data, runs it locally in a sandbox, and answers based on what the data actually says — not from memory.

---

## Google Sheet Structure

The planning sheet should contain tabs that the agent can reference. Recommended tab names:

| Tab | Contents |
|---|---|
| `Overall Schedule` | Master timeline of the day |
| `Vendor Info` | Vendor names, contacts, and service details |
| `Bridal Party Info` | Names and roles of the bridal party |
| `Reception Info` | Dinner courses, speeches, dances, entertainment |
| `Outstanding Info` | Items still needing confirmation |
| `Ceremony Info` | Processional order, officiant details |

Tab names can differ — the agent reads all tabs regardless of name and Claude interprets their contents.

---

## Caching

All fetched data is stored as JSON files in `temp_data/`:

| File | Contents |
|---|---|
| `examples.json` | Extracted text from example schedule PDFs |
| `emails.json` | All client emails sorted oldest to newest |
| `sheets.json` | All tabs and rows from the planning sheet |
| `contracts.json` | Extracted text from vendor contract PDFs |

Each source can be refreshed independently from the menu. The cache is shared between schedule generation and chat mode, so refreshing emails in the menu is immediately reflected in the next chat session.

> **Note:** If you change the `SCOPES` list in `google_auth.py`, delete `token.pickle` and re-authenticate so the updated permissions take effect.

---

## Security Notes

- `credentials.json` and `token.pickle` contain sensitive authentication data — do not commit them to version control
- `.env` contains your Anthropic API key — do not commit it to version control
- Add all three to your `.gitignore`:

```
credentials.json
token.pickle
.env
temp_data/
```

- The chat mode sandbox (`safe_exec` in `chat.py`) restricts Claude-generated code to a whitelist of safe built-ins — no file access, imports, or system calls are permitted

---

## Requirements

- Python 3.8+
- A Google account with Gmail, Sheets, and Drive access
- An Anthropic API key ([get one here](https://console.anthropic.com/))