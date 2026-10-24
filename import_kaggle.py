import hashlib
import json
from pathlib import Path

import pandas as pd

from database import get_connection, initialize_database, seed_database
from matcher import extract_skills

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "dataset" / "Resume.csv"


def main():
    if not CSV_PATH.exists():
        print(f"Dataset not found: {CSV_PATH}")
        return

    initialize_database()
    seed_database()
    df = pd.read_csv(CSV_PATH, encoding="utf-8", on_bad_lines="skip", low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    columns = {c.lower(): c for c in df.columns}
    resume_col, category_col = columns.get("resume_str"), columns.get("category")
    if not resume_col or not category_col:
        print("Required columns Resume_str and Category not found.")
        print("Columns found:", list(df.columns))
        return

    df = df.dropna(subset=[resume_col, category_col]).copy()
    df[resume_col] = df[resume_col].astype(str).str.strip()
    df[category_col] = df[category_col].astype(str).str.strip()
    df = df[(df[resume_col].str.len() > 30) & (df[category_col].str.len() > 0)]
    df = df.drop_duplicates(subset=[resume_col])
    imported = skipped = 0

    with get_connection() as conn:
        job_ids = [r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()]
        for _, row in df.iterrows():
            resume = row[resume_col]
            category = row[category_col]
            digest = hashlib.sha256(resume.encode("utf-8")).hexdigest()[:24]
            email = f"kaggle_{digest}@example.invalid"
            if conn.execute("SELECT 1 FROM candidates WHERE email=?", (email,)).fetchone():
                skipped += 1
                continue
            text = f"Resume category: {category}\n{resume}"
            cursor = conn.execute("""
                INSERT INTO candidates(name,email,cgpa,graduation_year,resume_text,skills,technical_score)
                VALUES(?,?,NULL,NULL,?,?,NULL)
            """, (f"Kaggle Candidate {digest[:8]}", email, text, json.dumps(extract_skills(text))))
            for job_id in job_ids:
                conn.execute("INSERT OR IGNORE INTO applications(candidate_id,job_id) VALUES(?,?)",
                             (cursor.lastrowid, job_id))
            imported += 1

    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        kaggle = conn.execute("SELECT COUNT(*) FROM candidates WHERE email LIKE 'kaggle%'").fetchone()[0]
    print(f"Valid unique resumes in CSV: {len(df)}")
    print(f"New resumes imported: {imported}")
    print(f"Existing hash-matched resumes skipped: {skipped}")
    print(f"Kaggle-tagged rows in DB: {kaggle}")
    print(f"Total candidate records: {total}")
    print("CGPA, graduation year, and technical assessment remain unknown for imported Kaggle resumes.")


if __name__ == "__main__":
    main()
