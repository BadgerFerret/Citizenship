#!/usr/bin/env python3
"""
parse_papers_free.py — Ingest past papers without using the Anthropic API.

Uses PyMuPDF to extract text from PDFs and regex-based parsing to extract
questions and mark scheme answers, then writes to data/questions.json.

Usage:
    python scripts/parse_papers_free.py            # Process all papers
    python scripts/parse_papers_free.py --dry-run  # Preview without saving
    python scripts/parse_papers_free.py --force    # Re-process already-ingested
"""

import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import Config

# ---------------------------------------------------------------------------
# Topic classification based on section headings and keywords
# ---------------------------------------------------------------------------

SECTION_TOPIC_MAP = {
    # Paper section headings → topic keys
    "living together": "life_in_modern_britain",
    "life in modern britain": "life_in_modern_britain",
    "democracy at work": "government_and_democracy",
    "democracy in the uk": "government_and_democracy",
    "government and democracy": "government_and_democracy",
    "rights and responsibilities": "rights_and_responsibilities",
    "uk and the wider world": "uk_and_wider_world",
    "wider world": "uk_and_wider_world",
    "global citizenship": "uk_and_wider_world",
    "taking citizenship action": "active_citizenship",
    "active citizenship": "active_citizenship",
    "taking action": "active_citizenship",
}

KEYWORD_TOPIC_MAP = {
    "life_in_modern_britain": [
        "migration", "asylum", "refugee", "identity", "british values",
        "multicultur", "diversity", "tolerance", "respect", "community cohesion",
        "human rights", "equality", "discrimination", "magna carta",
        "religion", "culture", "nhs", "welfare",
    ],
    "government_and_democracy": [
        "parliament", "democracy", "election", "voting", "constituency",
        "first-past-the-post", "fptp", "proportional", "devolution",
        "house of commons", "house of lords", "prime minister", "cabinet",
        "government minister", "mp ", "mps", "political party", "parties",
        "scrutiny", "legislature", "executive", "judiciary", "rule of law",
        "constitution", "referendum", "local council", "councillor",
        "devolved", "scottish parliament", "welsh assembly", "mayor",
    ],
    "rights_and_responsibilities": [
        "rights", "responsibilities", "duty", "legal", "law", "court",
        "justice", "criminal", "civil", "police", "arrest", "trial",
        "consumer", "employment", "trade union", "protest", "freedom of speech",
        "human rights act", "european convention",
    ],
    "uk_and_wider_world": [
        "united nations", "nato", "european union", "eu ", "global",
        "international", "foreign policy", "trade", "aid", "development",
        "climate change", "environment", "conflict", "war", "peacekeeping",
        "commonwealth", "g7", "g20", "imf", "world bank",
    ],
    "active_citizenship": [
        "campaign", "petition", "protest", "lobby", "advocacy", "volunteer",
        "community action", "pressure group", "ngo", "charity",
        "social media", "media", "journalism", "citizen action",
        "taking action", "making a difference",
    ],
}


def classify_topic(question_text: str, section_hint: str = "") -> str:
    text_lower = (question_text + " " + section_hint).lower()

    # Check section heading first
    for key, topic in SECTION_TOPIC_MAP.items():
        if key in text_lower:
            return topic

    # Keyword scoring
    scores = {topic: 0 for topic in KEYWORD_TOPIC_MAP}
    for topic, keywords in KEYWORD_TOPIC_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                scores[topic] += 1

    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best

    return "life_in_modern_britain"  # default


def classify_question_type(question_text: str) -> str:
    text_lower = question_text.lower()
    if re.search(r'\bwhich\b.*\bone\b|\bidentify\b|\bstate\b|\bname\b|\bwhat is\b', text_lower):
        return "identify"
    if re.search(r'\bdefine\b|\bwhat do you mean\b|\bwhat is meant\b', text_lower):
        return "define"
    if re.search(r'\bdescribe\b|\boutline\b', text_lower):
        return "describe"
    if re.search(r'\bexplain\b|\bsuggest\b|\bgive reasons?\b|\bwhy\b', text_lower):
        return "explain"
    if re.search(r'\banalyse\b|\banalyze\b|\bexamine\b|\bdiscuss\b', text_lower):
        return "analyse"
    if re.search(r'\bevaluate\b|\bjustify\b|\bassess\b|\bto what extent\b', text_lower):
        return "evaluate"
    return "explain"


