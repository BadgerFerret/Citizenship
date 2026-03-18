import json
import os
import random
import sqlite3
from datetime import datetime, timedelta

import anthropic
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from config import Config

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(Config.DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            avatar     TEXT DEFAULT '📚',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id                    INTEGER PRIMARY KEY,
            student_id            INTEGER REFERENCES students(id),
            started_at            TEXT DEFAULT (datetime('now')),
            ended_at              TEXT,
            topic_filter          TEXT,
            quiz_mode             TEXT DEFAULT 'classic',
            num_questions         INTEGER DEFAULT 0,
            total_marks_available INTEGER DEFAULT 0,
            total_marks_awarded   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id               INTEGER PRIMARY KEY,
            session_id       INTEGER REFERENCES sessions(id),
            student_id       INTEGER REFERENCES students(id),
            question_id      TEXT NOT NULL,
            topic            TEXT NOT NULL,
            subtopic         TEXT,
            question_text    TEXT NOT NULL,
            marks_available  INTEGER NOT NULL,
            marks_awarded    INTEGER NOT NULL,
            student_answer   TEXT NOT NULL,
            feedback_json    TEXT NOT NULL,
            answered_at      TEXT DEFAULT (datetime('now'))
        );
    """)
    db.commit()
    # Migration: add quiz_mode column to existing databases
    try:
        db.execute("ALTER TABLE sessions ADD COLUMN quiz_mode TEXT DEFAULT 'classic'")
        db.commit()
    except Exception:
        pass  # Column already exists
    db.close()


# ---------------------------------------------------------------------------
# Question bank helpers
# ---------------------------------------------------------------------------

def load_questions():
    if not os.path.exists(Config.QUESTIONS_PATH):
        return []
    with open(Config.QUESTIONS_PATH) as f:
        data = json.load(f)
    return [q for q in data.get("questions", []) if q.get("active", True)]


def save_questions(questions_data):
    os.makedirs(os.path.dirname(Config.QUESTIONS_PATH), exist_ok=True)
    with open(Config.QUESTIONS_PATH, "w") as f:
        json.dump(questions_data, f, indent=2)


def get_all_questions_raw():
    if not os.path.exists(Config.QUESTIONS_PATH):
        return {"version": "1.0", "questions": []}
    with open(Config.QUESTIONS_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Progress / weakness helpers
# ---------------------------------------------------------------------------

def get_weakness_by_topic(student_id, last_n=30):
    db = get_db()
    rows = db.execute("""
        SELECT topic, marks_available, marks_awarded
        FROM attempts
        WHERE student_id = ?
        ORDER BY answered_at DESC
        LIMIT ?
    """, (student_id, last_n)).fetchall()
    db.close()

    by_topic = {}
    for row in rows:
        t = row["topic"]
        if t not in by_topic:
            by_topic[t] = {"available": 0, "awarded": 0, "count": 0}
        by_topic[t]["available"] += row["marks_available"]
        by_topic[t]["awarded"] += row["marks_awarded"]
        by_topic[t]["count"] += 1

    result = {}
    for t, v in by_topic.items():
        result[t] = round(v["awarded"] / v["available"] * 100) if v["available"] else 0

    # Fill in topics with no attempts as None
    for t in Config.TOPICS:
        if t not in result:
            result[t] = None

    return result


def get_student_summary(student_id):
    db = get_db()
    total = db.execute(
        "SELECT COUNT(*) as c FROM attempts WHERE student_id=?", (student_id,)
    ).fetchone()["c"]
    recent = db.execute("""
        SELECT SUM(marks_available) as ma, SUM(marks_awarded) as aw
        FROM attempts WHERE student_id=? ORDER BY answered_at DESC LIMIT 20
    """, (student_id,)).fetchone()
    last_active = db.execute(
        "SELECT MAX(answered_at) as la FROM attempts WHERE student_id=?", (student_id,)
    ).fetchone()["la"]
    db.close()

    ma = recent["ma"] or 0
    aw = recent["aw"] or 0
    avg = round(aw / ma * 100) if ma else 0
    return {"total_attempts": total, "avg_pct": avg, "last_active": last_active}


def select_next_question(student_id, session_id, topic_filter=None, quiz_mode='classic'):
    questions = load_questions()
    if not questions:
        return None

    if quiz_mode == 'multi_choice':
        questions = [q for q in questions if q.get('question_type', '') in ('multiple_choice', 'multiple_choice_multi')]
        if not questions:
            return None

    db = get_db()
    # Questions already answered this session
    done_this_session = set(
        r["question_id"] for r in db.execute(
            "SELECT question_id FROM attempts WHERE session_id=?", (session_id,)
        ).fetchall()
    )

    # Questions aced recently (avg >= 80% over last 3 attempts)
    aced = set()
    for q in questions:
        rows = db.execute("""
            SELECT marks_available, marks_awarded FROM attempts
            WHERE student_id=? AND question_id=?
            ORDER BY answered_at DESC LIMIT 3
        """, (student_id, q["id"])).fetchall()
        if len(rows) >= 2:
            ma = sum(r["marks_available"] for r in rows)
            aw = sum(r["marks_awarded"] for r in rows)
            if ma and aw / ma >= 0.8:
                aced.add(q["id"])

    # Questions seen in last 7 days
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    seen_recently = set(
        r["question_id"] for r in db.execute("""
            SELECT DISTINCT question_id FROM attempts
            WHERE student_id=? AND answered_at > ?
        """, (student_id, week_ago)).fetchall()
    )
    db.close()

    # Filter candidates
    candidates = [
        q for q in questions
        if q["id"] not in done_this_session
    ]
    if not candidates:
        return None

    if topic_filter and topic_filter != "mixed":
        candidates = [q for q in candidates if q["topic"] == topic_filter]
        if not candidates:
            return None

    # Prefer not recently seen and not aced
    preferred = [q for q in candidates if q["id"] not in seen_recently and q["id"] not in aced]
    fallback = [q for q in candidates if q not in preferred]

    pool = preferred if preferred else fallback

    # Weighted selection: 60% weakest topic, 40% random
    weakness = get_weakness_by_topic(student_id)
    if topic_filter and topic_filter != "mixed":
        return random.choice(pool)

    topics_with_scores = {
        t: (s if s is not None else -1)
        for t, s in weakness.items()
    }
    weakest_topics = sorted(topics_with_scores, key=lambda t: topics_with_scores[t])[:2]

    weak_pool = [q for q in pool if q["topic"] in weakest_topics]
    rand_pool = pool

    if weak_pool and random.random() < 0.6:
        return random.choice(weak_pool)
    return random.choice(rand_pool)


# ---------------------------------------------------------------------------
# Claude evaluation
# ---------------------------------------------------------------------------

def auto_mark_mcq(question, selected_letters):
    """Instantly mark a multiple-choice question when the correct answer is known."""
    correct = question['mark_scheme'].get('correct_answer', '').upper()
    marks = question['marks']
    options = {o['letter']: o['text'] for o in question.get('options', [])}

    if question.get('question_type') == 'multiple_choice_multi':
        # correct_answer not reliably available for multi; fall through to Claude
        return None

    if not correct:
        return None  # no stored answer — caller will use Claude

    selected = selected_letters[0] if selected_letters else ''
    correct_text = options.get(correct, '')
    selected_text = options.get(selected, selected)
    is_correct = selected.upper() == correct

    return {
        "marks_awarded": marks if is_correct else 0,
        "percentage": 100 if is_correct else 0,
        "points_credited": [f"{correct}) {correct_text}"] if is_correct else [],
        "points_missed": [] if is_correct else [f"Correct answer: {correct}) {correct_text}"],
        "what_was_good": f"Correct! The answer is {correct}) {correct_text}." if is_correct else "",
        "how_to_improve": "" if is_correct else f"The correct answer was {correct}) {correct_text}.",
        "examiner_tip": "Read all options carefully before selecting — eliminate obviously wrong answers first.",
        "suggested_answer": f"{correct}) {correct_text}",
        "is_mcq": True,
        "correct_letter": correct,
        "selected_letter": selected,
    }


def evaluate_mcq_with_claude(question, selected_letters):
    """Ask Claude to mark an MCQ when the correct answer isn't in the mark scheme."""
    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    marks = question['marks']
    options = question.get('options', [])
    is_multi = question.get('question_type') == 'multiple_choice_multi'

    options_text = "\n".join(f"  {o['letter']}) {o['text']}" for o in options)
    selected_display = ", ".join(selected_letters)

    user_prompt = f"""## Multiple-Choice Question
{question['question_text']}

## Options
{options_text}

## Student's Selection
{selected_display}

This question is worth {marks} mark{'s' if marks != 1 else ''}.
{"The student must select the correct two options." if is_multi else "The student must select the single correct option."}

## Your Task
Determine whether the student's selection is correct based on GCSE Citizenship knowledge.
Respond with ONLY valid JSON (no markdown):
{{
  "marks_awarded": <integer 0 to {marks}>,
  "percentage": <integer 0-100>,
  "points_credited": [<correct options the student selected, as strings>],
  "points_missed": [<correct options the student missed, if any>],
  "what_was_good": "<empty string if wrong, or brief confirmation if right>",
  "how_to_improve": "<explanation of the correct answer and why>",
  "examiner_tip": "<1 sentence of exam technique advice>",
  "suggested_answer": "<letter and text of the correct answer(s)>",
  "is_mcq": true,
  "correct_letter": "<letter(s) of correct answer(s), comma-separated>",
  "selected_letter": "{selected_display}"
}}"""

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=Config.CLAUDE_MODEL,
                max_tokens=600,
                system=Config.EVALUATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            assert isinstance(data["marks_awarded"], int)
            assert 0 <= data["marks_awarded"] <= marks
            data["percentage"] = round(data["marks_awarded"] / marks * 100) if marks else 0
            data["is_mcq"] = True
            return data
        except Exception:
            if attempt == 2:
                return {
                    "marks_awarded": 0,
                    "percentage": 0,
                    "points_credited": [],
                    "points_missed": [],
                    "what_was_good": "",
                    "how_to_improve": "Unable to evaluate automatically. Ask your teacher to review.",
                    "examiner_tip": "",
                    "suggested_answer": "",
                    "is_mcq": True,
                    "correct_letter": "",
                    "selected_letter": selected_display,
                    "error": "evaluation_failed",
                }


