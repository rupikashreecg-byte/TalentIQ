import json
import re
import sqlite3
import uuid
from pathlib import Path
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from database import get_connection, initialize_database, seed_database
from matcher import extract_skills, rank_candidates, calculate_skill_score

app = Flask(__name__)
app.secret_key = "change-this-demo-secret"


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped




def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in first.", "error")
                return redirect(url_for("login"))

            if session.get("user_role") != role:
                flash("You don't have permission to access that page.", "error")

                if session.get("user_role") == "recruiter":
                    return redirect(url_for("index"))

                return redirect(url_for("candidate_dashboard"))

            return view(*args, **kwargs)
        return wrapped
    return decorator





@app.template_filter("from_json")
def from_json_filter(value):
    try:
        return json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []


ALLOWED_EXTENSIONS = {"pdf", "txt"}
UPLOAD_FOLDER = Path(__file__).resolve().parent / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)


def initialize_integrity_tables():
    """Create storage for assessment/interview integrity events."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS integrity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL,
                purpose TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def initialize_auth_table():
    with get_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('candidate','recruiter')),
            candidate_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")



@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")

        if role not in {"candidate", "recruiter"}:
            flash("Please select a valid account type.", "error")
            return render_template("login.html", mode="login")

        with get_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Email or password is incorrect.", "error")
            return render_template("login.html", mode="login")

        if user["role"] != role:
            flash("The selected account type does not match your account.", "error")
            return render_template("login.html", mode="login")

        session.clear()
        session["user_id"] = user["id"]
        session["user_role"] = user["role"]
        session["candidate_id"] = user["candidate_id"]

        flash("Welcome back!", "success")

        if role == "recruiter":
            return redirect(url_for("index"))
        else:
            return redirect(url_for("candidate_dashboard"))

    return render_template("login.html", mode="login")


# Candidate dashboard
@app.route("/candidate-dashboard")
@role_required("candidate")
def candidate_dashboard():
    return render_template("candidate_dashboard.html")
    



@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "candidate")
        if not name or not email or len(password) < 8 or role not in {"candidate", "recruiter"}:
            flash("Enter your name, a valid email, a password of at least 8 characters, and a role.", "error")
            return render_template("login.html", mode="register")
        candidate_id = None
        with get_connection() as conn:
            try:
                if role == "candidate":
                    cur = conn.execute("INSERT INTO candidates(name,email,resume_text,skills) VALUES(?,?,?,?)",
                                       (name, email, "", "[]"))
                    candidate_id = cur.lastrowid
                cur = conn.execute("INSERT INTO users(name,email,password_hash,role,candidate_id) VALUES(?,?,?,?,?)",
                                   (name, email, generate_password_hash(password), role, candidate_id))
                user_id = cur.lastrowid
            except sqlite3.IntegrityError:
                flash("An account with that email already exists.", "error")
                return render_template("login.html", mode="register")
        session.clear()
        session["user_id"] = user_id
        session["user_role"] = role
        session["candidate_id"] = candidate_id
        flash("Account created successfully.", "success")
        return redirect(url_for("index") if role == "recruiter" else url_for("assessment"))
    return render_template("login.html", mode="register")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def log_integrity_event(session_key, purpose, event_type, details=None):
    """Persist one integrity event in SQLite."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO integrity_events
                (session_key, purpose, event_type, details)
            VALUES (?, ?, ?, ?)
        """, (
            session_key,
            purpose,
            event_type,
            json.dumps(details or {})
        ))


def active_integrity_session(purpose):
    """Return whether this browser session has an active consented session."""
    integrity = session.get("integrity_session", {})
    return (
        integrity.get("active") is True
        and integrity.get("purpose") == purpose
        and bool(integrity.get("key"))
    )


def get_jobs():
    with get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]


def get_candidate(candidate_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        return dict(row) if row else None


def parse_resume_profile(candidate):
    text = candidate.get("resume_text") or ""
    skills = extract_skills(text)
    category_match = re.search(r"(?im)^Resume category:\s*(.+)$", text)
    category = category_match.group(1).strip() if category_match else "Not supplied"
    sections = {}
    current = "summary"
    buckets = {"education": [], "experience": [], "projects": []}
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        lower = cleaned.lower().strip(" :")
        if any(k in lower for k in ["education", "academic background", "qualification"]):
            current = "education"
            continue
        if any(k in lower for k in ["experience", "employment", "work history", "internship"]):
            current = "experience"
            continue
        if any(k in lower for k in ["project", "portfolio"]):
            current = "projects"
            continue
        if current in buckets and len(buckets[current]) < 8:
            buckets[current].append(cleaned[:220])
    return {"category": category, "skills": skills, **buckets}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/integrity/start", methods=["POST"])
@login_required
def integrity_start():
    """Start an integrity session after browser screen-share permission."""

    data = request.get_json(silent=True) or {}
    purpose = data.get("purpose")
    role = session.get("user_role")

    if purpose not in {"assessment", "interview"}:
        return {"ok": False, "error": "Invalid session type."}, 400

    if purpose == "assessment" and role != "candidate":
        return {"ok": False, "error": "Candidates only."}, 403

    if purpose == "interview" and role not in {"candidate", "recruiter"}:
        return {"ok": False, "error": "Login required."}, 403

    session_key = str(uuid.uuid4())

    session["integrity_session"] = {
        "key": session_key,
        "purpose": purpose,
        "active": True,
        "tab_outs": 0,
    }

    log_integrity_event(
        session_key, purpose, "screen_share_started"
    )

    return {"ok": True, "message": "Integrity session started."}


@app.route("/integrity/event", methods=["POST"])
@login_required
def integrity_event():
    """Receive and save browser integrity events."""

    active = session.get("integrity_session")

    if not active or not active.get("active"):
        return {
            "ok": False,
            "error": "No active integrity session."
        }, 403

    if active.get("purpose") == "assessment" and session.get("user_role") != "candidate":
        return {"ok": False, "error": "Candidates only."}, 403

    data = request.get_json(silent=True) or {}
    event_type = str(data.get("type", ""))[:80]
    details = data.get("details", {})

    if not isinstance(details, dict):
        details = {}

    allowed_events = {
        "tab_out",
        "page_visible",
        "fullscreen_exited",
        "copy_attempt",
        "paste_attempt",
        "screen_share_stopped",
    }

    if event_type not in allowed_events:
        return {"ok": False, "error": "Unsupported event."}, 400

    if event_type == "tab_out":
        active["tab_outs"] = int(active.get("tab_outs", 0)) + 1

    log_integrity_event(
        active["key"],
        active["purpose"],
        event_type,
        details
    )

    if event_type == "screen_share_stopped" or active.get("tab_outs", 0) > 3:
        active["active"] = False
        session["integrity_session"] = active

        reason = (
            "screen sharing stopped"
            if event_type == "screen_share_stopped"
            else "more than three tab-outs"
        )

        log_integrity_event(
            active["key"],
            active["purpose"],
            "session_paused",
            {
                "reason": reason,
                "tab_outs": active.get("tab_outs", 0)
            }
        )

        return {
            "ok": True,
            "paused": True,
            "reason": reason,
            "tab_outs": active.get("tab_outs", 0)
        }

    session["integrity_session"] = active

    return {
        "ok": True,
        "paused": False,
        "tab_outs": active.get("tab_outs", 0)
    }


@app.route("/integrity/stop", methods=["POST"])
@login_required
def integrity_stop():
    """End the active integrity session."""

    active = session.get("integrity_session")

    if active and active.get("key"):
        log_integrity_event(
            active["key"],
            active.get("purpose", "unknown"),
            "session_ended"
        )

        active["active"] = False
        session["integrity_session"] = active

    return {"ok": True}


# Recruiter dashboard
@app.route("/")
@role_required("recruiter")
def index():
    jobs = get_jobs()
    with get_connection() as conn:
        total_candidates = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        total_applications = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        assessments = conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
        interviews = conn.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        recent = conn.execute("""
            SELECT c.id, c.name, c.email, c.skills
            FROM candidates c ORDER BY c.id DESC LIMIT 5
        """).fetchall()
        assessment_count = conn.execute(
        "SELECT COUNT(*) FROM assessments"
        ).fetchone()[0]
    return render_template("index.html", jobs=jobs, total_candidates=total_candidates,
                           total_applications=total_applications,assessment_count=assessment_count, assessments=assessments,
                           interviews=interviews, recent_candidates=[dict(r) for r in recent])


# Shared jobs page: candidates view jobs,
# recruiters can view and create them
@app.route("/jobs", methods=["GET", "POST"])
@login_required
def jobs():
    if request.method == "POST":
        action = request.form.get("action", "")

        # Candidate applying for a job
        if action == "apply":
            if session.get("user_role") != "candidate":
                flash("Only candidates can apply for jobs.", "error")
                return redirect(url_for("jobs"))

            candidate_id = session.get("candidate_id")
            job_id = request.form.get("job_id", type=int)

            if not candidate_id or not job_id:
                flash("Candidate profile or job not found.", "error")
                return redirect(url_for("jobs"))

            with get_connection() as conn:
                job = conn.execute(
                    "SELECT id FROM jobs WHERE id = ?",
                    (job_id,)
                ).fetchone()

                if not job:
                    flash("Job role not found.", "error")
                    return redirect(url_for("jobs"))

                conn.execute("""
                    INSERT OR IGNORE INTO applications
                    (candidate_id, job_id)
                    VALUES (?, ?)
                """, (candidate_id, job_id))

            flash("Application submitted successfully!", "success")
            return redirect(url_for("jobs"))

        # Only recruiters can create jobs
        if session.get("user_role") != "recruiter":
            flash("Only recruiters can create job roles.", "error")
            return redirect(url_for("jobs"))

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        min_cgpa = request.form.get("min_cgpa", type=float)
        graduation_year = request.form.get("graduation_year", type=int)

        if (
            not title
            or not description
            or min_cgpa is None
            or graduation_year is None
        ):
            flash("Complete all job fields before saving.", "error")
            return redirect(url_for("jobs"))

        skills = extract_skills(description)

        with get_connection() as conn:
            conn.execute("""
                INSERT INTO jobs
                (title, description, required_skills,
                 min_cgpa, graduation_year)
                VALUES (?, ?, ?, ?, ?)
            """, (
                title,
                description,
                json.dumps(skills),
                min_cgpa,
                graduation_year
            ))

        flash("Job role added successfully!", "success")
        return redirect(url_for("jobs"))

    return render_template("jobs.html", jobs=get_jobs())


# Recruiter-only candidate listing
@app.route("/candidates")
@role_required("recruiter")
def candidates():
    selected_job_id = request.args.get("job_id", type=int)
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 50
    jobs_list = get_jobs()
    selected_job = next((j for j in jobs_list if j["id"] == selected_job_id), jobs_list[0] if jobs_list else None)
    ranked_candidates, total, pages = [], 0, 1
    if selected_job:
        with get_connection() as conn:
            total = conn.execute("""
                SELECT COUNT(*) FROM candidates c
                JOIN applications a ON a.candidate_id=c.id
                WHERE a.job_id=?
            """, (selected_job["id"],)).fetchone()[0]
            pages = max((total + per_page - 1) // per_page, 1)
            page = min(page, pages)
            rows = conn.execute("""
                SELECT c.*, a.id AS application_id, a.status
                FROM candidates c JOIN applications a ON a.candidate_id=c.id
                WHERE a.job_id=?
                ORDER BY c.id LIMIT ? OFFSET ?
            """, (selected_job["id"], per_page, (page-1)*per_page)).fetchall()
        ranked_candidates = rank_candidates(rows, selected_job)
    return render_template("candidates.html", jobs=jobs_list, selected_job=selected_job,
                           candidates=ranked_candidates, page=page, pages=pages, total=total)


# Recruiter-only candidate profile details
@app.route("/candidate/<int:candidate_id>")
@role_required("recruiter")
def candidate_detail(candidate_id):
    candidate = get_candidate(candidate_id)
    if not candidate:
        flash("Candidate not found.", "error")
        return redirect(url_for("candidates"))
    profile = parse_resume_profile(candidate)
    try:
        candidate["stored_skills"] = json.loads(candidate.get("skills") or "[]")
    except (TypeError, json.JSONDecodeError):
        candidate["stored_skills"] = []
    return render_template("candidate_detail.html", candidate=candidate, profile=profile)


# Shared resume page
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload_resume():
    if request.method == "POST":
        file = request.files.get("resume")
        if not file or not file.filename:
            flash("Choose a PDF or TXT resume first.", "error")
            return redirect(url_for("upload_resume"))
        if not allowed_file(file.filename):
            flash("Only PDF and TXT files are supported.", "error")
            return redirect(url_for("upload_resume"))
        filename = secure_filename(file.filename)
        suffix = filename.rsplit(".", 1)[1].lower()
        raw = file.read()
        if suffix == "txt":
            text = raw.decode("utf-8", errors="ignore")
        else:
            try:
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(raw))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                flash("PDF parsing requires pypdf. Run: python -m pip install pypdf", "error")
                return redirect(url_for("upload_resume"))
            except Exception as exc:
                flash(f"Could not read this PDF: {exc}", "error")
                return redirect(url_for("upload_resume"))
        text = text.strip()
        if len(text) < 20:
            flash("Not enough selectable text was found. Scanned PDFs need OCR, which this demo does not include.", "error")
            return redirect(url_for("upload_resume"))
        skills = extract_skills(text)
        email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
        name = Path(filename).stem.replace("_", " ").replace("-", " ").title()
        email = email_match.group(0) if email_match else f"upload_{abs(hash(filename + str(len(text))))}@example.invalid"
        
        with get_connection() as conn:
            if session.get("user_role") == "candidate":
                candidate_id = session.get("candidate_id")

                if not candidate_id:
                    flash("Your candidate profile was not found.", "error")
                    return redirect(url_for("candidate_dashboard"))

                conn.execute("""
                UPDATE candidates
                SET resume_text = ?, skills = ?
                WHERE id = ?
                """, (text, json.dumps(skills), candidate_id))

            else:
                cursor = conn.execute("""
                INSERT INTO candidates
                (name, email, cgpa, graduation_year,
                 resume_text, skills, technical_score)
                VALUES (?, ?, NULL, NULL, ?, ?, NULL)
                """, (name, email, text, json.dumps(skills)))

                candidate_id = cursor.lastrowid

                for job in conn.execute("SELECT id FROM jobs").fetchall():
                    conn.execute("""
                    INSERT OR IGNORE INTO applications(candidate_id, job_id)
                    VALUES (?, ?)
                    """, (candidate_id, job["id"]))

        if session.get("user_role") == "candidate":
            flash("Your resume was uploaded successfully.", "success")
            return redirect(url_for("candidate_dashboard"))

        flash("Resume uploaded and candidate profile created.", "success")
        return redirect(url_for("candidate_detail", candidate_id=candidate_id))

    return render_template("upload.html")


ASSESSMENT_QUESTIONS = [
    {"id": 1, "topic": "Python", "difficulty": 1, "question": "Which Python type is immutable?", "options": ["list", "dictionary", "tuple", "set"], "answer": "tuple", "explanation": "Tuples cannot be changed after creation."},
    {"id": 2, "topic": "SQL", "difficulty": 1, "question": "Which SQL clause filters rows before grouping?", "options": ["ORDER BY", "WHERE", "HAVING", "LIMIT"], "answer": "WHERE", "explanation": "WHERE filters rows before GROUP BY."},
    {"id": 3, "topic": "Data Structures", "difficulty": 1, "question": "Which structure follows LIFO?", "options": ["Queue", "Stack", "Heap", "Graph"], "answer": "Stack", "explanation": "A stack removes the most recently added item first."},
    {"id": 4, "topic": "Python", "difficulty": 1, "question": "Which keyword defines a function in Python?", "options": ["func", "define", "def", "function"], "answer": "def", "explanation": "The def keyword declares a function."},
    {"id": 5, "topic": "SQL", "difficulty": 1, "question": "Which command retrieves data from a table?", "options": ["GET", "SELECT", "FETCH TABLE", "READ"], "answer": "SELECT", "explanation": "SELECT retrieves rows from a database."},
    {"id": 6, "topic": "Data Structures", "difficulty": 1, "question": "Which structure follows FIFO?", "options": ["Stack", "Queue", "Tree", "Set"], "answer": "Queue", "explanation": "A queue removes the earliest inserted item first."},
    {"id": 7, "topic": "Python", "difficulty": 1, "question": "What does len([2,4,6]) return?", "options": ["2", "3", "4", "6"], "answer": "3", "explanation": "The list contains three elements."},
    {"id": 8, "topic": "SQL", "difficulty": 1, "question": "Which keyword removes duplicate rows from query results?", "options": ["UNIQUE", "DISTINCT", "ONLY", "DEDUP"], "answer": "DISTINCT", "explanation": "DISTINCT returns unique result rows."},
    {"id": 9, "topic": "Data Structures", "difficulty": 1, "question": "Which structure stores key-value pairs in Python?", "options": ["tuple", "list", "dict", "set"], "answer": "dict", "explanation": "Dictionaries map keys to values."},
    {"id": 10, "topic": "Python", "difficulty": 2, "question": "What is the average-case dictionary lookup complexity?", "options": ["O(1)", "O(log n)", "O(n)", "O(n log n)"], "answer": "O(1)", "explanation": "Hash-table lookup is O(1) on average."},
    {"id": 11, "topic": "SQL", "difficulty": 2, "question": "Which clause filters grouped aggregate results?", "options": ["WHERE", "HAVING", "ORDER BY", "FROM"], "answer": "HAVING", "explanation": "HAVING filters groups after aggregation."},
    {"id": 12, "topic": "Data Structures", "difficulty": 2, "question": "Which traversal of a BST returns sorted keys?", "options": ["Preorder", "Inorder", "Postorder", "Level order"], "answer": "Inorder", "explanation": "Inorder visits left subtree, node, then right subtree."},
    {"id": 13, "topic": "Python", "difficulty": 2, "question": "What does list comprehension create?", "options": ["A generator only", "A list", "A dictionary always", "A class"], "answer": "A list", "explanation": "List comprehensions build lists from an expression and iterable."},
    {"id": 14, "topic": "SQL", "difficulty": 2, "question": "What does COUNT(*) count?", "options": ["Only non-null values in one column", "All rows", "Distinct rows only", "Columns"], "answer": "All rows", "explanation": "COUNT(*) counts rows regardless of individual column nulls."},
    {"id": 15, "topic": "Data Structures", "difficulty": 2, "question": "Which structure is commonly used for BFS?", "options": ["Stack", "Queue", "Heap", "Hash map"], "answer": "Queue", "explanation": "BFS explores nodes in first-in-first-out order."},
    {"id": 16, "topic": "Python", "difficulty": 2, "question": "What is the result of bool([])?", "options": ["True", "False", "None", "Error"], "answer": "False", "explanation": "Empty lists are falsy in Python."},
    {"id": 17, "topic": "SQL", "difficulty": 2, "question": "Which join keeps every row from the left table?", "options": ["INNER JOIN", "LEFT JOIN", "CROSS JOIN", "SELF JOIN"], "answer": "LEFT JOIN", "explanation": "LEFT JOIN preserves all left-table rows."},
    {"id": 18, "topic": "Data Structures", "difficulty": 2, "question": "Binary search requires which property?", "options": ["Random order", "Sorted data", "A linked list only", "Unique values always"], "answer": "Sorted data", "explanation": "Binary search narrows the search interval in sorted data."},
    {"id": 19, "topic": "Python", "difficulty": 2, "question": "Which exception handles a missing dictionary key with d[k]?", "options": ["IndexError", "KeyError", "ValueError", "TypeError"], "answer": "KeyError", "explanation": "A missing key raises KeyError."},
    {"id": 20, "topic": "SQL", "difficulty": 2, "question": "Which aggregate returns the mean value?", "options": ["SUM", "COUNT", "AVG", "MEAN"], "answer": "AVG", "explanation": "AVG computes the arithmetic mean."},
    {"id": 21, "topic": "Data Structures", "difficulty": 3, "question": "What is the worst-case search time in an unbalanced BST?", "options": ["O(1)", "O(log n)", "O(n)", "O(n log n)"], "answer": "O(n)", "explanation": "An unbalanced tree can degrade into a chain."},
    {"id": 22, "topic": "Python", "difficulty": 3, "question": "What does a generator yield lazily?", "options": ["All results at once", "Values one at a time", "Only strings", "Only integers"], "answer": "Values one at a time", "explanation": "Generators produce values on demand."},
    {"id": 23, "topic": "SQL", "difficulty": 3, "question": "Which index type is generally useful for equality lookups on a column?", "options": ["B-tree index", "No index ever", "View only", "Trigger"], "answer": "B-tree index", "explanation": "B-tree indexes commonly support equality and range lookups."},
    {"id": 24, "topic": "Data Structures", "difficulty": 3, "question": "Which algorithmic technique is used by merge sort?", "options": ["Greedy only", "Divide and conquer", "Backtracking only", "Randomized search"], "answer": "Divide and conquer", "explanation": "Merge sort splits, sorts, and merges subproblems."},
    {"id": 25, "topic": "Python", "difficulty": 3, "question": "What is the purpose of a context manager with `with`?", "options": ["Repeat a loop", "Manage setup and cleanup", "Declare a class", "Sort a list"], "answer": "Manage setup and cleanup", "explanation": "Context managers reliably perform cleanup, such as closing files."},
    {"id": 26, "topic": "SQL", "difficulty": 3, "question": "What is a correlated subquery?", "options": ["A query unrelated to outer query", "A subquery referencing outer query columns", "A table constraint", "A join type"], "answer": "A subquery referencing outer query columns", "explanation": "A correlated subquery refers to values from the outer query."},
    {"id": 27, "topic": "Data Structures", "difficulty": 3, "question": "What is the average lookup complexity of a well-designed hash table?", "options": ["O(n)", "O(log n)", "O(1)", "O(n\u00b2)"], "answer": "O(1)", "explanation": "Hash tables offer constant average-case lookup."},
    {"id": 28, "topic": "Python", "difficulty": 3, "question": "Which structure is best suited to repeated membership checks on unique items?", "options": ["List only", "Set", "Tuple only", "String"], "answer": "Set", "explanation": "Sets are hash-based and support average O(1) membership checks."},
    {"id": 29, "topic": "SQL", "difficulty": 3, "question": "Which window function assigns a unique sequential number to rows?", "options": ["RANK()", "ROW_NUMBER()", "COUNT() only", "GROUP BY"], "answer": "ROW_NUMBER()", "explanation": "ROW_NUMBER assigns a sequential row number within a window."},
    {"id": 30, "topic": "Data Structures", "difficulty": 3, "question": "Which shortest-path algorithm supports non-negative edge weights?", "options": ["Binary search", "Dijkstra", "Merge sort", "DFS only"], "answer": "Dijkstra", "explanation": "Dijkstra finds shortest paths with non-negative edge weights."}
]


# Candidate-only assessment
@app.route("/assessment", methods=["GET", "POST"])
@role_required("candidate")
def assessment():
    # A short adaptive flow: correct answers raise difficulty; incorrect answers lower it.
    state = session.get("adaptive_assessment")
    if request.method == "GET":
        level = request.args.get("level", "adaptive")
        if level != "adaptive":
            questions = [q for q in ASSESSMENT_QUESTIONS if q["difficulty"] == int(level)]
            return render_template("assessment.html", questions=questions, difficulty=level,
                                   adaptive=False, current=None, question_number=1, question_total=len(questions))
        session["adaptive_assessment"] = {"level": 1, "asked": [], "results": [], "topic_scores": {}}
        state = session["adaptive_assessment"]
        first = next(q for q in ASSESSMENT_QUESTIONS if q["difficulty"] == 1)
        state["asked"] = [first["id"]]
        session.modified = True
        return render_template("assessment.html", questions=[], difficulty="adaptive",
                               adaptive=True, current=first, question_number=1, question_total=25)

    if not active_integrity_session("assessment"):
        flash(
            "Start screen sharing and the monitored assessment session before submitting answers.",
            "error",
        )
        return redirect(url_for("assessment"))

    if not state:
        flash("Start the assessment before submitting an answer.", "error")
        return redirect(url_for("assessment"))

    question_id = request.form.get("question_id", type=int)
    question = next((q for q in ASSESSMENT_QUESTIONS if q["id"] == question_id), None)
    if not question or question_id not in state["asked"]:
        flash("That assessment question is no longer active. Please restart.", "error")
        session.pop("adaptive_assessment", None)
        return redirect(url_for("assessment"))

    chosen = request.form.get("answer", "")
    correct = chosen == question["answer"]
    state["results"].append({**question, "chosen": chosen or "Not answered",
                             "correct_answer": question["answer"], "is_correct": correct})
    topic = question["topic"]
    state["topic_scores"].setdefault(topic, {"correct": 0, "total": 0})
    state["topic_scores"][topic]["total"] += 1
    state["topic_scores"][topic]["correct"] += int(correct)

    next_level = min(3, state["level"] + 1) if correct else max(1, state["level"] - 1)
    unused = [q for q in ASSESSMENT_QUESTIONS if q["id"] not in state["asked"]]
    next_question = None
    if unused and len(state["results"]) < 25:
        next_question = min(unused, key=lambda q: (abs(q["difficulty"] - next_level), q["difficulty"]))
    if next_question:
        state["level"] = next_question["difficulty"]
        state["asked"].append(next_question["id"])
        session["adaptive_assessment"] = state
        session.modified = True
        return render_template("assessment.html", questions=[], difficulty="adaptive",
                               adaptive=True, current=next_question,
                               question_number=len(state["results"]) + 1, question_total=25)

    results = state["results"]
    score = sum(int(r["is_correct"]) for r in results)
    total = len(results)
    percentage = round(score / total * 100, 2) if total else 0
    topic_scores = state["topic_scores"]
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO assessments(candidate_id, score, total, topic_scores, answers_json)
            VALUES (?, ?, ?, ?, ?)
        """, (session.get("candidate_id"), score, total, json.dumps(topic_scores),
              json.dumps([{"question_id": r["id"], "answer": r["chosen"], "correct": r["is_correct"], "topic": r["topic"]} for r in results])))
    session.pop("adaptive_assessment", None)
    strengths = [topic for topic, data in topic_scores.items() if data["total"] and data["correct"] / data["total"] >= 0.7]
    improvements = [topic for topic, data in topic_scores.items() if data["total"] and data["correct"] / data["total"] < 0.7]
    recommendations = {"Python": "Practise functions, data types, exceptions and generators.", "SQL": "Revise filtering, joins, aggregation, indexes and window functions.", "Data Structures": "Practise stacks, queues, trees, hash tables and algorithm complexity."}
    return render_template("assessment_result.html", score=score, total=total,
                           percentage=percentage, results=results, topic_scores=topic_scores,
                           strengths=strengths, improvements=improvements, recommendations=recommendations)


