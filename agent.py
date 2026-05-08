# agent.py
#
# PURPOSE:
# This is the main entry point and orchestration layer for the Wedding
# Coordinator Agent. It ties together all the reader modules, the caching
# system, the schedule generator, and the chat interface into a single
# interactive terminal menu.
#
# WHAT IT DOES:
# For each client wedding, this module:
#   1. Fetches data from four sources: local example PDFs, Gmail emails,
#      a Google Sheet, and vendor contract PDFs in Google Drive.
#   2. Caches each data source as a JSON file in temp_data/ so subsequent
#      runs don't need to re-fetch from Google's APIs.
#   3. On demand, formats all four data sources into a structured prompt
#      and sends it to Claude to generate a complete day-of wedding schedule.
#   4. Saves the generated schedule in both JSON and human-readable .txt format.
#   5. Provides a chat mode (via chat.py) for ad-hoc questions about the client.
#
# CACHING STRATEGY:
# Each data source is cached independently. The coordinator can refresh just
# emails (e.g. after a late vendor change) without re-fetching contracts or
# the sheet. The cache is also shared with chat.py, which reads the same
# temp_data/ files for its Q&A sessions.
#
# TO SWITCH CLIENTS:
# Change the three constants at the bottom of this file (CLIENT_NAME,
# SPREADSHEET_ID, CONTRACTS_FOLDER_ID) and clear the cache via menu
# option 6 before generating a new schedule.
#
# DEPENDENCIES:
#   - anthropic                          : Claude API for schedule generation
#   - python-dotenv                      : loading ANTHROPIC_API_KEY from .env
#   - google_auth                        : OAuth2 credentials for Google APIs
#   - readers.gmail_reader               : fetching client emails from Gmail
#   - readers.sheets_reader              : reading the wedding planning sheet
#   - readers.contracts_reader           : reading vendor contract PDFs from Drive
#   - readers.examples_reader            : reading example schedule PDFs locally
#   - chat                               : interactive Q&A mode

import os
import json
import datetime
import anthropic
from dotenv import load_dotenv
from google_auth import get_credentials
from readers.gmail_reader import get_emails_by_label
from readers.sheets_reader import get_all_sheets_data
from readers.contracts_reader import get_contracts_from_folder
from readers.examples_reader import get_schedule_examples
from chat import start_chat

# Load environment variables from .env, specifically ANTHROPIC_API_KEY
load_dotenv()

# Directory where all cached JSON data files are stored between runs
TEMP_DIR = "temp_data"


# ─────────────────────────────────────────────────────────────────────
# CACHE HELPERS
# ─────────────────────────────────────────────────────────────────────

