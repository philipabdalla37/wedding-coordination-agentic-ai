# chat.py
#
# PURPOSE:
# This module provides an interactive Q&A chat mode that lets a coordinator
# ask plain English questions about a specific client's wedding data and
# receive accurate, data-driven answers.
#
# HOW IT WORKS:
# Rather than having Claude answer from memory or general knowledge, this
# module uses a two-step pattern for every question:
#   1. Claude writes a small Python snippet to query the in-memory data
#      (emails, sheets, contracts, examples).
#   2. That code is executed locally in a sandboxed environment, and the
#      raw output is fed back to Claude so it can formulate a plain English
#      answer grounded in the actual data.
#
# This approach ensures answers are always based on what the data actually
# says rather than what Claude thinks it might say, which is critical for
# time-sensitive coordination decisions.
#
# SAFETY:
# Claude-generated code runs through safe_exec(), which applies a static
# keyword blocklist and a restricted __builtins__ environment to prevent
# any file access, imports, or system calls. If the generated code fails,
# the module automatically sends the error back to Claude with instructions
# to fix and retry once before giving up.
#
# ENTRY POINT:
# This module is called from agent.py menu option 8. It is not intended
# to be run directly.
#
# DEPENDENCIES:
#   - anthropic      : Claude API client for generating and interpreting code
#   - python-dotenv  : loading ANTHROPIC_API_KEY from the .env file
#   - os, json       : reading cached data files from disk
#   - io, ast        : stdout capture and syntax checking in safe_exec()

import os
import json
import io
import ast
import anthropic
from dotenv import load_dotenv

# Load environment variables from .env, specifically ANTHROPIC_API_KEY
load_dotenv()

# Directory where cached JSON data files are stored by agent.py
TEMP_DIR = "temp_data"


# ─────────────────────────────────────────────────────────────────────
# LOAD CACHED DATA INTO MEMORY
# ─────────────────────────────────────────────────────────────────────