# Shared interview page
@app.route("/interview", methods=["GET", "POST"])
@role_required("candidate")
def interview():
    jobs = get_jobs()
    role = session.get("user_role")

    with get_connection() as conn:
        all_candidates = [
            dict(row) for row in conn.execute(
                "SELECT id, name, email FROM candidates ORDER BY name LIMIT 1000"
            ).fetchall()
        ]

    # Candidates can only interview using their own profile.
    if role == "candidate":
        candidate_id = session.get("candidate_id")
    else:
        candidate_id = request.args.get("candidate_id", type=int)

    candidate = get_candidate(candidate_id) if candidate_id else None

    selected_job_id = request.args.get("job_id", type=int)
    job = next(
        (j for j in jobs if j["id"] == selected_job_id),
        jobs[0] if jobs else None
    )

    questions = [
        "Describe a project relevant to this role. What did you personally implement?",
        "Explain a technical challenge you faced, the options you considered, and why you chose your solution.",
        "How did you test your work and measure whether it succeeded?"
    ]

    if request.method == "POST":
        if not active_integrity_session("interview"):
            flash(
                "Start the monitored interview session before submitting an answer.",
                "error"
            )
            return redirect(url_for("interview"))

        if role == "candidate":
            candidate_id = session.get("candidate_id")
        else:
            candidate_id = request.form.get("candidate_id", type=int)

        candidate = get_candidate(candidate_id) if candidate_id else None

        if role == "candidate" and not candidate:
            flash("Your candidate profile was not found.", "error")
            return redirect(url_for("candidate_dashboard"))

        job_id = request.form.get("job_id", type=int)
        job = next((j for j in jobs if j["id"] == job_id), None)

        if not job:
            flash("Please select a valid job role.", "error")
            return redirect(url_for("interview"))

        answer = request.form.get("answer", "").strip()
        question = request.form.get(
            "question",
            "Explain a relevant project and your contribution."
        )

        if len(answer) < 10:
            flash("Please enter a more detailed answer.", "error")
            return redirect(url_for("interview", job_id=job_id))

        word_count = len(answer.split())

        evidence_terms = [
            "because", "implemented", "designed", "tested",
            "measured", "improved", "result", "trade-off",
            "challenge", "solution"
        ]

        evidence_count = sum(
            1 for term in evidence_terms if term in answer.lower()
        )

        clarity = min(100, round(word_count / 1.2))
        evidence = min(100, evidence_count * 15)

        resume_text = (candidate or {}).get("resume_text", "")
        candidate_skills = extract_skills(resume_text)

        relevance = min(
            100,
            35 + sum(
                1 for skill in candidate_skills
                if skill.lower() in answer.lower()
            ) * 12
        )

        score = round(
            0.35 * clarity + 0.35 * evidence + 0.30 * relevance,
            1
        )

        feedback = []

        if word_count < 40:
            feedback.append(
                "Add more detail and explain your own contribution."
            )

        if evidence_count < 2:
            feedback.append(
                "Include specific actions, decisions, testing and measurable outcomes."
            )

        if not feedback:
            feedback.append(
                "Good structure detected. A human reviewer should verify the technical accuracy."
            )

        with get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO interviews
                    (candidate_id, job_id, question, answer, score, feedback)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                candidate_id,
                job["id"],
                question,
                answer,
                score,
                "\n".join(feedback)
            ))

            interview_id = cursor.lastrowid

        return render_template(
            "interview_result.html",
            score=score,
            feedback=feedback,
            answer=answer,
            question=question,
            interview_id=interview_id
        )

    return render_template(
        "interview.html",
        jobs=jobs,
        candidate=candidate,
        all_candidates=all_candidates if role == "recruiter" else [],
        job=job,
        questions=questions
    )

