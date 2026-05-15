# Teacher Burnout Task Automator

A Flask-based dashboard for uploading exit tickets, extracting or pasting student responses, grading by keywords, generating smart local feedback, and viewing class analytics.

## Features
- Login and registration
- Single or batch exit ticket upload
- OCR processing output and text analysis output
- Classroom dashboard and analytics dashboard
- Learning gap identification
- Feedback and recommendation system
- Report generation
- Edit student name, update response text, refresh smart feedback, and delete records

## Run locally
1. Install dependencies: `pip install -r requirements.txt`
2. Install Tesseract OCR for a fully free local OCR setup
3. Optional: copy `.env.example` to `.env`
4. If Tesseract is not on your system PATH, set `TESSERACT_CMD` to the full `tesseract.exe` path
5. Start the app: `python app.py`

## Free setup
- No API key is required for grading, charts, smart feedback, reports, editing, or batch upload.
- The built-in feedback engine is completely local and free.
- OCR uses only `Tesseract + pytesseract`.
- The app tries common Windows Tesseract install paths automatically and runs multiple image-cleaning passes before OCR.

## Tesseract on Windows
- Typical install path: `C:\Program Files\Tesseract-OCR\tesseract.exe`
- If OCR still says Tesseract is not detected, add this to `.env`:
  `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe`
- For best OCR results, upload a bright JPG or PNG where the handwriting fills most of the image.
- Very messy handwriting can still need manual correction; this app improves OCR reliability but cannot guarantee perfect reading of every handwritten response.

## Notes
- Uploaded files are stored in `uploads/` and app data is stored in `teacher_burnout.db`.