def load_all_cached_data() -> dict:
    """
    Loads all cached JSON data files into memory as a single dictionary.

    The four data sources (emails, sheets, contracts, examples) are each
    stored as separate JSON files in temp_data/ by agent.py. This function
    reads all of them into memory at the start of a chat session so they
    are available for the entire session without repeated disk reads.

    The resulting dict is passed to both build_system_prompt() (so Claude
    knows what data is available) and safe_exec() (so the data is injected
    directly into the code execution environment as named variables).

    Returns:
        dict: A dictionary with four keys:
            - "emails"    (list | None): List of email dicts, or None if
                                         emails.json does not exist.
            - "sheets"    (dict | None): Dict of tab_name -> list of rows,
                                         or None if sheets.json does not exist.
            - "contracts" (list | None): List of contract dicts, or None if
                                         contracts.json does not exist.
            - "examples"  (list | None): List of example schedule strings,
                                         or None if examples.json does not exist.

        Missing files result in None values rather than raising exceptions,
        allowing the chat session to proceed with partial data and warning
        the user about what is missing.
    """
    data = {}
    files = {
        "emails":    "emails.json",
        "sheets":    "sheets.json",
        "contracts": "contracts.json",
        "examples":  "examples.json",
    }
    for key, filename in files.items():
        path = os.path.join(TEMP_DIR, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data[key] = json.load(f)
        else:
            # Store None so callers can distinguish "not cached" from "empty"
            data[key] = None
    return data


# ─────────────────────────────────────────────────────────────────────
# SAFE EXECUTOR
# ─────────────────────────────────────────────────────────────────────

# Keywords that are never permitted in Claude-generated code.
# This is a static blocklist applied before execution as a first line
# of defence. It prevents imports, file access, system calls, and other
# operations that could be harmful or that the sandboxed environment
# does not support anyway.
BLOCKED_KEYWORDS = [
    "import ",      # no module imports of any kind
    "__import__",   # no dynamic imports either
    "open(",        # no file reads or writes
    "eval(",        # no dynamic code evaluation
    "exec(",        # no nested execution
    "compile(",     # no dynamic compilation
    "os.",          # no OS-level operations
    "sys.",         # no system-level access
    "shutil",       # no file system manipulation
    "subprocess",   # no shell commands
    "socket",       # no network access
    "requests",     # no HTTP calls
    "urllib",       # no URL handling
    "pickle"        # no serialization/deserialization
]


def safe_exec(code: str, data: dict) -> tuple:
    """
    Executes Claude-generated Python code in a restricted sandbox and
    returns any printed output along with any error that occurred.

    The sandbox works by replacing the normal Python __builtins__ with a
    tightly controlled whitelist of safe built-in functions. This prevents
    Claude-generated code from doing anything beyond querying the pre-loaded
    data and printing results — no file access, no imports, no system calls.

    The four data variables (emails, sheets, contracts, examples) are
    injected directly into the execution globals so Claude's code can
    reference them as plain variables without needing any imports.

    Execution happens in two stages:
      1. Static check  — scans for blocked keywords before running anything.
      2. Syntax check  — validates the code parses correctly via ast.parse().
      3. Runtime exec  — runs the code with stdout redirected to a StringIO
                         buffer so printed output can be captured and returned.

    Args:
        code (str):
            The Python code string to execute, as extracted from Claude's
            response by extract_code().

        data (dict):
            The in-memory data dict from load_all_cached_data(). Its four
            values are injected as named variables in the exec environment.

    Returns:
        tuple(str, str):
            - First element:  captured stdout output (empty string if none).
            - Second element: error message (empty string if no error).
            Only one of these will be non-empty in practice.
    """

    # Stage 1 — static keyword blocklist check.
    # Scan the raw code string before attempting to parse or run it.
    # Returns immediately with an error if any forbidden keyword is found.
    for keyword in BLOCKED_KEYWORDS:
        if keyword in code:
            return "", f"Blocked: code contains forbidden operation '{keyword}'"

    # Stage 2 — syntax check.
    # ast.parse() validates the code is syntactically valid Python without
    # executing it. Catching SyntaxError here gives a cleaner error message
    # than letting exec() fail with a less informative traceback.
    try:
        ast.parse(code)
    except SyntaxError as e:
        return "", f"Syntax error: {e}"

    # Redirect stdout to a StringIO buffer so print() calls in Claude's
    # code are captured as a string rather than printed to the terminal.
    stdout_capture = io.StringIO()

    # Build the restricted execution environment.
    # __builtins__ is replaced with a whitelist — only safe, pure-Python
    # built-ins that are needed for data querying are included.
    # Anything not in this dict is simply unavailable to the executed code.
    exec_globals = {
        "__builtins__": {
            # print() is overridden to write to our capture buffer instead
            # of the terminal, so output can be returned as a string.
            "print":      lambda *a, **kw: print(*a, **kw, file=stdout_capture),
            "len":        len,
            "range":      range,
            "enumerate":  enumerate,
            "zip":        zip,
            "map":        map,
            "filter":     filter,
            "list":       list,
            "dict":       dict,
            "set":        set,
            "tuple":      tuple,
            "str":        str,
            "int":        int,
            "float":      float,
            "bool":       bool,
            "max":        max,
            "min":        min,
            "sum":        sum,
            "sorted":     sorted,
            "any":        any,
            "all":        all,
            "isinstance": isinstance,
            "next":       next,
            "True":       True,
            "False":      False,
            "None":       None,
        },
        # Inject the four data variables directly into the exec environment.
        # None values from load_all_cached_data() are replaced with empty
        # collections so Claude's code doesn't need to handle None checks.
        "emails":    data.get("emails")    or [],
        "sheets":    data.get("sheets")    or {},
        "contracts": data.get("contracts") or [],
        "examples":  data.get("examples")  or [],
    }

    # Stage 3 — execute the code.
    # The filename "<claude_generated>" appears in any runtime tracebacks
    # to make it clear the error came from generated code, not this module.
    try:
        exec(compile(code, "<claude_generated>", "exec"), exec_globals)
        return stdout_capture.getvalue().strip(), ""
    except Exception as e:
        return "", f"Runtime error: {type(e).__name__}: {e}"


# ─────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────

def build_system_prompt(client_name: str, data: dict) -> str:
    """
    Constructs the system prompt that is sent to Claude at the start of
    every chat session and persists for the entire conversation.

    The prompt instructs Claude to act as a wedding coordination assistant
    that answers questions by writing Python code rather than relying on
    its own knowledge. It includes:
      - The names of available sheet tabs (pulled live from the cached data)
      - The names of available vendors (pulled live from contracts)
      - The column headers of the Overall Schedule tab (so Claude knows
        how to index into the schedule rows correctly)
      - A decision guide mapping question types to the correct data source
      - Strict rules preventing Claude from guessing or using imports

    Injecting live metadata (tab names, vendor names, headers) means the
    prompt is always accurate for the current client's data rather than
    relying on hardcoded assumptions.

    Args:
        client_name (str):
            The name of the client (e.g. "Ereny & Mattew"), used to
            personalise the assistant's role in the prompt.

        data (dict):
            The in-memory data dict from load_all_cached_data(). Used to
            extract tab names, vendor names, and schedule headers.

    Returns:
        str: The fully constructed system prompt string.
    """

    # Extract live metadata to inject into the prompt
    sheet_tabs     = list(data["sheets"].keys()) if data.get("sheets") else []
    vendor_names   = [c['vendor'] for c in data["contracts"]] if data.get("contracts") else []

    # Pull the first row of the Overall Schedule tab as the column headers.
    # This tells Claude exactly which column names exist so it can write
    # accurate index-based or keyword-based lookups.
    #TODO: In the future, we could also inject the entire schedule as a variable
    schedule_headers = []
    if data.get("sheets") and "Overall Schedule" in data["sheets"]:
        rows = data["sheets"]["Overall Schedule"]
        if rows:
            schedule_headers = rows[0]

    return f"""
You are a smart wedding coordination assistant for the client: {client_name}.

You answer questions by writing Python code that queries pre-loaded data variables.
The data is already in memory — you do NOT need to import anything or open any files.

=== AVAILABLE VARIABLES (already loaded, use directly) ===

emails    → list of email dicts, sorted oldest → newest
            each has: 'subject', 'from', 'date', 'body', 'attachments'
            LATEST email wins on any conflict

sheets    → dict of sheet tabs
            available tabs: {', '.join(sheet_tabs) if sheet_tabs else 'none found'}

contracts → list of contract dicts
            available vendors: {', '.join(vendor_names) if vendor_names else 'none found'}
            each has: 'vendor', 'content'

examples  → list of past schedule strings (style reference only)

=== SCHEDULE RULE — IMPORTANT ===
Any question about timing, schedule, order of events, or what happens when
MUST check sheets["Overall Schedule"] first.
Overall Schedule headers: {schedule_headers if schedule_headers else 'check sheets["Overall Schedule"][0]'}

Access pattern:
    schedule = sheets["Overall Schedule"]
    headers  = schedule[0]   # column names
    rows     = schedule[1:]  # data rows

=== DECISION GUIDE ===

Question type                         → Variable to use
──────────────────────────────────────────────────────────────
"What time is X?" / "When does X?"   → sheets["Overall Schedule"]
"What is the order of events?"        → sheets["Overall Schedule"]
"Who is the vendor for X?"            → sheets["Vendor Info"] or contracts
"What does the X contract say?"       → contracts
"Did the client confirm X?"           → emails (latest wins)
"Has anything changed?"               → emails (latest wins)
"Who is in the bridal party?"         → sheets["Bridal Party Info"]
"What info is outstanding/missing?"   → sheets["Outstanding Info"]

=== HOW TO RESPOND ===

Step 1: Pick the right variable(s).
Step 2: Write clean Python to search and print the answer.
        NO imports. NO open(). Just use the variables directly.
Step 3: Wrap code EXACTLY like this:

<execute_python>
schedule = sheets["Overall Schedule"]
headers  = schedule[0]
rows     = schedule[1:]

for row in rows:
    row_text = ' '.join(str(cell) for cell in row).lower()
    if 'ceremony' in row_text:
        print(row)
</execute_python>

Step 4: After seeing the result, answer in plain friendly English.
        Be specific — mention exact times, names, and where the info came from.
        If something is missing, say "This isn't confirmed in the data yet."

=== STRICT RULES ===
- NO import statements — data is already loaded
- NO open(), os., sys., or file access
- ONE code block per response
- Never guess — only report what the data actually says
"""


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def extract_code(text: str):
    """
    Extracts the Python code block from Claude's response text.

    Claude is instructed to wrap any generated code in <execute_python>
    tags. This function finds those tags and returns the content between
    them, stripped of leading and trailing whitespace.

    Args:
        text (str): The full response text from Claude.

    Returns:
        str:  The extracted code string if both tags are found.
        None: If either tag is missing, meaning Claude answered directly
              without generating code (which is also a valid response).
    """
    start_tag = "<execute_python>"
    end_tag   = "</execute_python>"
    start = text.find(start_tag)
    end   = text.find(end_tag)
    if start != -1 and end != -1:
        return text[start + len(start_tag):end].strip()
    return None


def check_cache_available() -> list:
    """
    Checks which required cache files are missing from temp_data/.

    Called at the start of a chat session to warn the user if any data
    source has not been fetched yet. examples.json is excluded from this
    check as it is optional — the chat mode can function without style
    examples since they are only relevant to schedule generation.

    Returns:
        list[str]: A list of missing filenames (e.g. ["emails.json"]).
                   Returns an empty list if all required files are present.
    """
    required = ["emails.json", "sheets.json", "contracts.json"]
    return [f for f in required if not os.path.exists(os.path.join(TEMP_DIR, f))]


def print_code_block(code: str):
    """
    Prints a Claude-generated code block to the terminal with a simple
    box-drawing border for visual clarity.

    Used to show the coordinator exactly what code Claude wrote before
    it is executed, making the process transparent and auditable.

    Args:
        code (str): The code string to display.
    """
    print("  Generated code:")
    print("  +" + "-" * 50)
    for line in code.split("\n"):
        print(f"  | {line}")
    print("  +" + "-" * 50)


# ─────────────────────────────────────────────────────────────────────
# CHAT LOOP
# ─────────────────────────────────────────────────────────────────────

def start_chat(client_name: str):
    """
    Starts an interactive terminal chat session for a specific client.

    This is the main entry point for chat mode, called from agent.py.
    It sets up the session (loads data, builds the system prompt, initialises
    conversation history) and then runs the main input loop until the user
    types 'quit', 'exit', or 'q', or sends a keyboard interrupt.

    Each turn in the loop follows this pattern:
      1. Get user input.
      2. Send the full conversation history to Claude.
      3. If Claude returned code, execute it via safe_exec().
         a. If execution failed, send the error back to Claude and retry once.
         b. If execution succeeded with output, send the output back to Claude
            for a plain English answer.
         c. If execution succeeded but produced no output, ask Claude to
            explain that the data was not found.
      4. If Claude answered directly (no code), display the answer as-is.

    Conversation history is maintained as a list of role/content dicts and
    grows with every turn. This gives Claude context from earlier in the
    session, allowing follow-up questions to work naturally.

    Args:
        client_name (str):
            The name of the client whose data to load and query,
            e.g. "Ereny & Mattew". Used to personalise the system prompt
            and displayed in the session header.
    """

    print(f"\n{'=' * 52}")
    print(f"  CHAT MODE — {client_name}")
    print(f"{'=' * 52}")

    # Warn the user if any required cache files are missing.
    # The session will still start but answers may be incomplete.
    missing = check_cache_available()
    if missing:
        print(f"\n  Missing cached data: {', '.join(missing)}")
        print("  Go back to the menu and refresh the missing data first.\n")

    # Load all cached data into memory once for the entire session.
    # This avoids repeated disk reads on every question.
    print("  Loading cached data into memory...", end="", flush=True)
    data = load_all_cached_data()
    loaded = [k for k, v in data.items() if v is not None]
    print(f" done. ({', '.join(loaded)})\n")

    print("  Ask anything about this client's wedding.")
    print("  Type 'quit' to return to the main menu.\n")

    claude        = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    system_prompt = build_system_prompt(client_name, data)
    history       = []  # Grows with each turn to maintain conversational context

    while True:
        try:
            user_input = input("  You: ").strip()
        except (KeyboardInterrupt, EOFError):
            # Handle Ctrl+C and Ctrl+D gracefully instead of crashing
            print("\n\n  Returning to menu...")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("\n  Returning to menu...")
            break

        # Append the user's message to history before sending to Claude
        history.append({"role": "user", "content": user_input})
        print("\n  Thinking...", end="", flush=True)

        # Send the full conversation history to Claude.
        # The system prompt is sent separately on every call — it is not
        # stored in history since the Anthropic API handles it independently.
        try:
            response = claude.messages.create(
                model      = "claude-sonnet-4-5",
                max_tokens = 2048,
                system     = system_prompt,
                messages   = history
            )
        except Exception as e:
            print(f"\n  Claude API error: {e}\n")
            # Remove the user message we just appended so history stays clean
            # and the user can try again without a corrupted turn in history.
            history.pop()
            continue

        assistant_text = response.content[0].text

        # Clear the "Thinking..." line from the terminal before printing output
        print("\r" + " " * 25 + "\r", end="")

        # Check whether Claude included a code block in its response
        code = extract_code(assistant_text)

        if code:
            print_code_block(code)
            result, error = safe_exec(code, data)

            # ── Self-correction on error (one retry) ──────────────────
            # If the code failed, feed the error back to Claude with a
            # reminder of the sandbox constraints and ask it to fix and retry.
            # Only one retry is attempted to avoid infinite loops.
            if error:
                print(f"\n  Execution issue: {error}")
                history.append({"role": "assistant", "content": assistant_text})
                history.append({
                    "role":    "user",
                    "content": (
                        f"[SYSTEM: Code failed — {error}. "
                        f"Remember: no imports, no open(). "
                        f"Use the pre-loaded variables directly: "
                        f"emails, sheets, contracts, examples. Fix and retry.]"
                    )
                })

                retry = claude.messages.create(
                    model="claude-sonnet-4-5", max_tokens=2048,
                    system=system_prompt, messages=history
                )
                assistant_text = retry.content[0].text
                code = extract_code(assistant_text)

                if code:
                    print("  Retrying with fixed code...")
                    print_code_block(code)
                    result, error = safe_exec(code, data)
                    if error:
                        # Both attempts failed — log and move on
                        print(f"  Retry also failed: {error}\n")
                        history.append({"role": "assistant", "content": assistant_text})
                        continue

            # ── Feed result back for a plain English answer ────────────
            # Code ran successfully and produced output.
            # Send the raw output back to Claude so it can interpret it
            # and answer the user's question in plain English.
            if result:
                print(f"\n  Data found:\n")
                for line in result.split("\n"):
                    print(f"     {line}")

                history.append({"role": "assistant", "content": assistant_text})
                history.append({
                    "role":    "user",
                    "content": (
                        f"[SYSTEM: Code ran successfully. Result:\n{result}\n\n"
                        f"Now answer the user's original question in plain English "
                        f"based on this data. Be specific and concise.]"
                    )
                })

                final = claude.messages.create(
                    model="claude-sonnet-4-5", max_tokens=1024,
                    system=system_prompt, messages=history
                )
                final_text = final.content[0].text
                print(f"\n  {final_text}\n")
                history.append({"role": "assistant", "content": final_text})

            else:
                # Code ran without error but printed nothing.
                # This means the search found no matching data.
                # Ask Claude to explain this to the user naturally.
                history.append({"role": "assistant", "content": assistant_text})
                history.append({
                    "role":    "user",
                    "content": "[SYSTEM: Code ran but returned no output. The data may not exist in the cache.]"
                })
                no_data = claude.messages.create(
                    model="claude-sonnet-4-5", max_tokens=512,
                    system=system_prompt, messages=history
                )
                print(f"\n  {no_data.content[0].text}\n")
                history.append({"role": "assistant", "content": no_data.content[0].text})

        else:
            # Claude responded directly without generating code.
            # This is valid for questions that don't require data lookup,
            # such as general wedding coordination advice.
            print(f"\n  {assistant_text}\n")
            history.append({"role": "assistant", "content": assistant_text})