def ensure_temp_dir():
    """
    Creates the temp_data/ directory if it does not already exist.

    Called before any write operation to guarantee the directory is present.
    exist_ok=True means no error is raised if the directory already exists.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)


def cache_path(filename):
    """
    Returns the full path for a given cache filename inside temp_data/.

    Args:
        filename (str): The cache filename, e.g. "emails.json".

    Returns:
        str: The full relative path, e.g. "temp_data/emails.json".
    """
    return os.path.join(TEMP_DIR, filename)


def save_cache(filename, data):
    """
    Serializes a Python object to JSON and writes it to a cache file
    in temp_data/.

    Uses indent=2 for human-readable formatting and ensure_ascii=False
    to preserve non-ASCII characters (e.g. Arabic names in emails).

    Args:
        filename (str): The cache filename, e.g. "emails.json".
        data:           Any JSON-serializable Python object (list or dict).
    """
    ensure_temp_dir()
    with open(cache_path(filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"   Cached -> temp_data/{filename}")


def load_cache(filename):
    """
    Reads and deserializes a JSON cache file from temp_data/.

    Args:
        filename (str): The cache filename, e.g. "emails.json".

    Returns:
        The deserialized Python object (list or dict) if the file exists.
        None if the file does not exist.
    """
    path = cache_path(filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def cache_exists(filename):
    """
    Checks whether a cache file exists in temp_data/.

    Args:
        filename (str): The cache filename to check.

    Returns:
        bool: True if the file exists, False otherwise.
    """
    return os.path.exists(cache_path(filename))


def print_cache_status():
    """
    Prints a status table showing which data sources are currently cached,
    when they were last saved, and their file size.

    Displayed at the top of the menu on every loop iteration so the
    coordinator always knows whether the data is fresh before generating
    a schedule or starting a chat session.
    """
    files = {
        "examples.json":  "Example schedules",
        "emails.json":    "Client emails",
        "sheets.json":    "Google Sheet data",
        "contracts.json": "Vendor contracts",
    }
    print("\nCurrent temp_data cache status:")
    print("-" * 45)
    for filename, label in files.items():
        if cache_exists(filename):
            path     = cache_path(filename)
            size     = os.path.getsize(path)
            mtime    = os.path.getmtime(path)
            modified = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  [cached]  {label:<30} (saved {modified}, {size//1024}KB)")
        else:
            print(f"  [missing] {label:<30} (not cached)")
    print()


# ─────────────────────────────────────────────────────────────────────
# EMAIL SORT HELPER
# ─────────────────────────────────────────────────────────────────────

def sort_emails_by_date(emails):
    """
    Sorts a list of email dicts chronologically, oldest to newest.

    The Gmail API does not guarantee message order within or across
    threads, so this sort is applied after fetching to ensure the
    "latest email wins" rule works correctly when Claude reads them
    sequentially in the prompt.

    Emails with unparseable date headers are sorted to the beginning
    (datetime.min) rather than raising an exception, so a single
    malformed email doesn't break the entire sort.

    Args:
        emails (list[dict]): List of email dicts, each with a 'date'
                             field in RFC 2822 format.

    Returns:
        list[dict]: The same list sorted oldest to newest by date.
    """
    # Import is placed here rather than at the top of the file because
    # this is the only function that uses it, keeping the top-level
    # imports focused on the module's primary dependencies.
    from email.utils import parsedate_to_datetime

    def parse_date(e):
        try:
            return parsedate_to_datetime(e['date'])
        except Exception:
            # Fall back to the earliest possible datetime rather than
            # crashing, so malformed date headers are sorted to the front
            return datetime.datetime.min

    return sorted(emails, key=lambda e: parse_date(e))


# ─────────────────────────────────────────────────────────────────────
# FETCH & CACHE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────

def fetch_and_cache_examples():
    """
    Reads all example schedule PDFs from the local /examples folder,
    extracts their text, and saves the result to examples.json.

    Examples are style references — they show Claude the exact tone,
    structure, and level of detail expected in the output schedule.
    They are sourced locally rather than from Google Drive because they
    are static business assets that don't change per client.

    Returns:
        list[str]: List of extracted text strings, one per PDF.
                   Returns an empty list if no PDFs were found.
    """
    print("\nLoading example schedules from local /examples folder...")
    examples = get_schedule_examples("examples")
    if examples:
        save_cache("examples.json", examples)
        print(f"   {len(examples)} example(s) cached.")
    else:
        print("   No example PDFs found in /examples folder.")
    return examples


def fetch_and_cache_emails(creds, client_name, max_emails=50):
    """
    Fetches all emails from the Gmail label matching the client name,
    sorts them chronologically, and saves the result to emails.json.

    The client name is used directly as the Gmail label name, so the
    label must exist in Gmail and match exactly (case-insensitively).
    Emails are sorted oldest to newest after fetching so the "latest
    email wins" rule is enforced correctly when the data is read later.

    Args:
        creds:           Google OAuth2 credentials from get_credentials().
        client_name (str): The client name, used as the Gmail label to search.
        max_emails (int):  Maximum number of threads to fetch. Defaults to 50.

    Returns:
        list[dict]: Sorted list of email dicts. Empty list if none found.
    """
    print(f"\nFetching emails under Gmail label: '{client_name}'...")
    emails = get_emails_by_label(creds, label_name=client_name, max_results=max_emails)
    if emails:
        emails = sort_emails_by_date(emails)
        save_cache("emails.json", emails)
        print(f"   {len(emails)} email(s) cached and sorted oldest to newest.")
    else:
        print("   No emails found for this label.")
    return emails


def fetch_and_cache_sheets(creds, spreadsheet_id):
    """
    Reads all tabs from the client's Google Sheet planning document
    and saves the result to sheets.json.

    The sheet is the coordinator's central planning document and
    contains tabs like Overall Schedule, Vendor Info, Bridal Party Info,
    Reception Info, and Outstanding Info. All tabs are read so nothing
    is missed when Claude builds the schedule.

    Args:
        creds:                Google OAuth2 credentials from get_credentials().
        spreadsheet_id (str): The Google Sheets document ID from the URL.

    Returns:
        dict: Tab name -> list of rows. Empty dict if the sheet is empty.
    """
    print(f"\nReading all tabs from Google Sheet...")
    all_sheets = get_all_sheets_data(creds, spreadsheet_id)
    if all_sheets:
        save_cache("sheets.json", all_sheets)
        print(f"   {len(all_sheets)} tab(s) cached.")
    else:
        print("   Google Sheet appears to be empty.")
    return all_sheets


def fetch_and_cache_contracts(creds, contracts_folder_id):
    """
    Downloads all vendor contract PDFs from the specified Google Drive
    folder, extracts their text, and saves the result to contracts.json.

    Each contract provides binding details about vendor services, arrival
    times, deliverables, and payment terms. Claude uses this data in the
    schedule as a baseline, which emails may then override.

    Args:
        creds:                   Google OAuth2 credentials from get_credentials().
        contracts_folder_id (str): The Google Drive folder ID containing
                                   the vendor contract PDFs.

    Returns:
        list[dict]: List of contract dicts with 'vendor' and 'content' keys.
                    Empty list if no PDFs were found.
    """
    print(f"\nReading vendor contracts from Google Drive...")
    contracts = get_contracts_from_folder(creds, contracts_folder_id)
    if contracts:
        save_cache("contracts.json", contracts)
        print(f"   {len(contracts)} contract(s) cached.")
    else:
        print("   No PDF contracts found in the specified Drive folder.")
    return contracts


# ─────────────────────────────────────────────────────────────────────
# LOAD DATA (cache first, fallback to fetch)
# ─────────────────────────────────────────────────────────────────────

def load_all_data(creds, client_name, spreadsheet_id, contracts_folder_id):
    """
    Loads all four data sources into memory, using cached files where
    available and fetching from the source only when the cache is missing.

    This cache-first strategy means the coordinator can run schedule
    generation multiple times without repeatedly hitting Google's APIs.
    Individual sources can be refreshed via the menu when needed
    (e.g. after a client sends a last-minute email change).

    Args:
        creds:                   Google OAuth2 credentials.
        client_name (str):       Client name, used as Gmail label.
        spreadsheet_id (str):    Google Sheets document ID.
        contracts_folder_id (str): Google Drive folder ID for contracts.

    Returns:
        tuple: (examples, emails, all_sheets, contracts)
            Each element is the loaded data or an empty collection if
            nothing was found.
    """
    print("\nLoading data (from cache where available)...")

    # Attempt to load each source from cache first
    examples   = load_cache("examples.json")  if cache_exists("examples.json")  else None
    emails     = load_cache("emails.json")    if cache_exists("emails.json")    else None
    all_sheets = load_cache("sheets.json")    if cache_exists("sheets.json")    else None
    contracts  = load_cache("contracts.json") if cache_exists("contracts.json") else None

    # Fall back to live fetch for any source that was not cached
    if examples:   print("   Examples loaded from cache.")
    else:          examples   = fetch_and_cache_examples()

    if emails:     print("   Emails loaded from cache.")
    else:          emails     = fetch_and_cache_emails(creds, client_name)

    if all_sheets: print("   Sheet data loaded from cache.")
    else:          all_sheets = fetch_and_cache_sheets(creds, spreadsheet_id)

    if contracts:  print("   Contracts loaded from cache.")
    else:          contracts  = fetch_and_cache_contracts(creds, contracts_folder_id)

    return examples, emails, all_sheets, contracts


# ─────────────────────────────────────────────────────────────────────
# SCHEDULE GENERATOR
# ─────────────────────────────────────────────────────────────────────

def generate_wedding_schedule(client_name, spreadsheet_id, contracts_folder_id):
    """
    Generates a complete day-of wedding schedule using Claude, then saves
    the output as both a structured JSON file and a human-readable .txt file.

    This function orchestrates the full generation pipeline:
      1. Authenticates with Google and loads all data (cache-first).
      2. Formats each data source into a distinct section of a prompt.
      3. Sends the prompt to Claude with detailed coordinator instructions.
      4. Parses Claude's JSON response and writes two output files.

    The prompt instructs Claude to act as an experienced wedding coordinator
    and follow a three-step process: resolve email overrides, apply
    professional coordinator logic (buffers, sequencing, vendor arrivals),
    then output a fully structured JSON schedule with flagged issues and
    a coordinator summary.

    Output files are named using the client name, e.g.:
        schedule_ereny_and_mattew.json  -> structured data for further use
        schedule_ereny_and_mattew.txt   -> human-readable for the coordinator

    If Claude's response cannot be parsed as JSON (e.g. due to an accidental
    markdown fence), the raw output is saved to a _raw.txt file instead so
    no output is lost.

    Args:
        client_name (str):           The client name, e.g. "Ereny & Mattew".
        spreadsheet_id (str):        Google Sheets document ID.
        contracts_folder_id (str):   Google Drive folder ID for contracts.
    """

    print("\nAuthenticating with Google...")
    creds = get_credentials()

    print("\nLoading data (from cache where available)...")
    examples, emails, all_sheets, contracts = load_all_data(
        creds, client_name, spreadsheet_id, contracts_folder_id
    )

    # ── Format examples ───────────────────────────────────────────────
    # Each example is labelled and separated so Claude can clearly
    # distinguish between multiple reference schedules.
    examples_text = ""
    if examples:
        for i, ex in enumerate(examples, 1):
            examples_text += f"\n--- EXAMPLE SCHEDULE {i} ---\n{ex.strip()}\n"
    else:
        examples_text = "No examples provided."

    # ── Format emails ─────────────────────────────────────────────────
    # Emails are numbered and include a header reminding Claude that the
    # latest email takes precedence over contracts and sheet data.
    # Body text is capped at 800 characters per email to manage prompt
    # length while still providing enough context for conflict resolution.
    # Attachment metadata is included so Claude knows what documents were
    # shared, even though the attachment content itself is not extracted.
    email_text = ""
    if emails:
        email_text += "NOTE: Emails listed oldest to newest. LATEST EMAIL WINS on any conflict.\n"
        email_text += "Full email chains are included — each reply is a separate entry.\n"
        email_text += "=" * 50 + "\n"
        for i, e in enumerate(emails, 1):
            email_text += f"\n[Email {i} of {len(emails)}]\n"
            email_text += f"Date:    {e['date']}\n"
            email_text += f"From:    {e['from']}\n"
            email_text += f"Subject: {e['subject']}\n"
            email_text += f"Body:\n{e['body'][:800]}\n"

            # Include attachment filenames and sizes so Claude is aware
            # of what documents were shared, even without reading their content
            if e.get('attachments'):
                email_text += f"Attachments ({len(e['attachments'])}):\n"
                for att in e['attachments']:
                    email_text += (
                        f"  - {att['original_filename']} "
                        f"({att['mime_type']}, {att['size_kb']}KB)\n"
                    )
            email_text += "-" * 40 + "\n"
    else:
        email_text = "No emails found."

    # ── Format sheets ─────────────────────────────────────────────────
    # All tabs are included with their tab name as a header. Rows are
    # comma-joined so the structure is readable in the prompt without
    # needing to replicate a full spreadsheet format.
    sheet_text = ""
    if all_sheets:
        for tab_name, rows in all_sheets.items():
            sheet_text += f"\n--- TAB: {tab_name} ---\n"
            for row in rows:
                sheet_text += ", ".join(str(cell) for cell in row) + "\n"
    else:
        sheet_text = "No sheet data found."

    # ── Format contracts ──────────────────────────────────────────────
    # Contract content is capped at 2000 characters per vendor to keep
    # the prompt within a manageable size. The most important terms
    # (arrival times, deliverables, package details) typically appear
    # near the top of each contract.
    contracts_text = ""
    if contracts:
        for c in contracts:
            contracts_text += f"\n--- CONTRACT: {c['vendor']} ---\n"
            contracts_text += c['content'][:2000]
            contracts_text += "\n"
    else:
        contracts_text = "No contracts found."

    # Debug prints left commented in for convenience during development.
    # Uncomment to inspect the formatted prompt sections before sending to Claude.
    # print("email_text:", email_text)
    # print("sheet_text:", sheet_text)
    # print("contracts_text:", contracts_text)

    # ── Build the prompt ──────────────────────────────────────────────
    # The prompt is structured in three sections:
    #   Section 1: Example schedules (style reference)
    #   Section 2: Client data (emails, sheet, contracts)
    #   Instructions: Three-step process Claude must follow
    #
    # Double braces {{ }} are used around the JSON structure example to
    # escape them inside the f-string without being interpreted as
    # format placeholders.
    prompt = f"""
