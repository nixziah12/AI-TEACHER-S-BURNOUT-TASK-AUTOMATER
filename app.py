from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from dotenv import load_dotenv
from PIL import Image
from PIL import ImageEnhance
from PIL import ImageFilter
from PIL import ImageOps
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import pytesseract
except Exception:
    pytesseract = None

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATABASE = BASE_DIR / "teacher_burnout.db"
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf"}
STOP_WORDS = {
    "the", "and", "for", "that", "with", "from", "this", "into", "their", "about",
    "have", "your", "they", "them", "were", "what", "when", "where", "which", "while",
    "there", "because", "been", "being", "than", "then", "also", "very", "much", "more",
    "some", "just", "only", "make", "made", "using", "used", "after", "before", "under",
    "over", "does", "did", "done", "our", "out", "any", "each", "through", "during",
    "lesson", "class", "student", "students", "response", "answer", "write",
}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "teacher-burnout-secret")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.jinja_env.filters["fromjson"] = json.loads


def configure_tesseract() -> bool:
    if pytesseract is None:
        return False

    configured_path = os.environ.get("TESSERACT_CMD", "").strip()
    candidate_paths = [
        configured_path,
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        str(BASE_DIR / "Tesseract-OCR" / "tesseract.exe"),
    ]
    for candidate in candidate_paths:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return True

    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


