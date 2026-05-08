# readers/gmail_reader.py
#
# PURPOSE:
# This module fetches all client-related emails from Gmail by reading a
# specific Gmail label (e.g. "Ereny & Mattew") and returning every message
# in every thread under that label as a flat, chronologically ordered list.
#
# Emails are the primary source of truth for last-minute changes in this
# project. The agent follows a "latest email wins" rule — if a client or
# vendor changed a time or detail in a recent email, that overrides whatever
# the contract or planning sheet says. This module ensures the full email
# chain is captured so nothing is missed.
#
# The output is consumed by agent.py, which formats it into the Claude prompt
# as Section 2 (client emails), and by chat.py where Claude can query the
# email list at runtime to answer coordinator questions.
#
# HOW GMAIL LABELS WORK IN THIS PROJECT:
# Each client gets their own Gmail label (created manually in Gmail) whose
# name matches the CLIENT_NAME in agent.py (e.g. "Ereny & Mattew"). All
# emails related to that client — from the couple, vendors, and any related
# parties — are tagged with that label. This module uses the label name to
# find and fetch all of those emails automatically.
#
# DEPENDENCIES:
#   - google-api-python-client  -> for Gmail API access
#   - base64                    -> for decoding email body and attachment data
#   - os                        -> for creating the attachments directory

import base64
import os
from googleapiclient.discovery import build

# Directory where downloaded email attachments are saved on disk.
# Created automatically if it does not exist.
ATTACHMENTS_DIR = "temp_data/attachments"


def get_label_id(service, label_name):
    """
    Resolves a Gmail label name to its internal label ID.

    Gmail's API identifies labels by an opaque ID (e.g. "Label_123456789"),
    not by their human-readable name. This function fetches all labels on
    the account and finds the ID that matches the given name, using a
    case-insensitive comparison so "Ereny & Mattew" matches "ereny & mattew".

    Args:
        service: An authenticated Gmail API client instance.

        label_name (str):
            The human-readable label name as it appears in Gmail,
            e.g. "Ereny & Mattew".

    Returns:
        str: The Gmail label ID if found (e.g. "Label_123456789").
        None: If no label with that name exists on the account.
    """
    # Fetch all labels on the Gmail account (both system and user-created)
    labels = service.users().labels().list(userId='me').execute()
    for label in labels.get('labels', []):
        # Case-insensitive match to be forgiving of capitalisation differences
        if label['name'].lower() == label_name.lower():
            return label['id']
    print(f"Label '{label_name}' not found in Gmail.")
    return None


def extract_body(payload):
    """
    Recursively extracts the plain text body from a Gmail message payload.

    Gmail messages can have complex nested structures depending on whether
    they are plain text, HTML, or multipart (a mix of both). This function
    walks the payload tree to find and return the first plain text part,
    which is the most useful format for feeding into Claude.

    The recursion handles three cases:
      1. Simple message — body data is directly on the payload.
      2. Multipart message — body is inside a 'parts' list; we look for
         the first part with mimeType 'text/plain'.
      3. Nested multipart — a part is itself multipart (e.g. multipart/
         alternative inside multipart/mixed), so we recurse into it.

    Args:
        payload (dict):
            The 'payload' field of a Gmail message object, or a nested
            part within it.

    Returns:
        str: The decoded plain text body of the email, or an empty string
             if no plain text content could be found. The 'errors=ignore'
             decode flag silently drops any undecodable bytes rather than
             raising an exception.
    """
    # Case 1: Simple (non-multipart) message — body data is directly here
    if 'body' in payload and payload['body'].get('data'):
        return base64.urlsafe_b64decode(
            payload['body']['data']
        ).decode('utf-8', errors='ignore')

    # Case 2 & 3: Multipart message — body is split across 'parts'
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and part['body'].get('data'):
                return base64.urlsafe_b64decode(
                    part['body']['data']
                ).decode('utf-8', errors='ignore')
            if part['mimeType'].startswith('multipart'):
                result = extract_body(part)
                if result:
                    return result
    return ""