You are a professional, experienced, detail-oriented wedding day coordinator with 10+ years of experience.
Your job is to produce a detailed, accurate Day-Of Wedding Schedule
for the client below, written in the exact same style, tone, and
format as the example schedules provided. You think proactively — you anticipate what needs to happen BEFORE it needs to happen,
you build in buffer time so the day never feels rushed, and you know the behind-the-scenes
logistics that guests never see but that make everything run smoothly.

=====================================================
SECTION 1 — YOUR PAST SCHEDULE EXAMPLES (match this style exactly)
=====================================================
{examples_text}

=====================================================
SECTION 2 — CLIENT INFORMATION
=====================================================
Client / Wedding Name: {client_name}

--- EMAILS (from client, vendors, and related parties), (oldest to newest — LATEST EMAIL WINS on any conflict) ---
{email_text}

--- WEDDING PLANNING SHEET DATA ---
{sheet_text}

--- VENDOR CONTRACTS ---
{contracts_text}

=====================================================
INSTRUCTIONS
=====================================================

STEP 1 — RESOLVE CONFLICTS USING LATEST EMAIL
Before building the schedule, scan all emails chronologically.
If a client or vendor changed a time, vendor, or detail in a later
email, that newer version OVERRIDES the contract or sheet data.
Note every override you find.