TESSERACT_READY = configure_tesseract()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = sqlite3.connect(DATABASE)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            student_name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            topic TEXT NOT NULL,
            rubric TEXT,
            keywords TEXT NOT NULL,
            extracted_text TEXT NOT NULL,
            ocr_status TEXT NOT NULL,
            score REAL NOT NULL,
            understanding_level TEXT NOT NULL,
            matched_keywords TEXT NOT NULL,
            missing_keywords TEXT NOT NULL,
            keyword_breakdown TEXT NOT NULL,
            detailed_feedback TEXT NOT NULL,
            recommendations TEXT NOT NULL,
            present INTEGER NOT NULL DEFAULT 1,
            file_name TEXT,
            file_path TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    existing_columns = {row[1] for row in db.execute("PRAGMA table_info(scans)").fetchall()}
    if "feedback_source" not in existing_columns:
        db.execute("ALTER TABLE scans ADD COLUMN feedback_source TEXT NOT NULL DEFAULT 'rules'")
    db.commit()
    db.close()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.translate(str.maketrans({
        "0": "o",
        "1": "l",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "8": "b",
    }))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_text(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def token_similarity(candidate: str, keyword: str) -> float:
    return difflib.SequenceMatcher(None, normalize_text(candidate), normalize_text(keyword)).ratio()


def keyword_matches_text(keyword: str, normalized_text: str, text_tokens: list[str]) -> bool:
    keyword_normalized = normalize_text(keyword)
    if not keyword_normalized:
        return False

    if keyword_normalized in normalized_text:
        return True

    keyword_compact = keyword_normalized.replace(" ", "")
    if keyword_compact and keyword_compact in normalized_text.replace(" ", ""):
        return True

    keyword_tokens = keyword_normalized.split()
    if len(keyword_tokens) > 1:
        window_size = len(keyword_tokens)
        for index in range(0, max(len(text_tokens) - window_size + 1, 0)):
            candidate = " ".join(text_tokens[index:index + window_size])
            if token_similarity(candidate, keyword_normalized) >= 0.78:
                return True
        return all(
            any(token_similarity(token, keyword_token) >= 0.78 for token in text_tokens)
            for keyword_token in keyword_tokens
        )

    return any(token_similarity(token, keyword_normalized) >= 0.78 for token in text_tokens)


def split_multiline_input(raw_value: str) -> list[str]:
    return [line.strip() for line in raw_value.splitlines() if line.strip()]


def choose_student_name(index: int, provided_names: list[str], filename: str) -> str:
    if index < len(provided_names):
        return provided_names[index]
    inferred = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return inferred.title() if inferred else f"Student {index + 1}"


def extract_keywords_from_rubric(rubric: str) -> list[str]:
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", " ", rubric.lower())
    words = [word for word in cleaned.split() if len(word) > 3 and word not in STOP_WORDS]
    common = []
    seen = set()
    for word in words:
        if word not in seen:
            seen.add(word)
            common.append(word)
    return common[:8]


def keyword_list(raw_keywords: str, rubric: str) -> list[str]:
    parsed = [normalize_text(item) for item in raw_keywords.split(",") if normalize_text(item)]
    if parsed:
        return parsed
    return extract_keywords_from_rubric(rubric)


def crop_to_content(image: Image.Image) -> Image.Image:
    gray = image.convert("L")
    threshold = 235
    width, height = gray.size
    dark_pixels = []
    for y in range(height):
        for x in range(width):
            if gray.getpixel((x, y)) < threshold:
                dark_pixels.append((x, y))

    if not dark_pixels:
        return image

    left = max(min(x for x, _ in dark_pixels) - 25, 0)
    top = max(min(y for _, y in dark_pixels) - 25, 0)
    right = min(max(x for x, _ in dark_pixels) + 25, width)
    bottom = min(max(y for _, y in dark_pixels) + 25, height)
    return image.crop((left, top, right, bottom))


def clean_ocr_lines(text: str) -> str:
    text = text.replace("\x0c", "\n")
    text = re.sub(r"(?<=[a-zA-Z])-\s*\n\s*(?=[a-zA-Z])", "", text)
    raw_lines = [line.strip() for line in text.splitlines()]
    cleaned_lines = []
    for line in raw_lines:
        normalized_line = re.sub(r"\s+", " ", line).strip(" |/\\[]{}~`")
        normalized_line = re.sub(r"([a-zA-Z])\s+([.,;:!?])", r"\1\2", normalized_line)
        alnum_count = sum(1 for char in normalized_line if char.isalnum())
        if alnum_count < 3:
            continue
        if len(normalized_line) == 1:
            continue
        cleaned_lines.append(normalized_line)
    return "\n".join(cleaned_lines).strip()


def line_quality_score(line: str, keywords: list[str]) -> float:
    compact = line.strip()
    if not compact:
        return 0.0

    alnum_count = sum(1 for char in compact if char.isalnum())
    alpha_count = sum(1 for char in compact if char.isalpha())
    punctuation_count = sum(1 for char in compact if not char.isalnum() and not char.isspace())
    words = compact.split()
    avg_word_length = sum(len(word) for word in words) / len(words) if words else 0
    normalized_line = normalize_text(compact)
    line_tokens = normalized_line.split()
    keyword_hits = sum(1 for keyword in keywords if keyword_matches_text(keyword, normalized_line, line_tokens))

    score = alnum_count * 1.2 + alpha_count + keyword_hits * 18
    if len(compact) > 60:
        score -= (len(compact) - 60) * 0.6
    if punctuation_count > max(3, len(compact) // 8):
        score -= punctuation_count * 2.5
    if avg_word_length < 2.0:
        score -= 10
    return score


def keep_meaningful_lines(text: str, keywords: list[str]) -> str:
    lines = [line for line in clean_ocr_lines(text).splitlines() if line.strip()]
    if not lines:
        return ""

    scored_lines = []
    for index, line in enumerate(lines):
        score = line_quality_score(line, keywords)
        if line.lower().startswith("name"):
            score += 10
        if index < 3:
            score += 4
        scored_lines.append((index, line, score))

    strong_lines = [item for item in scored_lines if item[2] >= 10]
    if not strong_lines:
        strong_lines = sorted(scored_lines, key=lambda item: item[2], reverse=True)[:6]

    strong_lines = sorted(strong_lines, key=lambda item: item[0])[:12]
    return "\n".join(line for _, line, _ in strong_lines).strip()


def keyword_similarity(candidate: str, keyword: str) -> float:
    return token_similarity(candidate, keyword)


def repair_ocr_text(text: str, keywords: list[str]) -> str:
    text = clean_ocr_lines(text)
    if not text.strip():
        return ""
    if not keywords:
        return text.strip()

    single_word_keywords = [keyword for keyword in keywords if len(normalize_text(keyword).split()) == 1]
    repaired_lines = []
    for line in text.splitlines():
        words = line.split()
        repaired_words = []
        for word in words:
            bare_word = normalize_text(re.sub(r"[^a-zA-Z0-9]", "", word))
            replacement = word
            best_keyword = ""
            best_score = 0.0
            for keyword in single_word_keywords:
                score = keyword_similarity(bare_word, keyword)
                if score > best_score:
                    best_score = score
                    best_keyword = keyword
            if best_keyword and best_score >= 0.76:
                prefix = re.match(r"^[^a-zA-Z0-9]*", word).group(0)
                suffix = re.search(r"[^a-zA-Z0-9]*$", word).group(0)
                replacement = f"{prefix}{best_keyword}{suffix}"
            repaired_words.append(replacement)
        repaired_lines.append(" ".join(repaired_words))
    repaired_text = "\n".join(repaired_lines).strip()
    return repair_keyword_phrases(repaired_text, keywords)


def repair_keyword_phrases(text: str, keywords: list[str]) -> str:
    if not text.strip():
        return ""

    lines = []
    for line in text.splitlines():
        words = line.split()
        normalized_words = [normalize_text(word) for word in words]
        for keyword in sorted(keywords, key=lambda item: len(normalize_text(item).split()), reverse=True):
            keyword_tokens = normalize_text(keyword).split()
            if len(keyword_tokens) <= 1:
                continue
            window_size = len(keyword_tokens)
            for index in range(0, max(len(words) - window_size + 1, 0)):
                candidate = " ".join(normalized_words[index:index + window_size])
                if token_similarity(candidate, " ".join(keyword_tokens)) >= 0.78:
                    words[index:index + window_size] = [keyword]
                    normalized_words[index:index + window_size] = [normalize_text(keyword)]
                    break
        lines.append(" ".join(words))
    return "\n".join(lines).strip()


def extract_text_from_data(image: Image.Image, config: str, keywords: list[str]) -> tuple[str, float]:
    data = pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)
    line_map: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    confidences: list[float] = []

    for i, raw_text in enumerate(data["text"]):
        word = raw_text.strip()
        try:
            confidence = float(data["conf"][i])
        except (TypeError, ValueError):
            confidence = -1.0
        if not word or confidence < 5:
            continue
        line_key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line_map[line_key].append(word)
        confidences.append(confidence)

    ordered_lines = [" ".join(words) for _, words in sorted(line_map.items())]
    text = keep_meaningful_lines("\n".join(ordered_lines), keywords)
    average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return text, average_confidence


def extract_text_from_string(image: Image.Image, config: str, keywords: list[str]) -> tuple[str, float]:
    text = pytesseract.image_to_string(image, config=config)
    text = keep_meaningful_lines(text, keywords)
    return text, 0.0


def try_tesseract(file_path: Path, keywords: list[str]) -> str:
    if pytesseract is None or not TESSERACT_READY:
        return ""

    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return ""

    try:
        image = Image.open(file_path)
        image = ImageOps.exif_transpose(image)
        variants = build_ocr_variants(image)
        configs = [
            "--oem 3 --psm 6 -l eng -c preserve_interword_spaces=1",
            "--oem 3 --psm 11 -l eng -c preserve_interword_spaces=1",
            "--oem 3 --psm 12 -l eng -c preserve_interword_spaces=1",
            "--oem 3 --psm 4 -l eng -c preserve_interword_spaces=1",
            "--oem 3 --psm 3 -l eng -c preserve_interword_spaces=1",
            "--oem 3 --psm 7 -l eng -c preserve_interword_spaces=1",
        ]

        best_text = ""
        best_score = 0.0
        for variant in variants:
            for config in configs:
                for extractor in (extract_text_from_data, extract_text_from_string):
                    text, confidence = extractor(variant, config, keywords)
                    repaired = repair_ocr_text(text, keywords)
                    normalized_text = normalize_text(repaired)
                    text_tokens = normalized_text.split()
                    keyword_hits = sum(1 for keyword in keywords if keyword_matches_text(keyword, normalized_text, text_tokens))
                    score = ocr_text_score(repaired) + confidence * 1.8 + keyword_hits * 30
                    if score > best_score:
                        best_text = repaired
                        best_score = score
        return best_text.strip()
    except Exception:
        return ""


def build_ocr_variants(image: Image.Image) -> list[Image.Image]:
    cropped = crop_to_content(image)
    upper_crop = cropped.crop((0, 0, cropped.width, max(int(cropped.height * 0.68), 1)))
    gray = cropped.convert("L")
    enlarged = gray.resize((gray.width * 3, gray.height * 3))
    enlarged_4x = gray.resize((gray.width * 4, gray.height * 4))
    sharpened = enlarged.filter(ImageFilter.SHARPEN)
    smoothed = sharpened.filter(ImageFilter.MedianFilter(size=3))
    contrasted = ImageOps.autocontrast(smoothed)
    high_contrast = ImageEnhance.Contrast(contrasted).enhance(1.8)
    sharp_high_contrast = ImageEnhance.Sharpness(high_contrast).enhance(1.5)
    upper_gray = upper_crop.convert("L").resize((upper_crop.width * 3, upper_crop.height * 3))
    upper_contrasted = ImageOps.autocontrast(upper_gray.filter(ImageFilter.SHARPEN))
    four_x_contrasted = ImageOps.autocontrast(enlarged_4x.filter(ImageFilter.SHARPEN))

    threshold_light = contrasted.point(lambda pixel: 255 if pixel > 155 else 0)
    threshold_dark = contrasted.point(lambda pixel: 255 if pixel > 125 else 0)
    adaptive_soft = high_contrast.point(lambda pixel: 255 if pixel > 170 else 0)
    inverted = ImageOps.invert(contrasted)
    inverted_threshold = inverted.point(lambda pixel: 255 if pixel > 150 else 0)
    upper_threshold = upper_contrasted.point(lambda pixel: 255 if pixel > 155 else 0)

    return [
        contrasted,
        high_contrast,
        sharp_high_contrast,
        four_x_contrasted,
        threshold_light,
        threshold_dark,
        adaptive_soft,
        inverted_threshold,
        upper_contrasted,
        upper_threshold,
    ]


def ocr_text_score(text: str) -> int:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return 0
    alnum_count = sum(1 for char in cleaned if char.isalnum())
    word_count = len(cleaned.split())
    return alnum_count + word_count * 4


def extract_text(file_path: Path, manual_text: str, keywords: list[str]) -> tuple[str, str]:
    manual_text = manual_text.strip()
    if manual_text:
        return manual_text, "manual"

    tesseract_text = try_tesseract(file_path, keywords)
    if tesseract_text:
        repaired_text = repair_ocr_text(tesseract_text, keywords)
        return repaired_text, "tesseract"

    if file_path.suffix.lower() == ".pdf":
        return (
            "Free local OCR could not read this PDF directly. Upload the exit ticket as a JPG or PNG image, or paste the text manually.",
            "needs-review",
        )

    if not TESSERACT_READY:
        return (
            "Tesseract OCR is not installed or not detected. Install Tesseract, or set TESSERACT_CMD in your environment to the full tesseract.exe path.",
            "needs-review",
        )

    return (
        "OCR could not extract enough readable text from this image. Try a clearer, brighter photo with the handwriting centered, then re-upload or refresh the scan.",
        "needs-review",
    )


def build_local_feedback(
    student_name: str,
    extracted_text: str,
    keywords: list[str],
    matched: list[str],
    missing: list[str],
    score: float,
    understanding: str,
    rubric: str,
) -> tuple[str, list[str], str]:
    response_length = len(extracted_text.split())
    strength_line = "The response is still very limited and needs more detail."
    if matched:
        strength_line = f"{student_name} successfully mentioned {', '.join(matched[:3])}, which shows partial understanding."
        if len(matched) >= max(2, len(keywords) // 2):
            strength_line = f"{student_name} captured several core ideas, including {', '.join(matched[:4])}."

    gap_line = "Several essential lesson ideas are still missing."
    if missing:
        gap_line = f"The biggest gap is around {', '.join(missing[:3])}, so the explanation is not complete yet."
    elif keywords:
        gap_line = "The answer covers the expected keywords well and only needs refinement for clarity."

    writing_line = "The student would benefit from writing a longer, more complete explanation."
    if response_length >= 20:
        writing_line = "The response has enough length to show thinking, but a few ideas could be connected more clearly."
    if response_length >= 35:
        writing_line = "The response is detailed and shows clear effort, with room to sharpen precision."

    rubric_line = ""
    if rubric.strip():
        rubric_line = f"The feedback is based on the lesson target: {rubric.strip()[:140]}"

    feedback = " ".join(
        part for part in [strength_line, gap_line, writing_line, rubric_line] if part
    ).strip()

    recommendations: list[str] = []
    if missing:
        recommendations.append(f"Re-teach or review: {', '.join(missing[:3])}.")
    if score < 50:
        recommendations.append("Use a short guided reteach with one visual and one model answer.")
    elif score < 75:
        recommendations.append("Ask the student to revise the answer using the keyword checklist.")
    else:
        recommendations.append("Challenge the student with an extension question to deepen reasoning.")
    if response_length < 18:
        recommendations.append("Prompt the student to answer in 2-3 full sentences next time.")
    else:
        recommendations.append("Ask the student to connect the key terms in a clearer cause-and-effect explanation.")

    teacher_summary = (
        f"{student_name} is in the {understanding.lower()} band with a score of {score}%, "
        f"and should next focus on {', '.join(missing[:2]) if missing else 'precision and clarity'}."
    )
    return feedback, recommendations, teacher_summary


def analyze_response(student_name: str, extracted_text: str, keywords: list[str], rubric: str) -> dict[str, Any]:
    normalized = normalize_text(extracted_text)
    text_tokens = normalized.split()
    matched = []
    missing = []
    breakdown = []

    for keyword in keywords:
        is_present = keyword_matches_text(keyword, normalized, text_tokens)
        breakdown.append({"keyword": keyword, "score": 100 if is_present else 20})
        if is_present:
            matched.append(keyword)
        else:
            missing.append(keyword)

    coverage = len(matched) / len(keywords) if keywords else 0
    text_length_bonus = min(len(extracted_text.split()) / 45, 0.2)
    score = round(min((coverage * 0.8 + text_length_bonus) * 100, 100), 1)

    if score >= 80:
        understanding = "Strong"
    elif score >= 60:
        understanding = "Good"
    elif score >= 40:
        understanding = "Developing"
    else:
        understanding = "At Risk"

    summary_bits = []
    if matched:
        summary_bits.append(f"Included key ideas such as {', '.join(matched[:4])}.")
    if missing:
        summary_bits.append(f"Still missing important ideas like {', '.join(missing[:4])}.")
    if not extracted_text.strip():
        summary_bits.append("Response was empty, so the scan needs teacher review.")
    if rubric.strip():
        summary_bits.append(f"Rubric focus: {rubric.strip()[:160]}")

    feedback, recommendations, teacher_summary = build_local_feedback(
        student_name,
        extracted_text,
        keywords,
        matched,
        missing,
        score,
        understanding,
        rubric,
    )

    return {
        "score": score,
        "understanding": understanding,
        "matched": matched,
        "missing": missing,
        "breakdown": breakdown,
        "feedback": feedback or "The response needs a closer teacher review.",
        "recommendations": recommendations,
        "teacher_summary": teacher_summary,
        "feedback_source": "local-smart",
    }


def enrich_analysis_locally(analysis: dict[str, Any]) -> dict[str, Any]:
    return analysis


def get_current_user() -> sqlite3.Row | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def fetch_scans() -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM scans WHERE user_id = ? ORDER BY datetime(created_at) DESC",
        (session["user_id"],),
    ).fetchall()


def dashboard_context(scans: list[sqlite3.Row]) -> dict[str, Any]:
    total_students = len(scans)
    average_score = round(statistics.mean([scan["score"] for scan in scans]), 1) if scans else 0
    strong_count = sum(1 for scan in scans if scan["score"] >= 70)
    needs_help = total_students - strong_count

    score_distribution = {"Excellent": 0, "Good": 0, "Fair": 0, "Needs Help": 0}
    keyword_mastery: Counter[str] = Counter()
    keyword_totals: Counter[str] = Counter()
    misconception_counts: Counter[str] = Counter()
    student_streaks: defaultdict[str, int] = defaultdict(int)

    for scan in scans:
        score = scan["score"]
        if score >= 85:
            score_distribution["Excellent"] += 1
        elif score >= 70:
            score_distribution["Good"] += 1
        elif score >= 50:
            score_distribution["Fair"] += 1
        else:
            score_distribution["Needs Help"] += 1

        for keyword in json.loads(scan["keyword_breakdown"]):
            keyword_totals[keyword["keyword"]] += 1
            if keyword["score"] >= 70:
                keyword_mastery[keyword["keyword"]] += 1
            else:
                misconception_counts[keyword["keyword"]] += 1

        if scan["score"] < 60:
            student_streaks[scan["student_name"]] += 1

    topic_labels = list(keyword_totals.keys())
    topic_scores = [round((keyword_mastery[key] / keyword_totals[key]) * 100, 1) for key in topic_labels] if topic_labels else []
    weak_topics = sorted(zip(topic_labels, topic_scores), key=lambda item: item[1])[:5]
    top_students = sorted(scans, key=lambda item: item["score"], reverse=True)[:5]
    intervention_alerts = [name for name, count in student_streaks.items() if count >= 3]

    trend_points = []
    grouped_by_day: defaultdict[str, list[float]] = defaultdict(list)
    for scan in scans:
        grouped_by_day[scan["created_at"][:10]].append(scan["score"])
    for day, scores in sorted(grouped_by_day.items()):
        trend_points.append({"date": day, "average": round(sum(scores) / len(scores), 1)})

    common_misconceptions = [
        {"keyword": key, "count": count}
        for key, count in misconception_counts.most_common(5)
    ]

    return {
        "total_students": total_students,
        "average_score": average_score,
        "strong_count": strong_count,
        "needs_help": needs_help,
        "score_distribution": score_distribution,
        "topic_labels": topic_labels,
        "topic_scores": topic_scores,
        "weak_topics": weak_topics,
        "top_students": top_students,
        "intervention_alerts": intervention_alerts,
        "trend_points": trend_points,
        "common_misconceptions": common_misconceptions,
    }


@app.context_processor
def inject_globals() -> dict[str, Any]:
    return {
        "current_user": get_current_user(),
        "current_year": datetime.now().year,
        "tesseract_ready": TESSERACT_READY,
    }


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename: str):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (full_name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (full_name, email, generate_password_hash(password), datetime.now().isoformat()),
            )
            db.commit()
            flash("Account created. Please sign in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That email is already registered.", "error")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash("Welcome back.", "success")
            return redirect(url_for("home"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


@app.route("/home")
@login_required
def home():
    scans = fetch_scans()
    context = dashboard_context(scans)
    recent_scans = scans[:4]
    return render_template("home.html", recent_scans=recent_scans, metrics=context)


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        files = [file for file in request.files.getlist("ticket_files") if file and file.filename]
        if not files:
            single_file = request.files.get("ticket_file")
            if single_file and single_file.filename:
                files = [single_file]

        class_name = request.form["class_name"].strip() or "General Class"
        topic = request.form["topic"].strip() or "Untitled Lesson"
        rubric = request.form["rubric"].strip()
        keywords = keyword_list(request.form.get("keywords", ""), rubric)
        manual_text = request.form.get("student_response", "")
        provided_names = split_multiline_input(request.form.get("student_names", ""))

        if not files:
            flash("Please upload a file to continue.", "error")
            return redirect(url_for("upload"))

        db = get_db()
        created_scan_ids: list[int] = []
        skipped_files: list[str] = []

        for index, file in enumerate(files):
            if not allowed_file(file.filename):
                skipped_files.append(file.filename)
                continue

            student_name = choose_student_name(index, provided_names, file.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            safe_name = secure_filename(file.filename)
            stored_name = f"{timestamp}_{safe_name}"
            save_path = UPLOAD_FOLDER / stored_name
            file.save(save_path)

            manual_text_for_scan = manual_text if len(files) == 1 else ""
            extracted_text, ocr_status = extract_text(save_path, manual_text_for_scan, keywords)
            analysis = analyze_response(student_name, extracted_text, keywords, rubric)
            analysis = enrich_analysis_locally(analysis)

            db.execute(
                """
                INSERT INTO scans (
                    user_id, student_name, class_name, topic, rubric, keywords, extracted_text,
                    ocr_status, score, understanding_level, matched_keywords, missing_keywords,
                    keyword_breakdown, detailed_feedback, recommendations, present, file_name,
                    file_path, created_at, feedback_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    student_name,
                    class_name,
                    topic,
                    rubric,
                    json.dumps(keywords),
                    extracted_text,
                    ocr_status,
                    analysis["score"],
                    analysis["understanding"],
                    json.dumps(analysis["matched"]),
                    json.dumps(analysis["missing"]),
                    json.dumps(analysis["breakdown"]),
                    analysis["feedback"],
                    json.dumps(analysis["recommendations"]),
                    1,
                    file.filename,
                    stored_name,
                    datetime.now().isoformat(),
                    analysis["feedback_source"],
                ),
            )
            created_scan_ids.append(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

        db.commit()
        if not created_scan_ids:
            flash("No valid files were processed. Supported files are PNG, JPG, JPEG, and PDF.", "error")
            return redirect(url_for("upload"))
        if skipped_files:
            flash(f"Skipped unsupported files: {', '.join(skipped_files)}", "error")
        if len(created_scan_ids) == 1:
            flash("Exit ticket processed successfully.", "success")
            return redirect(url_for("scan_detail", scan_id=created_scan_ids[0]))
        flash(f"Batch upload complete. Processed {len(created_scan_ids)} exit tickets.", "success")
        return redirect(url_for("upload"))

    scans = fetch_scans()[:5]
    return render_template("upload.html", recent_scans=scans)


@app.route("/scan/<int:scan_id>")
@login_required
def scan_detail(scan_id: int):
    scan = get_db().execute(
        "SELECT * FROM scans WHERE id = ? AND user_id = ?",
        (scan_id, session["user_id"]),
    ).fetchone()
    if not scan:
        flash("Scan not found.", "error")
        return redirect(url_for("upload"))

    return render_template(
        "scan_detail.html",
        scan=scan,
        keywords=json.loads(scan["keywords"]),
        matched_keywords=json.loads(scan["matched_keywords"]),
        missing_keywords=json.loads(scan["missing_keywords"]),
        keyword_breakdown=json.loads(scan["keyword_breakdown"]),
        recommendations=json.loads(scan["recommendations"]),
        feedback_source=scan["feedback_source"] if "feedback_source" in scan.keys() else "local-smart",
    )


@app.route("/scan/<int:scan_id>/update", methods=["POST"])
@login_required
def update_scan(scan_id: int):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM scans WHERE id = ? AND user_id = ?",
        (scan_id, session["user_id"]),
    ).fetchone()
    if not existing:
        flash("Scan not found.", "error")
        return redirect(url_for("upload"))

    student_name = request.form["student_name"].strip() or existing["student_name"]
    class_name = request.form["class_name"].strip() or existing["class_name"]
    topic = request.form["topic"].strip() or existing["topic"]
    rubric = request.form["rubric"].strip()
    keywords = keyword_list(request.form.get("keywords", ""), rubric)
    extracted_text = request.form["extracted_text"].strip()
    analysis = analyze_response(student_name, extracted_text, keywords, rubric)
    analysis = enrich_analysis_locally(analysis)

    db.execute(
        """
        UPDATE scans
        SET student_name = ?, class_name = ?, topic = ?, rubric = ?, keywords = ?, extracted_text = ?,
            score = ?, understanding_level = ?, matched_keywords = ?, missing_keywords = ?,
            keyword_breakdown = ?, detailed_feedback = ?, recommendations = ?, feedback_source = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            student_name,
            class_name,
            topic,
            rubric,
            json.dumps(keywords),
            extracted_text,
            analysis["score"],
            analysis["understanding"],
            json.dumps(analysis["matched"]),
            json.dumps(analysis["missing"]),
            json.dumps(analysis["breakdown"]),
            analysis["feedback"],
            json.dumps(analysis["recommendations"]),
            analysis["feedback_source"],
            scan_id,
            session["user_id"],
        ),
    )
    db.commit()
    flash("Student record updated.", "success")
    return redirect(url_for("scan_detail", scan_id=scan_id))


@app.route("/scan/<int:scan_id>/refresh-feedback", methods=["POST"])
@login_required
def refresh_feedback(scan_id: int):
    db = get_db()
    scan = db.execute(
        "SELECT * FROM scans WHERE id = ? AND user_id = ?",
        (scan_id, session["user_id"]),
    ).fetchone()
    if not scan:
        flash("Scan not found.", "error")
        return redirect(url_for("upload"))

    keywords = json.loads(scan["keywords"])
    analysis = analyze_response(scan["student_name"], scan["extracted_text"], keywords, scan["rubric"] or "")
    analysis = enrich_analysis_locally(analysis)
    db.execute(
        """
        UPDATE scans
        SET score = ?, understanding_level = ?, matched_keywords = ?, missing_keywords = ?,
            keyword_breakdown = ?, detailed_feedback = ?, recommendations = ?, feedback_source = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            analysis["score"],
            analysis["understanding"],
            json.dumps(analysis["matched"]),
            json.dumps(analysis["missing"]),
            json.dumps(analysis["breakdown"]),
            analysis["feedback"],
            json.dumps(analysis["recommendations"]),
            analysis["feedback_source"],
            scan_id,
            session["user_id"],
        ),
    )
    db.commit()
    flash("Feedback refreshed using the free local scoring engine.", "success")
    return redirect(url_for("scan_detail", scan_id=scan_id))


@app.route("/scan/<int:scan_id>/reprocess-ocr", methods=["POST"])
@login_required
def reprocess_ocr(scan_id: int):
    db = get_db()
    scan = db.execute(
        "SELECT * FROM scans WHERE id = ? AND user_id = ?",
        (scan_id, session["user_id"]),
    ).fetchone()
    if not scan:
        flash("Scan not found.", "error")
        return redirect(url_for("upload"))

    if not scan["file_path"]:
        flash("This scan does not have an uploaded file to reprocess.", "error")
        return redirect(url_for("scan_detail", scan_id=scan_id))

    file_path = UPLOAD_FOLDER / scan["file_path"]
    if not file_path.exists():
        flash("The uploaded image file could not be found.", "error")
        return redirect(url_for("scan_detail", scan_id=scan_id))

    keywords = json.loads(scan["keywords"])
    extracted_text, ocr_status = extract_text(file_path, "", keywords)
    analysis = analyze_response(scan["student_name"], extracted_text, keywords, scan["rubric"] or "")
    analysis = enrich_analysis_locally(analysis)

    db.execute(
        """
        UPDATE scans
        SET extracted_text = ?, ocr_status = ?, score = ?, understanding_level = ?,
            matched_keywords = ?, missing_keywords = ?, keyword_breakdown = ?,
            detailed_feedback = ?, recommendations = ?, feedback_source = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            extracted_text,
            ocr_status,
            analysis["score"],
            analysis["understanding"],
            json.dumps(analysis["matched"]),
            json.dumps(analysis["missing"]),
            json.dumps(analysis["breakdown"]),
            analysis["feedback"],
            json.dumps(analysis["recommendations"]),
            analysis["feedback_source"],
            scan_id,
            session["user_id"],
        ),
    )
    db.commit()
    flash("OCR reprocessed with the enhanced handwriting pipeline.", "success")
    return redirect(url_for("scan_detail", scan_id=scan_id))


@app.route("/scan/<int:scan_id>/delete", methods=["POST"])
@login_required
def delete_scan(scan_id: int):
    db = get_db()
    scan = db.execute(
        "SELECT * FROM scans WHERE id = ? AND user_id = ?",
        (scan_id, session["user_id"]),
    ).fetchone()
    if scan:
        file_path = scan["file_path"]
        if file_path:
            stored_file = UPLOAD_FOLDER / file_path
            if stored_file.exists():
                stored_file.unlink()
        db.execute("DELETE FROM scans WHERE id = ? AND user_id = ?", (scan_id, session["user_id"]))
        db.commit()
        flash("Student scan deleted.", "success")
    next_url = request.form.get("next")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("upload"))