def extract_attachments(service, user_id, message_id, payload, email_subject):
    """
    Recursively finds and downloads all file attachments from a Gmail message.

    Attachments in Gmail are not included inline in the message payload —
    they are stored separately and must be fetched via a dedicated API call
    using the attachment ID. This function walks the payload parts tree to
    find all named attachments, downloads each one, saves it to disk under
    a sanitized filename, and returns a list of metadata dicts.

    The saved files are not read back or parsed here — they are stored for
    reference and their metadata (name, type, size, path) is included in
    the email dict so Claude is aware of what documents were shared between
    the client and vendors.

    Args:
        service: An authenticated Gmail API client instance.

        user_id (str):
            The Gmail user ID. Always 'me' in this project, which refers
            to the authenticated account.

        message_id (str):
            The Gmail message ID, used to fetch attachment data via the API.

        payload (dict):
            The top-level payload of the Gmail message, used as the starting
            point for recursively walking the parts tree.

        email_subject (str):
            The subject line of the email, used to prefix the saved filename
            so attachments are traceable back to the email they came from.

    Returns:
        list[dict]: A list of attachment metadata dicts, one per attachment,
            each containing:
                - "original_filename" (str): The filename as sent by the sender.
                - "saved_as"          (str): The sanitized filename used when
                                             saving to disk.
                - "mime_type"         (str): The file's MIME type
                                             (e.g. "application/pdf").
                - "size_kb"           (int): File size in kilobytes.
                - "path"              (str): Full local path to the saved file.

        Returns an empty list if the message has no attachments.
    """

    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
    attachments = []

    def process_parts(parts):
        """
        Inner recursive function that walks the parts list and downloads
        any parts that are named file attachments.

        A part is considered an attachment if it has both a non-empty
        filename and an attachmentId. Parts without these (e.g. the plain
        text or HTML body) are skipped.
        """

        for part in parts:
            # Recurse into nested multipart
            if part['mimeType'].startswith('multipart') and 'parts' in part:
                process_parts(part['parts'])
                continue

            filename = part.get('filename', '').strip()
            body     = part.get('body', {})
            att_id   = body.get('attachmentId')

            # Only process parts that are actual named attachments
            if not filename or not att_id:
                continue

            try:
                # Fetch the attachment binary data from Gmail.
                # Unlike the message body, attachment data is not included
                # in the original message fetch and requires a separate call.
                att_data = service.users().messages().attachments().get(
                    userId=user_id,
                    messageId=message_id,
                    id=att_id
                ).execute()

                file_bytes = base64.urlsafe_b64decode(att_data['data'])

                # Save with a sanitized filename to avoid path issues
                safe_subject = "".join(
                    c for c in email_subject if c.isalnum() or c in (' ', '-', '_')
                ).strip()[:40]
                safe_filename = f"{safe_subject}__{filename}".replace(' ', '_')
                save_path = os.path.join(ATTACHMENTS_DIR, safe_filename)

                with open(save_path, 'wb') as f:
                    f.write(file_bytes)

                size_kb = len(file_bytes) // 1024
                print(f"Attachment saved: {safe_filename} ({size_kb}KB)")

                attachments.append({
                    "original_filename": filename,
                    "saved_as":          safe_filename,
                    "mime_type":         part['mimeType'],
                    "size_kb":           size_kb,
                    "path":              save_path
                })

            except Exception as e:
                print(f"Could not download attachment '{filename}': {e}")

    if 'parts' in payload:
        process_parts(payload['parts'])

    return attachments


def get_full_thread(service, thread_id):
    """
    Fetches all individual messages in a Gmail thread.

    Gmail groups replies together into threads. When fetching by label,
    the API returns thread-level objects, not individual messages. This
    function expands a single thread into its constituent messages so
    every reply in a chain can be processed individually.

    The Gmail API returns thread messages in chronological order (oldest
    first) when using format='full', which aligns with the "latest email
    wins" rule used throughout this project.

    Args:
        service: An authenticated Gmail API client instance.

        thread_id (str):
            The Gmail thread ID, obtained from the threads().list() response.

    Returns:
        list[dict]: A list of Gmail message objects in chronological order.
                    Each message object contains a 'payload' field with
                    headers, body parts, and attachment metadata.
                    Returns an empty list if the thread has no messages.
    """
    thread = service.users().threads().get(
        userId='me',
        id=thread_id,
        format='full'
    ).execute()
    return thread.get('messages', [])