def evaluate_answer(question, student_answer):
    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

    points_list = "\n".join(
        f"{i+1}. {p}"
        for i, p in enumerate(question["mark_scheme"]["indicative_points"])
    )
    guidance = question["mark_scheme"].get("marking_guidance", "")
    marks = question["marks"]

    user_prompt = f"""## Question
{question['question_text']}
This question is worth {marks} mark{'s' if marks != 1 else ''}.

## Mark Scheme — Indicative Content
Each numbered point below is a credit-worthy idea worth 1 mark. Award 1 mark for each point \
the student addresses, up to the maximum of {marks} marks total.

{points_list}

Marking guidance: {guidance}

## Student's Answer
{student_answer}

## Your Task
Mark this answer as a GCSE examiner would. Work through each indicative point and decide \
whether the student has addressed it. Apply these rules strictly:

1. ACCEPT equivalent meaning — if the student conveys the right idea in different words, AWARD the mark
2. ACCEPT implied understanding — if it is clear the student knows the concept even if not fully explicit, AWARD the mark
3. DO NOT penalise spelling, grammar, or informal phrasing — mark knowledge only
4. BE GENEROUS at boundaries — if you are unsure whether a point is addressed, AWARD the mark
5. AWARD partial credit — a student who addresses 2 out of 4 points should receive 2 marks
6. The answer does NOT need to be perfect or complete to earn marks — credit every correct point shown

Respond with ONLY valid JSON in this exact structure (no markdown, no commentary):
{{
  "marks_awarded": <integer 0 to {marks}>,
  "percentage": <integer 0-100>,
  "points_credited": [<plain-English description of each indicative point the student addressed>],
  "points_missed": [<plain-English description of each indicative point the student did not address>],
  "what_was_good": "<1-2 sentences praising specific strengths — be encouraging>",
  "how_to_improve": "<1-2 sentences of specific, actionable advice on what to add next time>",
  "examiner_tip": "<1 sentence of exam technique advice for this question type>",
  "suggested_answer": "<a model answer written as a student would write it, worth full marks>"
}}"""

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=Config.CLAUDE_MODEL,
                max_tokens=Config.MAX_TOKENS_EVALUATION,
                system=Config.EVALUATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            # Validate
            assert isinstance(data["marks_awarded"], int)
            assert 0 <= data["marks_awarded"] <= marks
            data["percentage"] = round(data["marks_awarded"] / marks * 100) if marks else 0
            return data
        except Exception:
            if attempt == 2:
                fallback_answer = (
                    question["mark_scheme"].get("exemplar_answer") or
                    " ".join(question["mark_scheme"].get("indicative_points", []))
                )
                return {
                    "marks_awarded": 0,
                    "percentage": 0,
                    "points_credited": [],
                    "points_missed": question["mark_scheme"]["indicative_points"],
                    "what_was_good": "Unable to evaluate automatically.",
                    "how_to_improve": "Please ask your teacher to review this answer.",
                    "examiner_tip": "",
                    "suggested_answer": fallback_answer,
                    "error": "evaluation_failed",
                }