def classify_difficulty(marks: int) -> str:
    if marks <= 2:
        return "easy"
    if marks <= 4:
        return "medium"
    return "hard"


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def extract_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    return "\n===PAGE===\n".join(pages)


NOISE_PATTERNS = [
    r"\*P\w+\*",                          # page codes like *P72634A0124*
    r"DO NOT WRITE IN THIS AREA",
    r"Turn over\s*",
    r"P\d{5}[A-Z]\d*",                    # standalone page ref
    r"©\d{4} Pearson Education Ltd\.",
    r"N:\d+/\d+/\d+",
    r"===PAGE===",
    r"[\.]{10,}",                          # dotted answer lines
    r"[_]{5,}",                            # underscore lines
    r"\d{1,2}\s+\.\.\.\s*\n",             # numbered answer lines "1 ... "
]


def clean_text(text: str) -> str:
    for pat in NOISE_PATTERNS:
        text = re.sub(pat, "", text, flags=re.MULTILINE)
    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Question paper parser — linear state-machine approach
# ---------------------------------------------------------------------------

MARKS_RE = re.compile(r"\((\d+)\)")
SECTION_RE = re.compile(
    r"SECTION\s+[A-Z][\s\n]+(.*?)(?:\n|$)",
    re.IGNORECASE,
)

# Matches start of a main question: "1 " or "1\t" at start of line (1-2 digit number)
MAIN_Q_RE = re.compile(r"^(\d{1,2})\s{1,6}(?!\d)", re.MULTILINE)
# Matches a sub-question letter: "(a)" "(b)" etc. at start of line (possibly indented)
SUB_Q_RE = re.compile(r"^\s{0,8}\(([a-z])\)\s+", re.MULTILINE)
# Marks on their own line or inline: (1) (2) (4) etc.
MARKS_LINE_RE = re.compile(r"^\s*\((\d{1,2})\)\s*$", re.MULTILINE)


