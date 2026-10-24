# TalentIQ — Intelligent Placement Assessment and Candidate Selection System

A Flask-based research prototype for resume review, job-role management, candidate matching, adaptive technical assessment, structured interview scoring, and recruiter metrics.

## Requirements
- Python 3.10 or newer
- pip
- A modern browser (Chrome or Edge recommended for screen sharing)

## Run in VS Code (Windows)
1. Extract the ZIP file.
2. Open the `intelligent-placement-system` folder in VS Code.
3. Open **Terminal → New Terminal** in VS Code.
4. Create a virtual environment:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks activation, run `python -m pip install -r requirements.txt` without activating the environment, or use Command Prompt and run `.venv\Scripts\activate.bat`.
5. Install packages:
   ```powershell
   python -m pip install -r requirements.txt
   ```
6. Start the app:
   ```powershell
   python app.py
   ```
7. Open the local URL printed in the terminal (normally `http://127.0.0.1:5000`).

## Features
- Dashboard and job-role management
- Candidate directory, resume upload, and candidate ranking
- Resume parsing for PDF and TXT files
- Adaptive technical assessment
- Structured interview response rubric
- Recruiter intelligence and research/metrics pages
- Consent-based browser screen sharing for assessment/interview sessions
- SQLite integrity-event logging for tab-outs, copy/paste attempts, fullscreen exit, and screen-share stop

## Important monitoring limitation
The included monitoring feature asks the participant to explicitly grant screen-sharing permission and logs browser events. **It does not record, save, or upload screen video.** Browser signals are imperfect and should not be treated as proof of misconduct. Use human review and an appeal process before making any candidate decision. This is a research prototype, not a production-ready hiring or proctoring system.

## Database and dataset
The ZIP includes the prototype SQLite database (`placement.db`) and the sample resume dataset under `dataset/Resume.csv`. The resume dataset may contain personal information; use it only for an authorized academic/demo purpose and do not publish it or expose it through a public server.

## Before deployment
Change `app.secret_key`, add authentication and role-based authorization, protect monitoring-event review routes, add CSRF protection, define retention/deletion rules, validate uploads, and obtain informed consent. Do not expose the development server or debug mode to the public internet.


### Monitored assessment/interview
The browser prompt is configured to prefer the current TalentIQ tab. The app accepts browser-tab sharing only and rejects window/entire-screen selections where the browser exposes the selected surface type. It does not record or upload video. Keep the assessment page open and choose the TalentIQ tab in the browser sharing picker.


### Fullscreen note
The start button requests browser fullscreen and exam-tab sharing from the same user click, then retries fullscreen after the tab is selected. Browser security still controls fullscreen; if the browser keeps its toolbar visible, press F11 manually. The screen-sharing picker itself may continue to display a sharing banner. This prototype logs integrity events but does not record or upload screen video.