# Recruiter-only recruitment dashboard
@app.route("/recruiter")
@role_required("recruiter")
def recruiter():
    jobs = get_jobs()
    selected_job_id = request.args.get("job_id", type=int)
    job = next((j for j in jobs if j["id"] == selected_job_id), jobs[0] if jobs else None)
    ranked = []
    with get_connection() as conn:
        assessments = conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
        interviews = conn.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        if job:
            rows = conn.execute("""
                SELECT c.*, a.id AS application_id, a.status
                FROM candidates c JOIN applications a ON a.candidate_id=c.id
                WHERE a.job_id=? ORDER BY c.id LIMIT 100
            """, (job["id"],)).fetchall()
            ranked = rank_candidates(rows, job)
    return render_template("recruiter.html", jobs=jobs, selected_job=job, candidates=ranked,
                           assessment_count=assessments, interview_count=interviews)


# Recruiter-only research and analytics
@app.route("/research")
@role_required("recruiter")
def research():
    with get_connection() as conn:
        candidate_count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        application_count = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        kaggle_count = conn.execute("SELECT COUNT(*) FROM candidates WHERE email LIKE 'kaggle%'").fetchone()[0]
        uploaded_count = conn.execute("SELECT COUNT(*) FROM candidates WHERE email LIKE 'upload_%'").fetchone()[0]
        assessment_count = conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
        interview_count = conn.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]
        categories = conn.execute("SELECT resume_text FROM candidates WHERE resume_text LIKE 'Resume category:%'").fetchall()
    category_counts = {}
    for row in categories:
        match = re.search(r"(?im)^Resume category:\s*(.+)$", row["resume_text"] or "")
        if match:
            category = match.group(1).strip()
            category_counts[category] = category_counts.get(category, 0) + 1
    top_categories = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    return render_template("research.html", candidate_count=candidate_count,
                           application_count=application_count, kaggle_count=kaggle_count,
                           uploaded_count=uploaded_count, assessment_count=assessment_count,
                           interview_count=interview_count, top_categories=top_categories)


