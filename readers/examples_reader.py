# readers/examples_reader.py
#
# PURPOSE:
# This module reads past wedding schedule PDFs from a local folder and
# extracts their text content. The extracted schedules are used as
# style and format references — they are injected into the Claude prompt
# in agent.py so the AI can mirror the tone, structure, and level of
# detail used in real schedules produced by this coordination business.
#
# Unlike contracts_reader.py (which pulls PDFs from Google Drive), this
# module reads from the local filesystem. The example schedules are static
# reference documents that don't change per client, so they live locally
# in the /examples folder rather than in Drive.
#
# DEPENDENCIES:
#   - PyMuPDF (fitz)  : for extracting text from PDF files
#   - os              : for listing files in the local examples folder

import fitz  # PyMuPDF : used for PDF text extraction
import os    # Used to list directory contents and build file paths

def get_schedule_examples(examples_folder="examples"):
    """
    Reads all PDF files from a local folder and extracts their plain text.

    Each PDF is treated as one complete example schedule. The text from all
    pages is concatenated into a single string per file, which is then
    appended to the results list. These strings are later formatted and
    injected into the Claude prompt as Section 1 (style examples).

    Args:
        examples_folder (str):
            Path to the local folder containing example schedule PDFs.
            Defaults to "examples", which is relative to the project root.
            The folder should contain only PDF files intended as style
            references — all non-PDF files are silently ignored.

    Returns:
        list[str]: A list of strings, one per PDF found in the folder.
                   Each string is the full concatenated text of that PDF.
                   Returns an empty list if the folder contains no PDFs
                   or does not exist.

    Notes:
        - TODO: Fix get_text() works well for text-based PDFs but returns an empty
          string for scanned image PDFs with no embedded text layer.
    """
    
    examples = [] # Accumulates one text string per PDF found
    
    for filename in os.listdir(examples_folder):
        if filename.endswith(".pdf"):
            path = os.path.join(examples_folder, filename)
            
            # Open the PDF with PyMuPDF and concatenate text from all pages
            # into a single string. This preserves the full schedule content
            # without needing to track page boundaries.
            pdf_doc = fitz.open(path)
            text = "".join(page.get_text() for page in pdf_doc)
            examples.append(text)
            print(f"Loaded example schedule successfully: {filename}")
    
    return examples