STEP 2 — THINK LIKE A COORDINATOR
Apply professional coordinator logic when placing events:
- Add 10-15 min buffer between back-to-back events
- Schedule cake cutting 45-60 min before dessert service so
  the kitchen team has time to slice and plate
- Schedule vendor arrivals 15-30 min before they are needed
- Don't schedule first dances or toasts right after a course,
  give guests time to settle
- Build in a private first look or couple portrait window
  before cocktail hour if photography allows
- Flag any time slot that feels tight and explain why
- If the day ends late, work backwards from the end time
  to make sure nothing is rushed

STEP 3 — OUTPUT FORMAT
Return your response as a single valid JSON object with this exact structure:

{{
  "wedding": "{client_name}",
  "generated_by": "Wedding Coordinator AI",
  "overrides_from_emails": [
    {{
      "original": "what the contract or sheet said",
      "updated_to": "what the latest email changed it to",
      "email_date": "date of the email that caused the change",
      "email_subject": "subject line of that email"
    }}
  ],
  "schedule": [
    {{
      "time": "9:00 AM",
      "event": "Hair & Makeup — Bride",
      "duration_minutes": 120,
      "location": "Bridal Suite, Room 204",
      "assigned_to": "Vendor or person responsible",
      "source": "where this came from e.g. Photography Contract, Email 12, Sheet Tab: Getting Ready",
      "coordinator_notes": "why it was placed here and any proactive tips or warnings",
      "buffer_after_minutes": 15
    }}
  ],
  "coordinator_summary": "A short paragraph summarizing the full day flow, any risks, and key things to watch.",
  "flagged_issues": [
    {{
      "issue": "describe the potential problem",
      "recommendation": "what you suggest to fix or watch for"
    }}
  ]
}}