def parse_message(service, message, fetch_attachments=True):
    """
    Parses a raw Gmail message object into a clean, structured dict.

    Extracts the most useful fields from the Gmail API's verbose message
    format — headers, plain text body, and attachment metadata — and
    returns them in the flat structure expected by agent.py and chat.py.

    Args:
        service: An authenticated Gmail API client instance. Passed through
                 to extract_attachments() if attachment fetching is enabled.

        message (dict):
            A raw Gmail message object as returned by the API with
            format='full'. Must contain a 'payload' field with 'headers'.

        fetch_attachments (bool):
            If True, downloads all file attachments and includes their
            metadata in the returned dict. Set to False to skip attachment
            downloading, e.g. for faster runs where only email text is needed.
            Defaults to True.

    Returns:
        dict: A structured email dict containing:
            - "message_id"  (str):        The Gmail message ID.
            - "subject"     (str):        Email subject line.
            - "from"        (str):        Sender name and address.
            - "date"        (str):        Send date in RFC 2822 format.
            - "body"        (str):        Plain text body of the email.
            - "attachments" (list[dict]): List of attachment metadata dicts
                                          (empty list if none or disabled).
    """
    headers  = {h['name']: h['value'] for h in message['payload']['headers']}
    subject  = headers.get('Subject', '(no subject)')
    sender   = headers.get('From',    '(unknown sender)')
    date     = headers.get('Date',    '')
    msg_id   = message['id']

    body = extract_body(message['payload'])

    attachments = []
    if fetch_attachments:
        attachments = extract_attachments(
            service, 'me', msg_id, message['payload'], subject
        )

    return {
        'message_id': msg_id,
        'subject':    subject,
        'from':       sender,
        'date':       date,
        'body':       body,
        'attachments': attachments
    }


def get_emails_by_label(creds, label_name, max_results=50, fetch_attachments=True):
    """
    Fetches all emails under a Gmail label and returns them as a flat,
    chronologically ordered list of parsed message dicts.

    This is the main entry point for this module, called by agent.py.
    It orchestrates the full pipeline:
      1. Resolve the label name to a Gmail label ID.
      2. List all threads tagged with that label.
      3. Expand each thread into its individual messages.
      4. Parse each message into a structured dict.

    The result is a flat list — thread boundaries are not preserved —
    because the rest of the system treats all emails as a single
    chronological stream where the latest message always takes precedence.

    Args:
        creds (google.oauth2.credentials.Credentials):
            OAuth2 credentials from google_auth.py. Must include the
            'https://www.googleapis.com/auth/gmail.readonly' scope.

        label_name (str):
            The Gmail label name to fetch emails from. Must exactly match
            (case-insensitively) a label that exists on the account.
            In this project, this is the client name (e.g. "Ereny & Mattew").

        max_results (int):
            Maximum number of threads to retrieve. Defaults to 50. Note
            this caps the number of threads, not individual messages — a
            single thread with 10 replies counts as 1 toward this limit.

        fetch_attachments (bool):
            Whether to download and save email attachments to disk.
            Defaults to True. Set to False for faster runs when attachments
            are not needed.

    Returns:
        list[dict]: A flat list of parsed email dicts, sorted oldest to
                    newest (chronological order within each thread, threads
                    in the order the API returns them). Each dict has the
                    structure described in parse_message().
                    Returns an empty list if the label is not found or has
                    no threads.
    """
    service = build('gmail', 'v1', credentials=creds)

    # resolve label name to ID
    label_id = get_label_id(service, label_name)
    if not label_id:
        return []

    # Get all THREADS under this label (not just messages)
    # This ensures we capture every reply in a chain
    results = service.users().threads().list(
        userId='me',
        labelIds=[label_id],
        maxResults=max_results
    ).execute()

    threads = results.get('threads', [])
    if not threads:
        print(f"No threads found under label: '{label_name}'")
        return []

    print(f"Found {len(threads)} thread(s) under label: '{label_name}'")

    # Expand every thread into its individual messages
    all_messages = []
    total_attachments = 0

    for i, thread in enumerate(threads, 1):
        thread_messages = get_full_thread(service, thread['id'])
        print(f"   🧵 Thread {i}/{len(threads)}: {len(thread_messages)} message(s)", end="")

        for message in thread_messages:
            parsed = parse_message(service, message, fetch_attachments=fetch_attachments)
            all_messages.append(parsed)
            total_attachments += len(parsed['attachments'])

        print(f" — {sum(len(m['attachments']) for m in all_messages[-len(thread_messages):])} attachment(s)")

    print(f"\n Total: {len(all_messages)} message(s) across {len(threads)} thread(s)")
    if total_attachments > 0:
        print(f"Total attachments downloaded: {total_attachments} → saved to temp_data/attachments/")

    return all_messages
