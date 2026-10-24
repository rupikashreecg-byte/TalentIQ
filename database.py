
import json
import random
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "placement.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database():
    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                cgpa REAL,
                graduation_year INTEGER,
                resume_text TEXT NOT NULL,
                skills TEXT NOT NULL,
                technical_score REAL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                required_skills TEXT NOT NULL,
                min_cgpa REAL NOT NULL,
                graduation_year INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'Applied',
                UNIQUE(candidate_id, job_id),
                FOREIGN KEY(candidate_id) REFERENCES candidates(id),
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );

            CREATE TABLE IF NOT EXISTS selection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(application_id) REFERENCES applications(id)
            );

            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                score REAL NOT NULL,
                total INTEGER NOT NULL,
                topic_scores TEXT NOT NULL,
                answers_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id)
            );

            CREATE TABLE IF NOT EXISTS interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                job_id INTEGER,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                score REAL NOT NULL,
                feedback TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id),
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
        """)

        columns = conn.execute(
            "PRAGMA table_info(candidates)"
        ).fetchall()

        required_nullable_columns = {
            "cgpa", "graduation_year", "technical_score"
        }

        needs_migration = any(
            column[1] in required_nullable_columns and column[3] == 1
            for column in columns
        )

        if needs_migration:
            conn.execute("""
                CREATE TABLE candidates_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    cgpa REAL,
                    graduation_year INTEGER,
                    resume_text TEXT NOT NULL,
                    skills TEXT NOT NULL,
                    technical_score REAL
                )
            """)

            conn.execute("""
                INSERT INTO candidates_new
                (id, name, email, cgpa, graduation_year,
                 resume_text, skills, technical_score)
                SELECT id, name, email, cgpa, graduation_year,
                       resume_text, skills, technical_score
                FROM candidates
            """)

            conn.execute("DROP TABLE candidates")
            conn.execute(
                "ALTER TABLE candidates_new RENAME TO candidates"
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


def seed_database():
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM jobs"
        ).fetchone()[0]

        if count > 0:
            return

        jobs = [
            (
                "Junior Data Analyst",
                "Analyse datasets using Python, SQL, Pandas and statistics.",
                ["python", "sql", "pandas", "statistics"],
                7.0, 2028
            ),
            (
                "Machine Learning Intern",
                "Build ML models using Python, scikit-learn and NumPy.",
                ["python", "machine learning", "scikit-learn", "numpy"],
                7.5, 2028
            ),
            (
                "Junior Web Developer",
                "Develop websites using HTML, CSS, JavaScript and React.",
                ["html", "css", "javascript", "react"],
                6.5, 2028
            ),
        ]

        for title, description, skills, cgpa, year in jobs:
            conn.execute("""
                INSERT INTO jobs
                (title, description, required_skills,
                 min_cgpa, graduation_year)
                VALUES (?, ?, ?, ?, ?)
            """, (
                title, description, json.dumps(skills), cgpa, year
            ))

        random.seed(42)

        skill_pool = [
            "python", "sql", "pandas", "numpy",
            "machine learning", "scikit-learn", "statistics",
            "html", "css", "javascript", "react", "java",
            "git", "data structures", "excel"
        ]

        for i in range(1, 101):
            skills = random.sample(
                skill_pool, random.randint(3, 8)
            )

            resume_text = (
                "Computer Science student with skills in "
                + ", ".join(skills)
                + ". Completed academic projects using these technologies."
            )

            cursor = conn.execute("""
                INSERT INTO candidates
                (name, email, cgpa, graduation_year,
                 resume_text, skills, technical_score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                f"Candidate {i:03d}",
                f"candidate{i:03d}@example.com",
                round(random.uniform(6.0, 10.0), 2),
                random.choice([2027, 2028, 2028, 2029]),
                resume_text,
                json.dumps(skills),
                random.randint(35, 100)
            ))

            for job_id in (1, 2, 3):
                conn.execute("""
                    INSERT OR IGNORE INTO applications
                    (candidate_id, job_id)
                    VALUES (?, ?)
                """, (cursor.lastrowid, job_id))


if __name__ == "__main__":
    initialize_database()
    seed_database()

    with get_connection() as conn:
        print("Database initialized successfully!")
        print(
            "Candidates:",
            conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        )
        print(
            "Jobs:",
            conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        )