@app.route("/health")
def health():
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    return {"status": "ok", "candidate_count": count}

@app.route("/recruiter/interview-results")
@role_required("recruiter")
def interview_results():
    with get_connection() as conn:
        results = conn.execute("""
            SELECT i.*, c.name AS candidate_name, j.title AS job_title
            FROM interviews i
            LEFT JOIN candidates c ON c.id = i.candidate_id
            LEFT JOIN jobs j ON j.id = i.job_id
            ORDER BY i.id DESC
        """).fetchall()

    return render_template(
        "interview_results.html",
        results=[dict(row) for row in results]
    )


@app.route("/recruiter/assessment-results")
@role_required("recruiter")
def assessment_results():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                a.id,
                a.candidate_id,
                a.score,
                a.total,
                a.topic_scores,
                a.answers_json,
                a.created_at,
                c.name AS candidate_name,
                c.email AS candidate_email
            FROM assessments a
            LEFT JOIN candidates c ON c.id = a.candidate_id
            ORDER BY a.id DESC
        """).fetchall()

    reports = []

    question_lookup = {
        q["id"]: q for q in ASSESSMENT_QUESTIONS
    }

    for row in rows:
        report = dict(row)

        try:
            report["topics"] = json.loads(
                report["topic_scores"] or "{}"
            )
        except (TypeError, json.JSONDecodeError):
            report["topics"] = {}

        try:
            saved_answers = json.loads(
                report["answers_json"] or "[]"
            )
        except (TypeError, json.JSONDecodeError):
            saved_answers = []

        report["answers"] = []

        for answer in saved_answers:
            question = question_lookup.get(
                answer.get("question_id"), {}
            )

            report["answers"].append({
                "topic": answer.get(
                    "topic", question.get("topic", "General")
                ),
                "question": question.get(
                    "question", "Question unavailable"
                ),
                "chosen": answer.get("answer", "Not answered"),
                "correct_answer": question.get(
                    "answer", "Unavailable"
                ),
                "is_correct": answer.get("correct", False)
            })

        reports.append(report)

    return render_template(
        "assessment_results.html",
        results=reports
    )


if __name__ == "__main__":
    initialize_database()
    seed_database()
    initialize_integrity_tables()
    initialize_auth_table()
    app.run(debug=True)