@app.route("/dashboard")
@login_required
def dashboard():
    scans = fetch_scans()
    metrics = dashboard_context(scans)
    return render_template("dashboard.html", scans=scans, metrics=metrics)


@app.route("/analytics")
@login_required
def analytics():
    scans = fetch_scans()
    metrics = dashboard_context(scans)
    return render_template("analytics.html", scans=scans, metrics=metrics)


@app.route("/gaps")
@login_required
def gaps():
    scans = fetch_scans()
    metrics = dashboard_context(scans)
    action_plan = []
    for topic, score in metrics["weak_topics"]:
        if score < 55:
            action_plan.append(f"Reteach {topic} with a visual example and a short guided discussion.")
        else:
            action_plan.append(f"Check {topic} with one more formative question next lesson.")
    return render_template("gaps.html", metrics=metrics, action_plan=action_plan)


@app.route("/feedback")
@login_required
def feedback():
    scans = fetch_scans()
    return render_template("feedback.html", scans=scans)


@app.route("/reports")
@login_required
def reports():
    scans = fetch_scans()
    metrics = dashboard_context(scans)
    summary = {
        "class_name": scans[0]["class_name"] if scans else "No class yet",
        "date": datetime.now().strftime("%d %b %Y"),
        "report_id": f"RPT-{datetime.now().strftime('%Y%m%d')}-{session['user_id']}",
    }
    return render_template("reports.html", scans=scans, metrics=metrics, summary=summary)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
else:
    init_db()