def parse_questions_from_paper(text: str) -> list[dict]:
    """
    Linear state-machine parser. Tracks current main question number and
    sub-question letter as it scans line by line.
    Returns list of dicts: {ref, question_text, marks, section_hint, options}
    """
    text = clean_text(text)
    lines = text.split("\n")

    questions = []
    current_section = ""
    current_main = None       # e.g. "3"
    current_sub = None        # e.g. "a"
    current_text_lines = []
    current_intro = ""        # intro text before first sub-q (e.g. "Study Source A...")

    def flush_current():
        nonlocal current_text_lines, current_intro
        if current_main is None:
            current_text_lines = []
            return
        ref = f"{current_main}({current_sub})" if current_sub else current_main
        raw = "\n".join(current_text_lines).strip()

        # Strip answer lines
        raw = clean_answer_lines(raw)

        # Extract marks
        marks = 1
        m = MARKS_LINE_RE.search("\n".join(current_text_lines[:4]))
        if m:
            marks = int(m.group(1))
        else:
            m = MARKS_RE.search(raw[:80])
            if m:
                marks = int(m.group(1))

        # Strip (N) mark from text
        q_text = re.sub(r"^\s*\(\d+\)\s*\n?", "", raw)
        q_text = re.sub(r"\n\s*\(\d+\)\s*$", "", q_text).strip()

        # Prepend intro text for source-based questions
        if current_intro and current_sub:
            q_text = (current_intro.strip() + "\n\n" + q_text).strip()

        options = extract_mcq_options(q_text)
        if options:
            q_text = strip_mcq_options(q_text)

        if q_text and len(q_text) > 5:
            questions.append({
                "ref": ref,
                "question_text": q_text.strip(),
                "marks": marks,
                "section_hint": current_section,
                "options": options,
            })
        current_text_lines = []

    for line in lines:
        stripped = line.strip()

        # Check for section heading
        sec_m = SECTION_RE.match(line)
        if sec_m:
            current_section = sec_m.group(1).strip()
            continue

        # Check for "Total for Question N" — marks end of question group
        if re.match(r"\(Total for Question", stripped, re.IGNORECASE):
            flush_current()
            current_sub = None
            current_intro = ""
            continue

        # Check for new main question number at start of line
        main_m = MAIN_Q_RE.match(line)
        if main_m:
            num = main_m.group(1)
            # Avoid matching things like "10 marks" or a lone number
            rest = line[main_m.end():].strip()
            # If rest starts with a sub-letter, this is "1 (a) ..."
            sub_inline = re.match(r"\(([a-z])\)\s+(.*)", rest)
            if sub_inline:
                flush_current()
                current_main = num
                current_sub = sub_inline.group(1)
                current_intro = ""
                current_text_lines = [sub_inline.group(2)]
            elif rest and not re.match(r"^\d", rest):
                # Standalone question or intro line
                flush_current()
                current_main = num
                current_sub = None
                current_intro = ""
                current_text_lines = [rest] if rest else []
            else:
                # Probably a page number or noise
                current_text_lines.append(line)
            continue

        # Check for sub-question on its own line: "  (b) Question text"
        sub_m = SUB_Q_RE.match(line)
        if sub_m and current_main:
            flush_current()
            # Save intro text from previous standalone main-q block
            if current_sub is None and current_text_lines:
                current_intro = "\n".join(current_text_lines).strip()
                current_intro = clean_answer_lines(current_intro)
            current_sub = sub_m.group(1)
            rest = line[sub_m.end():]
            current_text_lines = [rest] if rest.strip() else []
            continue

        # Otherwise accumulate into current question
        current_text_lines.append(line)

    flush_current()

    # Filter out noise and instruction-only entries
    def _is_real_question(q: dict) -> bool:
        t = q["question_text"].strip()
        if not t or len(t) < 12:
            return False
        if q["marks"] > 25:
            return False
        noise_patterns = [
            r"TOTAL FOR (SECTION|PAPER)",
            r"^SECTION [A-Z]",
            r"BLANK PAGE",
            r"copyright holders",
            r"^hour \d",
            r"^Paper\s*\nreference",
            r"Question \d+\s*\nThis .{0,30}question has been removed",
            r"^Source Booklet",
        ]
        for pat in noise_patterns:
            if re.search(pat, t, re.IGNORECASE | re.MULTILINE):
                return False
        # Instruction-only intros: "Study Source X ... before you answer" or
        # "Study Source X ... answer the question(s) that follow"
        if re.search(r"Study (?:Source|the [Ss]ource)", t, re.IGNORECASE):
            if not re.search(r"\b(explain|describe|identify|analyse|evaluate|suggest|compare|state|name|which one|give)\b", t, re.IGNORECASE):
                return False
        # Active citizenship scenario intro (Paper 2 Q1 preamble)
        if re.search(r"You have been part of a group that organised", t, re.IGNORECASE):
            return False
        return True

    questions = [q for q in questions if _is_real_question(q)]

    # Deduplicate by ref (keep last occurrence which tends to be cleaner)
    seen = {}
    for q in questions:
        seen[q["ref"]] = q
    return list(seen.values())


def clean_answer_lines(text: str) -> str:
    """Remove answer space lines and noise from question text."""
    lines = text.split("\n")
    clean = []
    for line in lines:
        stripped = line.strip()
        # Skip blank answer lines (dots, underscores)
        if re.match(r"^[.\s_\-]{5,}$", stripped):
            continue
        # Skip standalone single digits (answer space numbering)
        if re.match(r"^\d\s*$", stripped):
            continue
        clean.append(line)
    return "\n".join(clean)