CRITICAL RULES:
- Every 30-minute slot from the earliest event to the end of the night must appear,
  even if empty. For empty slots use event: "Buffer / Free Time" and explain why
  in coordinator_notes.
- Do not guess. If a piece of info is missing, set the field to "Unknown — needs confirmation"
  and add it to flagged_issues.
- Return ONLY the raw JSON. No markdown, no backticks, no explanation outside the JSON.
"""

    # ── Call Claude ───────────────────────────────────────────────────
    # max_tokens is set high (8096) because a full day-of schedule with
    # 30-minute slots, coordinator notes, and flagged issues is lengthy.
    # Using a single-turn message rather than a conversation since the
    # entire context is self-contained in the prompt.
    print("\nSending to Claude — generating schedule...\n")
    claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = claude.messages.create(
        model      = "claude-sonnet-4-5",
        max_tokens = 8096,
        messages   = [{"role": "user", "content": prompt}]
    )
    raw_output = message.content[0].text

    # ── Save outputs ──────────────────────────────────────────────────
    # Base filename is derived from the client name with spaces replaced
    # and special characters removed for filesystem compatibility.
    base_filename = f"schedule_{client_name.replace(' ', '_').replace('&', 'and').lower()}"
    json_filename = f"{base_filename}.json"
    txt_filename  = f"{base_filename}.txt"

    try:
        # Strip any accidental markdown code fences that Claude may have
        # added despite being told not to. Starting with ``` and ending
        # with ``` are both checked independently.
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]

        schedule_json = json.loads(cleaned)

        # Save the structured JSON output for programmatic use
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(schedule_json, f, indent=2, ensure_ascii=False)
        print(f"JSON schedule saved to: {json_filename}")

        # Save a human-readable .txt version for the coordinator to
        # print or share. Each schedule slot is written with its key
        # fields on separate indented lines for easy scanning.
        with open(txt_filename, "w", encoding="utf-8") as f:
            f.write(f"WEDDING SCHEDULE — {client_name.upper()}\n")
            f.write("=" * 60 + "\n\n")

            # List any changes Claude identified from the email overrides
            if schedule_json.get("overrides_from_emails"):
                f.write("CHANGES FROM LATEST EMAILS\n")
                f.write("-" * 40 + "\n")
                for o in schedule_json["overrides_from_emails"]:
                    f.write(f"  Original:   {o['original']}\n")
                    f.write(f"  Changed to: {o['updated_to']}\n")
                    f.write(f"  Email date: {o['email_date']} — {o['email_subject']}\n\n")

            f.write("\nDAY-OF SCHEDULE\n")
            f.write("-" * 40 + "\n")
            for slot in schedule_json.get("schedule", []):
                f.write(f"\n{slot['time']} — {slot['event']}\n")
                if slot.get("location"):             f.write(f"  Location:    {slot['location']}\n")
                if slot.get("assigned_to"):          f.write(f"  Assigned to: {slot['assigned_to']}\n")
                if slot.get("source"):               f.write(f"  Source:      {slot['source']}\n")
                if slot.get("coordinator_notes"):    f.write(f"  Notes:       {slot['coordinator_notes']}\n")
                if slot.get("buffer_after_minutes"): f.write(f"  Buffer after: {slot['buffer_after_minutes']} min\n")

            if schedule_json.get("coordinator_summary"):
                f.write("\n\nCOORDINATOR SUMMARY\n")
                f.write("-" * 40 + "\n")
                f.write(schedule_json["coordinator_summary"] + "\n")

            if schedule_json.get("flagged_issues"):
                f.write("\n\nFLAGGED ISSUES\n")
                f.write("-" * 40 + "\n")
                for flag in schedule_json["flagged_issues"]:
                    f.write(f"  - {flag['issue']}\n")
                    f.write(f"    -> {flag['recommendation']}\n\n")

        print(f"Readable schedule saved to: {txt_filename}")

    except json.JSONDecodeError as e:
        # If Claude's output cannot be parsed as JSON, save the raw text
        # so no output is lost. The coordinator can inspect the raw file
        # to understand what went wrong.
        print(f"Could not parse JSON: {e}")
        with open(f"{base_filename}_raw.txt", "w", encoding="utf-8") as f:
            f.write(raw_output)
        print(f"   Raw output saved to: {base_filename}_raw.txt")

    # Print a preview of the raw output to the terminal for a quick sanity
    # check without needing to open the saved file.
    print("\n" + "=" * 60)
    print(raw_output[:2000])
    print("..." if len(raw_output) > 2000 else "")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────────────
# INTERACTIVE MENU
# ─────────────────────────────────────────────────────────────────────

def menu(client_name, spreadsheet_id, contracts_folder_id):
    """
    Runs the main interactive terminal menu in a loop until the user exits.

    The menu displays the current cache status at the top of every iteration
    so the coordinator always knows which data is fresh. Each option either
    triggers a data refresh, generates a schedule, launches the chat mode,
    or clears the cache.

    Google authentication (get_credentials()) is called on-demand for any
    option that needs it, rather than once at startup, so the app launches
    instantly and only opens a browser when actually needed.

    The chat mode (option 8) is excluded from the "Press Enter to continue"
    pause at the end of the loop because chat.py manages its own input loop
    and returns control to menu() when the user types 'quit'.

    Args:
        client_name (str):           The active client name, shown in the header.
        spreadsheet_id (str):        Google Sheets document ID for this client.
        contracts_folder_id (str):   Google Drive folder ID for this client.
    """
    while True:
        print_cache_status()
        print("=" * 45)
        print(f"  WEDDING AGENT — {client_name}")
        print("=" * 45)
        print("  1. Refresh example schedules")
        print("  2. Refresh client emails")
        print("  3. Refresh Google Sheet data")
        print("  4. Refresh vendor contracts")
        print("  5. Refresh ALL data sources")
        print("  6. Clear all cached data")
        print("  7. Generate wedding schedule")
        print("  8. Ask anything about this client")
        print("  9. Exit")
        print("=" * 45)

        choice = input("  Enter your choice (1-9): ").strip()

        if choice == "1":
            fetch_and_cache_examples()

        elif choice == "2":
            print("\nAuthenticating with Google...")
            creds = get_credentials()
            fetch_and_cache_emails(creds, client_name)

        elif choice == "3":
            print("\nAuthenticating with Google...")
            creds = get_credentials()
            fetch_and_cache_sheets(creds, spreadsheet_id)

        elif choice == "4":
            print("\nAuthenticating with Google...")
            creds = get_credentials()
            fetch_and_cache_contracts(creds, contracts_folder_id)

        elif choice == "5":
            print("\nAuthenticating with Google...")
            creds = get_credentials()
            fetch_and_cache_examples()
            fetch_and_cache_emails(creds, client_name)
            fetch_and_cache_sheets(creds, spreadsheet_id)
            fetch_and_cache_contracts(creds, contracts_folder_id)
            print("\nAll data sources refreshed and cached.")

        elif choice == "6":
            confirm = input("\n  Delete all cached data? (yes/no): ").strip().lower()
            if confirm == "yes":
                for f in ["examples.json", "emails.json", "sheets.json", "contracts.json"]:
                    path = cache_path(f)
                    if os.path.exists(path):
                        os.remove(path)
                print("  Cache cleared.")
            else:
                print("  Cancelled.")

        elif choice == "7":
            print("\nAuthenticating with Google...")
            creds = get_credentials()
            generate_wedding_schedule(client_name, spreadsheet_id, contracts_folder_id)

        elif choice == "8":
            # chat.py manages its own input loop and returns here when done
            start_chat(client_name)

        elif choice == "9":
            print("\nGoodbye!\n")
            break

        else:
            print("\n  Invalid choice. Please enter a number between 1 and 9.")

        # Pause after every action except chat mode, which handles its own flow
        if choice != "8":
            input("\n  Press Enter to return to menu...")


# ─────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Configure your client here ────────────────────────────────────
    # Change these three values when switching to a new client.
    # After changing, clear the cache via menu option 6 to avoid mixing
    # data from the previous client with the new one.
    CLIENT_NAME         = "Ereny & Mattew"
    SPREADSHEET_ID      = "107T8ZSwOA3LW0e-8CSqvQj9pg7xH1cZPi_jquXwE7_4"
    CONTRACTS_FOLDER_ID = "1iCmKF8D6pMdJHz0VTv2dDGeNxMqQ7QLL"
    # ─────────────────────────────────────────────────────────────────

    menu(CLIENT_NAME, SPREADSHEET_ID, CONTRACTS_FOLDER_ID)