
import json

from database import get_connection, initialize_database, seed_database
from matcher import rank_candidates


initialize_database()
seed_database()

with get_connection() as conn:
    job = conn.execute(
        "SELECT * FROM jobs WHERE id = 1"
    ).fetchone()

    candidates = conn.execute(
        """
        SELECT
            c.*,
            a.id AS application_id
        FROM candidates c
        JOIN applications a ON a.candidate_id = c.id
        WHERE a.job_id = ?
        """,
        (job["id"],),
    ).fetchall()

ranked = rank_candidates(candidates, job)

print("\nTOP 5 CANDIDATES")
print("-" * 65)

for candidate in ranked[:5]:
    print(
        candidate["name"],
        "| Eligible:", candidate["eligible"],
        "| Skills:", candidate["skill_score"],
        "| TF-IDF:", candidate["tfidf_score"],
        "| Technical:", candidate["technical_score"],
        "| Hybrid:", candidate["hybrid_score"],
    )

print("\nTotal candidates:", len(ranked))
print(
    "Eligible candidates:",
    sum(candidate["eligible"] for candidate in ranked),
)
