# AI Teacher Burnout Task Automator

A Flask web app that helps teachers upload exit tickets, extract responses with OCR, grade by keywords, generate feedback, and view class analytics to reduce repetitive workload.

## Overview

Teachers spend a significant amount of time grading student responses, identifying learning gaps, and preparing feedback. This project helps reduce that workload by processing handwritten or typed exit tickets and turning them into useful classroom insights.

Uploaded images are enhanced with image preprocessing, converted to text using Tesseract OCR, and analyzed to support keyword-based grading, learning-gap detection, and feedback generation.

## Features

- Login and registration
- Single or batch exit ticket upload
- OCR processing and text analysis
- Classroom dashboard and analytics dashboard
- Learning gap identification
- Smart local feedback and recommendations
- Report generation
- Edit student name, update response text, refresh feedback, and delete records

## Run Locally

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Install Tesseract OCR for local OCR support.
3. Optional: copy `.env.example` to `.env`.
4. If Tesseract is not on your system PATH, set `TESSERACT_CMD` to the full `tesseract.exe` path.
5. Start the app:

   ```bash
   python app.py
   ```

## Free Setup

- No API key is required for grading, charts, smart feedback, reports, editing, or batch upload.
- The built-in feedback engine is local and free.
- OCR uses `Tesseract + pytesseract`.
- The app tries common Windows Tesseract install paths automatically and runs multiple image-cleaning passes before OCR.

## Tesseract on Windows

Typical install path:

```text
C:\Program Files\Tesseract-OCR\tesseract.exe
```

If OCR says Tesseract is not detected, add this to `.env`:

```text
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

For best OCR results, upload a bright JPG or PNG where the handwriting fills most of the image.

## Notes

- Uploaded files are stored in `uploads/`.
- App data is stored in `teacher_burnout.db`.
- These local runtime files are ignored by Git and should not be uploaded to GitHub.