def clean_answer_lines(text: str) -> str:
    """Remove answer space lines and noise from question text."""
    lines = text.split("\n")
    clean = []
    for line in lines:
        stripped = line.strip()
        # Skip blank answer lines
        if re.match(r"^[.\s_]{5,}$", stripped):
            continue
        # Skip standalone numbers that are answer-space labels
        if re.match(r"^\d+\s*$", stripped):
            continue
        clean.append(line)
    return "\n".join(clean)


MCQ_OPTION_RE = re.compile(
    r"(?m)^\s*([A-F])\s{1,4}(.+?)(?=^\s*[A-F]\s{1,4}|\Z)",
    re.DOTALL,
)


def extract_mcq_options(text: str) -> dict | None:
    options = {}
    for m in MCQ_OPTION_RE.finditer(text):
        letter = m.group(1)
        opt_text = m.group(2).strip().replace("\n", " ")
        options[letter] = opt_text
    return options if len(options) >= 2 else None


def strip_mcq_options(text: str) -> str:
    return MCQ_OPTION_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# Mark scheme parser
# ---------------------------------------------------------------------------

REF_RE = re.compile(r"^\d+(\s*\([a-z]\))?$")


def _normalise_ref(raw: str) -> str:
    """'1 (a)' → '1(a)',  '3 (c)' → '3(c)',  '2' → '2'"""
    return re.sub(r"\s+", "", raw.strip())


def _extract_ref_from_segment(seg: str) -> tuple[str | None, str]:
    """
    Try to extract the question ref from a mark scheme segment.
    Returns (ref, body_text) or (None, seg) if not found.

    Handles two layouts:
      Layout A: ref is first non-empty line  →  "1(a)\nB is correct..."
      Layout B: question text first, then "Mark\nref\nbody"
    """
    lines = [l for l in seg.split("\n") if l.strip()]
    if not lines:
        return None, seg

    # Layout A: first line is a ref
    candidate = _normalise_ref(lines[0])
    if REF_RE.match(candidate):
        body = "\n".join(lines[1:])
        return candidate, body

    # Layout B: look for "Mark\n<ref>\n..." pattern
    mark_pos = re.search(r"(?im)^Mark\s*$", seg)
    if mark_pos:
        after_mark = seg[mark_pos.end():].strip()
        after_lines = [l for l in after_mark.split("\n") if l.strip()]
        if after_lines:
            candidate = _normalise_ref(after_lines[0])
            if REF_RE.match(candidate):
                body = "\n".join(after_lines[1:])
                return candidate, body

    # Try any line that looks like a ref
    for line in lines[:6]:
        candidate = _normalise_ref(line)
        if REF_RE.match(candidate):
            idx = seg.index(line.strip())
            body = seg[idx + len(line.strip()):]
            return candidate, body

    return None, seg


