# readers/contracts_reader.py
#
# PURPOSE:
# This module is responsible for reading vendor contracts stored as PDF files
# in a specific Google Drive folder. It downloads each PDF, extracts the raw
# text from every page using PyMuPDF, and returns a structured list of dicts
# (one per contract) containing the vendor name and the full contract text.
#
# The output is consumed by agent.py, which formats it into a prompt section
# and feeds it to Claude so the AI can reference vendor terms, timelines,
# and deliverables when generating the wedding day schedule.
#
# DEPENDENCIES:
#   - google-api-python-client  : for Google Drive file listing & downloading
#   - PyMuPDF (fitz)            : for extracting text from PDF files
#   - io                        : for converting raw bytes into a file-like object
from googleapiclient.discovery import build
import fitz  # PyMuPDF — used for PDF text extraction
import io    # Used to wrap raw bytes so PyMuPDF can read them without saving to disk

def get_contracts_from_folder(creds, folder_id):
    """
    Fetches and extracts text from all PDF vendor contracts stored in a
    Google Drive folder.

    How it works:
      1. Connects to the Google Drive API using the provided credentials.
      2. Queries the specified folder for all files with a PDF MIME type.
      3. Downloads each PDF file as raw bytes.
      4. Opens the bytes in PyMuPDF and extracts plain text from every page.
      5. Returns a list of dicts, one per contract, with the vendor name
         (derived from the filename) and the full extracted text.

    Args:
        creds (google.oauth2.credentials.Credentials):
            OAuth2 credentials obtained via google_auth.py. Must include
            the 'https://www.googleapis.com/auth/drive.readonly' scope.

        folder_id (str):
            The Google Drive folder ID where the vendor contract PDFs live.
            Found in the Drive URL: drive.google.com/drive/folders/<folder_id>

    Returns:
        list[dict]: A list of contract objects, each with:
            - "vendor"  (str): The vendor name, taken from the PDF filename
                               with the .pdf extension stripped.
            - "content" (str): The full plain-text content extracted from
                               all pages of the PDF.

        Returns an empty list if the folder contains no PDFs or the API
        call returns no results.

    Example return value:
        [
            {
                "vendor":  "Amy Salon Hair Contract",
                "content": "This agreement is between Amy Salon and ..."
            },
            {
                "vendor":  "JustKlik Photography Contract",
                "content": "Photography services for the event on ..."
            }
        ]
    """
    # Build the Google Drive API v3 client using the authenticated credentials
    drive_service = build('drive', 'v3', credentials=creds)
    
    # List all PDFs in the folder
    results = drive_service.files().list(
        q=f"'{folder_id}' in parents and mimeType='application/pdf'",
        fields="files(id, name)"
    ).execute()
    
    contracts = [] # Will hold one dict per successfully processed contract

    # Iterate over every PDF file found in the folder
    for file in results.get('files', []):
        # Download the PDF file bytes
        file_data = drive_service.files().get_media(fileId=file['id']).execute()
        
        # Extract text using PyMuPDF
        pdf_doc = fitz.open(stream=io.BytesIO(file_data), filetype="pdf")
        
        # Concatenate the plain text from every page in the PDF.
        # get_text() returns the text layer of a page; works well for
        # text-based PDFs but may return empty strings for scanned images.
        text = ""
        for page in pdf_doc:
            text += page.get_text()
        
        # Append the structured contract dict to our results list.
        contracts.append({
            "vendor": file['name'].replace('.pdf', ''),
            "content": text
        })
        print(f"Read Contract Successfully: {file['name']}")
    
    return contracts