# ---------------------------------------------------------------------------
# Routes — Home
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    db = get_db()
    students = db.execute("SELECT * FROM students ORDER BY name").fetchall()
    db.close()
    student_data = []
    for s in students:
        summary = get_student_summary(s["id"])
        weakness = get_weakness_by_topic(s["id"])
        scored = {t: v for t, v in weakness.items() if v is not None}
        weakest = min(scored, key=scored.get) if scored else None
        student_data.append({
            "id": s["id"],
            "name": s["name"],
            "avatar": s["avatar"],
            "summary": summary,
            "weakest_topic": weakest,
            "weakest_topic_label": Config.TOPICS[weakest]["label"] if weakest else None,
        })
    topics = Config.TOPICS
    return render_template("home.html", students=student_data, topics=topics)


@app.route("/add-student", methods=["POST"])
def add_student():
    name = request.form.get("name", "").strip()
    avatar = request.form.get("avatar", "📚")
    if name:
        db = get_db()
        db.execute("INSERT OR IGNORE INTO students (name, avatar) VALUES (?, ?)", (name, avatar))
        db.commit()
        db.close()
    return redirect(url_for("home"))


@app.route("/delete-student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    db = get_db()
    db.execute("DELETE FROM attempts WHERE student_id=?", (student_id,))
    db.execute("DELETE FROM sessions WHERE student_id=?", (student_id,))
    db.execute("DELETE FROM students WHERE id=?", (student_id,))
    db.commit()
    db.close()
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# Routes — Session
# ---------------------------------------------------------------------------

@app.route("/session/start", methods=["POST"])
def session_start():
    student_id = request.form.get("student_id", type=int)
    topic_filter = request.form.get("topic_filter", "mixed")
    num_questions = request.form.get("num_questions", 10, type=int)
    quiz_mode = request.form.get("quiz_mode", "classic")
    if quiz_mode not in ("classic", "multi_choice"):
        quiz_mode = "classic"

    if not student_id:
        return redirect(url_for("home"))

    # Map "weakest" to the actual weakest topic
    if topic_filter == "weakest":
        weakness = get_weakness_by_topic(student_id)
        scored = {t: v for t, v in weakness.items() if v is not None}
        topic_filter = min(scored, key=scored.get) if scored else "mixed"

    db = get_db()
    cur = db.execute(
        "INSERT INTO sessions (student_id, topic_filter, quiz_mode) VALUES (?, ?, ?)",
        (student_id, topic_filter, quiz_mode)
    )
    sess_id = cur.lastrowid
    db.commit()
    db.close()

    session["current_session"] = {
        "session_id": sess_id,
        "student_id": student_id,
        "num_questions": num_questions,
        "answered": 0,
        "quiz_mode": quiz_mode,
    }
    return redirect(url_for("quiz", session_id=sess_id))


@app.route("/quiz/<int:session_id>")
def quiz(session_id):
    db = get_db()
    sess = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    student = db.execute("SELECT * FROM students WHERE id=?", (sess["student_id"],)).fetchone()
    db.close()

    sess_info = session.get("current_session", {})
    num_questions = sess_info.get("num_questions", 10)
    answered = sess_info.get("answered", 0)
    quiz_mode = sess_info.get("quiz_mode", "classic")

    if answered >= num_questions:
        return redirect(url_for("session_end", session_id=session_id))

    question = select_next_question(sess["student_id"], session_id, sess["topic_filter"], quiz_mode)
    if not question:
        return redirect(url_for("session_end", session_id=session_id))

    topic_label = Config.TOPICS.get(question["topic"], {}).get("label", question["topic"])
    return render_template(
        "quiz.html",
        question=question,
        session_id=session_id,
        student=student,
        topic_label=topic_label,
        answered=answered,
        num_questions=num_questions,
    )


@app.route("/api/submit-answer", methods=["POST"])
def submit_answer():
    data = request.get_json()
    session_id = data.get("session_id")
    question_id = data.get("question_id")
    student_answer = data.get("answer", "").strip()

    if not student_answer:
        return jsonify({"error": "No answer provided"}), 400

    # Find question
    all_q = load_questions()
    question = next((q for q in all_q if q["id"] == question_id), None)
    if not question:
        return jsonify({"error": "Question not found"}), 404

    db = get_db()
    sess = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    db.close()

    q_type = question.get('question_type', '')
    if q_type in ('multiple_choice', 'multiple_choice_multi'):
        selected_letters = [l.strip().upper() for l in student_answer.split(',') if l.strip()]
        feedback = auto_mark_mcq(question, selected_letters)
        if feedback is None:
            feedback = evaluate_mcq_with_claude(question, selected_letters)
    else:
        feedback = evaluate_answer(question, student_answer)
    marks_awarded = feedback.get("marks_awarded", 0)

    db = get_db()
    cur = db.execute("""
        INSERT INTO attempts
            (session_id, student_id, question_id, topic, subtopic,
             question_text, marks_available, marks_awarded, student_answer, feedback_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        sess["student_id"],
        question["id"],
        question["topic"],
        question.get("subtopic"),
        question["question_text"],
        question["marks"],
        marks_awarded,
        student_answer,
        json.dumps(feedback),
    ))
    attempt_id = cur.lastrowid

    # Update session totals
    db.execute("""
        UPDATE sessions SET
            num_questions = num_questions + 1,
            total_marks_available = total_marks_available + ?,
            total_marks_awarded = total_marks_awarded + ?
        WHERE id = ?
    """, (question["marks"], marks_awarded, session_id))
    db.commit()
    db.close()

    # Update answered count in flask session
    sess_info = session.get("current_session", {})
    sess_info["answered"] = sess_info.get("answered", 0) + 1
    session["current_session"] = sess_info

    return jsonify({"attempt_id": attempt_id, "feedback": feedback})


@app.route("/result/<int:attempt_id>")
def result(attempt_id):
    db = get_db()
    attempt = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    session_row = db.execute("SELECT * FROM sessions WHERE id=?", (attempt["session_id"],)).fetchone()
    student = db.execute("SELECT * FROM students WHERE id=?", (attempt["student_id"],)).fetchone()
    db.close()

    feedback = json.loads(attempt["feedback_json"])
    topic_label = Config.TOPICS.get(attempt["topic"], {}).get("label", attempt["topic"])
    sess_info = session.get("current_session", {})
    answered = sess_info.get("answered", 0)
    num_questions = sess_info.get("num_questions", 10)
    is_last = answered >= num_questions

    return render_template(
        "result.html",
        attempt=attempt,
        feedback=feedback,
        student=student,
        topic_label=topic_label,
        session_id=attempt["session_id"],
        is_last=is_last,
    )


@app.route("/session/<int:session_id>/end", methods=["GET", "POST"])
def session_end(session_id):
    db = get_db()
    db.execute("UPDATE sessions SET ended_at=datetime('now') WHERE id=?", (session_id,))
    db.commit()
    sess = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    student = db.execute("SELECT * FROM students WHERE id=?", (sess["student_id"],)).fetchone()

    # Topic breakdown for this session
    rows = db.execute("""
        SELECT topic, SUM(marks_available) as ma, SUM(marks_awarded) as aw, COUNT(*) as cnt
        FROM attempts WHERE session_id=?
        GROUP BY topic
    """, (session_id,)).fetchall()
    db.close()

    topic_breakdown = []
    for row in rows:
        pct = round(row["aw"] / row["ma"] * 100) if row["ma"] else 0
        topic_breakdown.append({
            "topic": row["topic"],
            "label": Config.TOPICS.get(row["topic"], {}).get("label", row["topic"]),
            "pct": pct,
            "awarded": row["aw"],
            "available": row["ma"],
            "count": row["cnt"],
            "color": Config.TOPIC_COLORS.get(row["topic"], "#aaa"),
        })

    overall_pct = 0
    if sess["total_marks_available"]:
        overall_pct = round(sess["total_marks_awarded"] / sess["total_marks_available"] * 100)

    # Find weakest topic in this session
    weakest = min(topic_breakdown, key=lambda x: x["pct"]) if topic_breakdown else None

    return render_template(
        "session_end.html",
        sess=sess,
        student=student,
        topic_breakdown=json.dumps(topic_breakdown),
        topic_breakdown_list=topic_breakdown,
        overall_pct=overall_pct,
        weakest=weakest,
    )


# ---------------------------------------------------------------------------
# Routes — Progress
# ---------------------------------------------------------------------------

@app.route("/progress/<int:student_id>")
def progress(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    all_students = db.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    db.close()
    return render_template("progress.html", student=student, all_students=all_students,
                           topics=Config.TOPICS, topic_colors=Config.TOPIC_COLORS)


@app.route("/api/progress/<int:student_id>")
def api_progress(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()

    # Overall summary
    total = db.execute(
        "SELECT COUNT(*) as c FROM attempts WHERE student_id=?", (student_id,)
    ).fetchone()["c"]
    overall = db.execute(
        "SELECT SUM(marks_available) as ma, SUM(marks_awarded) as aw FROM attempts WHERE student_id=?",
        (student_id,)
    ).fetchone()
    overall_pct = round(overall["aw"] / overall["ma"] * 100) if overall["ma"] else 0

    # By topic (all time)
    topic_rows = db.execute("""
        SELECT topic, SUM(marks_available) as ma, SUM(marks_awarded) as aw, COUNT(*) as cnt
        FROM attempts WHERE student_id=?
        GROUP BY topic
    """, (student_id,)).fetchall()
    by_topic = {}
    for row in topic_rows:
        pct = round(row["aw"] / row["ma"] * 100) if row["ma"] else 0
        by_topic[row["topic"]] = {
            "avg_pct": pct,
            "attempts": row["cnt"],
            "label": Config.TOPICS.get(row["topic"], {}).get("label", row["topic"]),
            "color": Config.TOPIC_COLORS.get(row["topic"], "#aaa"),
        }

    # Fill missing topics
    for t, info in Config.TOPICS.items():
        if t not in by_topic:
            by_topic[t] = {"avg_pct": None, "attempts": 0, "label": info["label"],
                           "color": Config.TOPIC_COLORS.get(t, "#aaa")}

    # Timeline (last 50 attempts)
    timeline_rows = db.execute("""
        SELECT answered_at, marks_available, marks_awarded, topic, question_id
        FROM attempts WHERE student_id=?
        ORDER BY answered_at ASC
    """, (student_id,)).fetchall()
    timeline = []
    for row in timeline_rows:
        pct = round(row["marks_awarded"] / row["marks_available"] * 100) if row["marks_available"] else 0
        timeline.append({
            "date": row["answered_at"][:10],
            "pct": pct,
            "topic": row["topic"],
            "color": Config.TOPIC_COLORS.get(row["topic"], "#aaa"),
        })

    # Recent sessions
    session_rows = db.execute("""
        SELECT id, started_at, ended_at, topic_filter,
               num_questions, total_marks_available, total_marks_awarded
        FROM sessions WHERE student_id=? AND ended_at IS NOT NULL
        ORDER BY started_at DESC LIMIT 10
    """, (student_id,)).fetchall()
    recent_sessions = []
    for row in session_rows:
        pct = round(row["total_marks_awarded"] / row["total_marks_available"] * 100) if row["total_marks_available"] else 0
        recent_sessions.append({
            "id": row["id"],
            "date": row["started_at"][:10] if row["started_at"] else "",
            "topic": Config.TOPICS.get(row["topic_filter"], {}).get("label", "Mixed") if row["topic_filter"] and row["topic_filter"] != "mixed" else "Mixed",
            "questions": row["num_questions"],
            "pct": pct,
        })

    # Streak (consecutive days with at least one attempt)
    dates = db.execute("""
        SELECT DISTINCT DATE(answered_at) as d FROM attempts
        WHERE student_id=? ORDER BY d DESC
    """, (student_id,)).fetchall()
    db.close()

    streak = 0
    today = datetime.utcnow().date()
    for i, row in enumerate(dates):
        d = datetime.strptime(row["d"], "%Y-%m-%d").date()
        expected = today - timedelta(days=i)
        if d == expected:
            streak += 1
        else:
            break

    return jsonify({
        "student": {"id": student_id, "name": student["name"]},
        "summary": {
            "total_attempts": total,
            "overall_avg_pct": overall_pct,
            "streak_days": streak,
        },
        "by_topic": by_topic,
        "timeline": timeline,
        "recent_sessions": recent_sessions,
    })


# ---------------------------------------------------------------------------
# Routes — Admin
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin():
    return render_template("admin.html", topics=Config.TOPICS)


@app.route("/api/questions")
def api_questions():
    topic = request.args.get("topic")
    active_only = request.args.get("active") != "false"
    data = get_all_questions_raw()
    questions = data.get("questions", [])
    if topic:
        questions = [q for q in questions if q.get("topic") == topic]
    if active_only:
        questions = [q for q in questions if q.get("active", True)]
    return jsonify({"questions": questions, "total": len(questions)})


@app.route("/api/questions/<question_id>/toggle", methods=["POST"])
def toggle_question(question_id):
    data = get_all_questions_raw()
    for q in data["questions"]:
        if q["id"] == question_id:
            q["active"] = not q.get("active", True)
            break
    save_questions(data)
    return jsonify({"ok": True})




# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    debug = not os.environ.get("PORT")  # debug off when deployed
    print("GCSE Citizenship Revision App")
    print(f"Open http://localhost:{port} in your browser")
    app.run(debug=debug, host="0.0.0.0", port=port)
