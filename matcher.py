import json
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


SKILL_DICTIONARY = [
    "python", "java", "c++", "c#", "sql", "mysql", "postgresql",
    "pandas", "numpy", "scipy", "machine learning", "scikit-learn",
    "statistics", "html", "css", "javascript", "typescript", "react",
    "node.js", "express", "git", "data structures", "algorithms",
    "excel", "flask", "fastapi", "tensorflow", "pytorch", "power bi",
    "tableau", "deep learning", "natural language processing",
    "nlp", "data analysis", "data visualization", "mongodb", "aws",
    "azure", "linux", "docker", "kubernetes", "spark", "hadoop",
    "communication", "project management", "leadership", "sales",
    "recruitment", "talent acquisition", "payroll", "accounting",
    "financial analysis", "marketing", "seo", "content writing",
    "customer service", "data entry", "networking", "cybersecurity",
    "cloud computing", "powerpoint", "word", "c", "r programming",
    "scikit learn", "computer vision", "opencv", "nlp", "agile",
]

ALIASES = {
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "js": "javascript",
    "powerbi": "power bi",
    "ms excel": "excel",
    "natural language processing (nlp)": "natural language processing",
}


def extract_skills(resume_text):
    text = str(resume_text or "").lower()
    for alias, canonical in ALIASES.items():
        text = re.sub(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", canonical, text)
    detected = set()
    for skill in set(SKILL_DICTIONARY):
        # Word boundaries reduce false positives such as "c" in ordinary words.
        pattern = r"(?<!\w)" + re.escape(skill) + r"(?!\w)"
        if re.search(pattern, text):
            detected.add(skill)
    if "scikit learn" in detected:
        detected.discard("scikit learn")
        detected.add("scikit-learn")
    return sorted(detected)


def calculate_skill_score(candidate_skills, required_skills):
    candidate = {str(s).strip().lower() for s in candidate_skills}
    required = {str(s).strip().lower() for s in required_skills}
    if not required:
        return 0.0
    return round(100 * len(candidate.intersection(required)) / len(required), 2)


def check_eligibility(candidate, job):
    reasons, unknown = [], []
    cgpa = candidate.get("cgpa")
    graduation_year = candidate.get("graduation_year")
    if cgpa is None:
        unknown.append("CGPA unavailable")
    elif float(cgpa) < float(job["min_cgpa"]):
        reasons.append(f"CGPA below minimum of {job['min_cgpa']}")
    if graduation_year is None:
        unknown.append("Graduation year unavailable")
    elif int(graduation_year) != int(job["graduation_year"]):
        reasons.append("Graduation year does not match")
    if reasons:
        return False, reasons
    if unknown:
        return None, unknown
    return True, ["All configured eligibility checks passed"]


def calculate_tfidf_scores(job_description, resume_texts):
    resume_texts = [str(text or "")[:6000] for text in resume_texts]
    if not resume_texts:
        return []
    documents = [str(job_description or "")[:6000]] + resume_texts
    try:
        vectorizer = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2),
            max_features=10000, dtype=float, sublinear_tf=True
        )
        matrix = vectorizer.fit_transform(documents)
        scores = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
        return [round(float(score) * 100, 2) for score in scores]
    except ValueError:
        return [0.0] * len(resume_texts)


def calculate_hybrid_score(skill_score, tfidf_score, technical_score):
    available = [(0.50, skill_score), (0.20, tfidf_score), (0.30, technical_score)]
    available = [(weight, float(score)) for weight, score in available if score is not None]
    if not available:
        return None
    total_weight = sum(weight for weight, _ in available)
    return round(sum(weight * score for weight, score in available) / total_weight, 2)


def rank_candidates(candidates, job):
    candidates = [dict(candidate) for candidate in candidates]
    if not candidates:
        return []
    try:
        required_skills = json.loads(job["required_skills"] or "[]")
    except (TypeError, json.JSONDecodeError):
        required_skills = []
    tfidf_scores = calculate_tfidf_scores(
        job["description"], [candidate.get("resume_text", "") for candidate in candidates]
    )
    for index, candidate in enumerate(candidates):
        try:
            stored_skills = json.loads(candidate.get("skills") or "[]")
        except (TypeError, json.JSONDecodeError):
            stored_skills = []
        detected_skills = extract_skills(candidate.get("resume_text", ""))
        all_skills = sorted(set(stored_skills) | set(detected_skills))
        candidate["extracted_skills"] = all_skills
        candidate["matched_skills"] = sorted(set(all_skills) & set(required_skills))
        candidate["missing_skills"] = sorted(set(required_skills) - set(all_skills))
        candidate["skill_score"] = calculate_skill_score(all_skills, required_skills)
        candidate["tfidf_score"] = tfidf_scores[index]
        technical = candidate.get("technical_score")
        candidate["technical_score"] = float(technical) if technical is not None else None
        candidate["hybrid_score"] = calculate_hybrid_score(
            candidate["skill_score"], candidate["tfidf_score"], candidate["technical_score"]
        )
        eligible, reasons = check_eligibility(candidate, job)
        candidate["eligible"] = eligible
        candidate["eligibility_reason"] = "; ".join(reasons)
    candidates.sort(
        key=lambda c: (c["hybrid_score"] is not None,
                       c["hybrid_score"] if c["hybrid_score"] is not None else -1),
        reverse=True
    )
    return candidates