def parse_mark_scheme(text: str) -> dict[str, dict]:
    """
    Returns dict keyed by normalised question ref (e.g. "1(a)", "2"),
    value is {"indicative_points": [...], "marking_guidance": str, "mark": int}
    """
    results = {}
    text = clean_text(text)

    segments = re.split(
        r"Question\s*\n?\s*number\s*\n?\s*(?:Ind?c?ative content|Answer\w*|[A-Z][^\n]{5,60})\s*\n?\s*(?:Marking instructions\s*\n)?\s*(?:Mark\s*)?",
        text,
        flags=re.IGNORECASE,
    )

    # Flatten all segments, then sub-split on embedded refs
    # e.g. "4(a)\n...(1)\n4(b)\n...(1)\n4(c)\n..."
    all_subsegments = []
    for seg in segments[1:]:
        # Sub-split on pattern: mark "(N)" followed by a new question ref on next line
        subsplit = re.split(
            r"(?m)\n\s*\(\d+\)\s*\n(?=\s*\d+\s*(?:\([a-z]\))?\s*\n)",
            seg,
        )
        if len(subsplit) > 1:
            # First sub-segment keeps its mark at end; rest get it prepended
            all_subsegments.append(subsplit[0])
            for part in subsplit[1:]:
                all_subsegments.append(part)
        else:
            all_subsegments.append(seg)

    for seg in all_subsegments:
        seg = seg.strip()
        if not seg:
            continue

        ref, body = _extract_ref_from_segment(seg)
        if not ref:
            continue

        # Extract mark from "(N)" at end
        mark_match = re.search(r"\((\d+)\)\s*$", body.strip())
        mark = int(mark_match.group(1)) if mark_match else 1

        # Extract bullet-point indicative points (• bullets, possibly split across lines)
        raw_bullets = re.split(r"\n?\s*•\s*\n?", body)
        bullets = []
        for b in raw_bullets[1:]:  # skip before first bullet
            b_clean = b.strip()
            # Stop at trailing marks line or accept line
            b_clean = re.sub(r"\n?Accept other valid.*", "", b_clean, flags=re.IGNORECASE)
            b_clean = re.sub(r"\n?\(\d+\)\s*$", "", b_clean).strip()
            b_clean = re.sub(r"\s+", " ", b_clean)
            if b_clean and len(b_clean) > 3:
                bullets.append(b_clean)

        # MCQ correct answer
        correct_match = re.search(
            r"([A-F])(?:\s+(?:and|&)\s+([A-F]))?\s+(?:is|are)\s+the correct answer",
            body, re.IGNORECASE,
        )
        if correct_match and not bullets:
            correct_letters = correct_match.group(1)
            if correct_match.group(2):
                correct_letters += f" and {correct_match.group(2)}"
            bullets = [f"Correct answer: {correct_letters}"]

        # Marking guidance: text before first bullet, stripped of ref/mark noise
        pre_bullet = body.split("•")[0] if "•" in body else body
        pre_bullet = re.sub(r"\(\d+\)\s*$", "", pre_bullet).strip()
        pre_bullet = re.sub(r"\n{2,}", "\n", pre_bullet).strip()

        results[ref] = {
            "indicative_points": bullets,
            "marking_guidance": pre_bullet[:600] if pre_bullet else "",
            "mark": mark,
        }

    return results


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def make_id(year: str, paper_num: str, ref: str) -> str:
    # e.g. q_2023j_01_1a, q_2023j_01_2
    session = "j"  # all are June series
    ref_clean = re.sub(r"[^a-z0-9]", "", ref.lower())
    return f"q_{year}{session}_{paper_num.zfill(2)}_{ref_clean}"


# ---------------------------------------------------------------------------
# Main ingestion
# ---------------------------------------------------------------------------

def ingest_paper(entry: dict) -> list[dict]:
    paper_label = entry["paper"]        # e.g. "June 2023"
    code = entry["code"]                # e.g. "1CS0/01"
    paper_pdf = entry["paper_pdf"]
    ms_pdf = entry["ms_pdf"]

    year = re.search(r"\d{4}", paper_label).group(0)
    paper_num = code.split("/")[-1]     # "01" or "02"

    print(f"  Extracting text...", flush=True)
    paper_text = extract_text(paper_pdf)
    ms_text = extract_text(ms_pdf)

    print(f"  Parsing questions...", flush=True)
    raw_questions = parse_questions_from_paper(paper_text)

    print(f"  Parsing mark scheme...", flush=True)
    ms_data = parse_mark_scheme(ms_text)

    print(f"  Found {len(raw_questions)} questions, {len(ms_data)} mark scheme entries", flush=True)

    questions = []
    for rq in raw_questions:
        ref = rq["ref"]
        # Fallback: if N(a) not in MS but N is (only sub-part), use parent
        ms = ms_data.get(ref) or ms_data.get(re.sub(r"\([a-z]\)$", "", ref), {})

        # Use mark from mark scheme if available (more reliable)
        marks = ms.get("mark") or rq["marks"]

        question_text = rq["question_text"]
        if not question_text:
            continue

        # Add MCQ options to question text if present
        if rq["options"]:
            opts_text = "\n".join(f"  {k}) {v}" for k, v in sorted(rq["options"].items()))
            question_text = question_text + "\n\n" + opts_text

        section_hint = rq["section_hint"]
        topic = classify_topic(question_text, section_hint)
        q_type = classify_question_type(question_text)
        difficulty = classify_difficulty(marks)

        # Subtopic: use section hint as a rough subtopic
        subtopic = re.sub(r"\s+", "_", section_hint.lower())[:40] if section_hint else "general"

        q = {
            "id": make_id(year, paper_num, ref),
            "source": {
                "paper": paper_label,
                "paper_code": code,
                "question_ref": ref,
                "exam_board": "Edexcel",
            },
            "topic": topic,
            "subtopic": subtopic,
            "question_text": question_text,
            "marks": marks,
            "question_type": q_type,
            "mark_scheme": {
                "indicative_points": ms.get("indicative_points", []),
                "marking_guidance": ms.get("marking_guidance", ""),
                "exemplar_answer": None,
            },
            "difficulty": difficulty,
            "active": True,
        }
        questions.append(q)

    return questions


def load_db() -> dict:
    path = Path("data/questions.json")
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"version": "1.0", "questions": []}


def save_db(db: dict) -> None:
    path = Path("data/questions.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(db, f, indent=2)


def already_ingested(db: dict) -> set:
    seen = set()
    for q in db.get("questions", []):
        src = q.get("source", {})
        seen.add(f"{src.get('paper')}|{src.get('paper_code')}")
    return seen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--paper", metavar="LABEL")
    parser.add_argument("--code", metavar="CODE")
    args = parser.parse_args()

    manifest_path = Path("papers/manifest.json")
    with open(manifest_path) as f:
        manifest = [e for e in json.load(f) if "paper" in e and "paper_pdf" in e]

    if args.paper:
        manifest = [e for e in manifest if e["paper"] == args.paper]
    if args.code:
        manifest = [e for e in manifest if e["code"] == args.code]

    db = load_db()
    existing_ids = {q["id"] for q in db["questions"]}
    ingested = already_ingested(db) if not args.force else set()

    total_added = 0
    total_skipped = 0

    for entry in manifest:
        paper = entry["paper"]
        code = entry["code"]
        key = f"{paper}|{code}"

        if key in ingested:
            print(f"Skipping  {paper} ({code}) — already ingested")
            total_skipped += 1
            continue

        missing = [p for p in [entry["paper_pdf"], entry["ms_pdf"]] if not Path(p).exists()]
        if missing:
            print(f"Skipping  {paper} ({code}) — PDF(s) not found: {missing}")
            total_skipped += 1
            continue

        print(f"\nProcessing {paper} ({code})")
        try:
            questions = ingest_paper(entry)
        except Exception as exc:
            print(f"  Error: {exc}")
            import traceback; traceback.print_exc()
            total_skipped += 1
            continue

        new_qs = [q for q in questions if q["id"] not in existing_ids]
        dupes = len(questions) - len(new_qs)

        if args.dry_run:
            print(f"  Would add {len(new_qs)} questions ({dupes} dupes skipped)")
            for q in new_qs[:3]:
                print(f"    [{q['id']}] {q['question_text'][:80]}...")
        else:
            db["questions"].extend(new_qs)
            existing_ids.update(q["id"] for q in new_qs)
            total_added += len(new_qs)
            print(f"  Added {len(new_qs)} questions ({dupes} dupes skipped)")

    print()
    if args.dry_run:
        print("Dry run complete — nothing saved.")
    else:
        if total_added > 0:
            save_db(db)
            print(f"Saved. Added {total_added} new questions.")
            print(f"Total in bank: {len(db['questions'])}")
            print()
            print("Next: git add data/questions.json && git commit -m 'Add ingested questions'")
        else:
            print("Nothing new to save.")
        if total_skipped:
            print(f"({total_skipped} paper(s) skipped)")


if __name__ == "__main__":
    main()
