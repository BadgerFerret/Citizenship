#!/usr/bin/env python3
"""
ingest_papers.py — Batch-ingest past papers into data/questions.json.

Reads papers/manifest.json for the list of papers to process. For each entry,
uploads the paper PDF and mark scheme to Claude, extracts all questions, and
saves them directly to data/questions.json. Already-ingested papers are skipped
automatically.

Usage:
    python scripts/ingest_papers.py            # Process all new papers in manifest
    python scripts/ingest_papers.py --dry-run  # Preview without saving
    python scripts/ingest_papers.py --force    # Re-process already-ingested papers

Workflow:
    1. Download PDFs for each past paper and mark scheme.
    2. Put them in the paths listed in papers/manifest.json, e.g.:
           papers/2023_june_01/paper.pdf
           papers/2023_june_01/ms.pdf
    3. Run:  python scripts/ingest_papers.py
    4. Commit data/questions.json — questions are then available in Codespaces
       and Railway without needing the PDFs.

Folder naming convention:
    papers/<year>_<session>_<component>/paper.pdf
    papers/<year>_<session>_<component>/ms.pdf

    Examples:
        papers/2023_june_01/   -> June 2023, Paper 1
        papers/2023_june_02/   -> June 2023, Paper 2
        papers/2022_june_01/   -> June 2022, Paper 1
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path

# Allow importing config.py from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
from config import Config


TOPICS = {
    "life_in_modern_britain",
    "rights_and_responsibilities",
    "government_and_democracy",
    "uk_and_wider_world",
    "active_citizenship",
}

EXTRACT_PROMPT = """You are extracting GCSE Citizenship questions and mark scheme points from past exam papers.

I am providing you with:
1. A scanned/PDF past paper (questions)
2. The corresponding mark scheme

Your task is to extract ALL questions from the paper (including sub-questions) and match each one
to its mark scheme. Output a JSON array where each element follows this exact schema:

{{
  "id": "<unique id: q_<year><session>_<question_ref>, e.g. q_2023j_01a>",
  "source": {{
    "paper": "{paper}",
    "paper_code": "{code}",
    "question_ref": "<e.g. 1(a)>",
    "exam_board": "Edexcel"
  }},
  "topic": "<one of: life_in_modern_britain | rights_and_responsibilities | government_and_democracy | uk_and_wider_world | active_citizenship>",
  "subtopic": "<specific subtopic, e.g. electoral_systems>",
  "question_text": "<full question text exactly as written>",
  "marks": <integer>,
  "question_type": "<one of: define | describe | explain | analyse | evaluate | identify>",
  "mark_scheme": {{
    "indicative_points": ["<point 1>", "<point 2>", ...],
    "marking_guidance": "<examiner's guidance on awarding marks>",
    "exemplar_answer": "<model answer if provided, else null>"
  }},
  "difficulty": "<easy | medium | hard>",
  "active": true
}}

Rules:
- Include ALL questions, including 1-mark questions.
- Set "topic" based on the subject matter of the question. Use your knowledge of the Edexcel 1CS0
  GCSE Citizenship specification to assign the most appropriate topic.
- For "difficulty": easy = 1-2 marks or recall; medium = 3-4 marks or explanation;
  hard = 5+ marks or evaluation/analysis.
- For "indicative_points": extract the bullet-point list from the mark scheme. Each bullet
  should be a standalone point worth 1 mark.
- Do NOT include essay/extended writing questions unless the mark scheme clearly shows
  individual mark-worthy points.
- Output ONLY valid JSON — no markdown, no commentary, no code fences.

Paper: {paper} | Code: {code}
"""


def pdf_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def extract_questions(paper_pdf: str, ms_pdf: str, paper: str, code: str) -> list:
    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

    paper_b64 = pdf_to_base64(paper_pdf)
    ms_b64 = pdf_to_base64(ms_pdf)

    prompt = EXTRACT_PROMPT.format(paper=paper, code=code)

    print(f"  Sending to Claude ({Config.CLAUDE_MODEL})...", flush=True)

    response = client.messages.create(
        model=Config.CLAUDE_MODEL,
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": paper_b64,
                        },
                        "title": f"Past paper: {paper} {code}",
                    },
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": ms_b64,
                        },
                        "title": f"Mark scheme: {paper} {code}",
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )

    text = response.content[0].text.strip()

    # Strip markdown code fences if Claude added them
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    questions = json.loads(text)

    # Validate and normalise
    valid = []
    for i, q in enumerate(questions):
        errors = []
        for field in ["id", "topic", "question_text", "marks", "mark_scheme"]:
            if not q.get(field):
                errors.append(f"missing '{field}'")
        if errors:
            print(f"  Skipping question {i + 1}: {', '.join(errors)}")
            continue
        if q.get("topic") not in TOPICS:
            print(
                f"  Warning: question {q.get('id', i + 1)} has unknown topic "
                f"'{q.get('topic')}' — defaulting to 'life_in_modern_britain'"
            )
            q["topic"] = "life_in_modern_britain"
        if not isinstance(q["mark_scheme"].get("indicative_points"), list):
            q["mark_scheme"]["indicative_points"] = []
        q.setdefault("active", True)
        q.setdefault("difficulty", "medium")
        q.setdefault("question_type", "explain")
        valid.append(q)

    return valid


def load_questions_db() -> dict:
    path = Path("data/questions.json")
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"version": "1.0", "questions": []}


def save_questions_db(db: dict) -> None:
    path = Path("data/questions.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(db, f, indent=2)


def already_ingested_sources(db: dict) -> set:
    """Return set of 'paper|code' strings already present in the question bank."""
    seen = set()
    for q in db.get("questions", []):
        src = q.get("source", {})
        paper = src.get("paper", "")
        code = src.get("paper_code", "")
        if paper and code:
            seen.add(f"{paper}|{code}")
    return seen


def main():
    parser = argparse.ArgumentParser(
        description="Batch-ingest past papers into data/questions.json"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract questions and print them without saving",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process papers that have already been ingested (adds duplicates unless IDs match)",
    )
    parser.add_argument(
        "--paper",
        metavar="LABEL",
        help='Only process the entry with this paper label, e.g. "June 2023"',
    )
    parser.add_argument(
        "--code",
        metavar="CODE",
        help='Only process the entry with this paper code, e.g. "J560/01"',
    )
    args = parser.parse_args()

    if not Config.ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    manifest_path = Path("papers/manifest.json")
    if not manifest_path.exists():
        print(f"Error: {manifest_path} not found. Create it with a list of papers to ingest.")
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    # Strip _comment entries (they're not real papers)
    manifest = [e for e in manifest if "paper" in e and "paper_pdf" in e]

    # Optional filter by --paper / --code
    if args.paper:
        manifest = [e for e in manifest if e["paper"] == args.paper]
    if args.code:
        manifest = [e for e in manifest if e["code"] == args.code]

    if not manifest:
        print("No matching entries found in manifest.")
        sys.exit(0)

    db = load_questions_db()
    existing_ids = {q["id"] for q in db["questions"]}
    ingested_sources = already_ingested_sources(db) if not args.force else set()

    total_added = 0
    total_dupes = 0
    total_skipped_papers = 0

    for entry in manifest:
        paper = entry["paper"]
        code = entry["code"]
        source_key = f"{paper}|{code}"

        if source_key in ingested_sources:
            print(f"Skipping  {paper} ({code}) — already ingested (use --force to re-process)")
            total_skipped_papers += 1
            continue

        paper_pdf = entry["paper_pdf"]
        ms_pdf = entry["ms_pdf"]

        missing = [p for p in [paper_pdf, ms_pdf] if not Path(p).exists()]
        if missing:
            print(f"Skipping  {paper} ({code}) — PDF(s) not found:")
            for p in missing:
                print(f"            {p}")
            total_skipped_papers += 1
            continue

        print(f"\nProcessing {paper} ({code})")

        try:
            questions = extract_questions(paper_pdf, ms_pdf, paper, code)
        except json.JSONDecodeError as exc:
            print(f"  Error: Claude returned invalid JSON — {exc}")
            print("  Skipping this paper. Try again or check the PDF quality.")
            total_skipped_papers += 1
            continue
        except Exception as exc:
            print(f"  Error: {exc}")
            total_skipped_papers += 1
            continue

        # Deduplicate by question ID
        new_questions = [q for q in questions if q["id"] not in existing_ids]
        dupes = len(questions) - len(new_questions)

        if args.dry_run:
            print(f"  Would add {len(new_questions)} questions ({dupes} duplicates skipped)")
            print(json.dumps(new_questions, indent=2))
        else:
            db["questions"].extend(new_questions)
            existing_ids.update(q["id"] for q in new_questions)
            total_added += len(new_questions)
            total_dupes += dupes
            print(f"  Added {len(new_questions)} questions ({dupes} duplicates skipped)")

    print()
    if args.dry_run:
        print("Dry run complete — nothing was saved.")
    else:
        if total_added > 0:
            save_questions_db(db)
            print(f"Saved. Added {total_added} questions ({total_dupes} duplicates skipped).")
            print(f"Total questions in bank: {len(db['questions'])}")
            print()
            print("Next step: commit data/questions.json so questions are available everywhere:")
            print("  git add data/questions.json && git commit -m 'Add questions from past papers'")
        else:
            print("Nothing new to save.")
        if total_skipped_papers:
            print(f"({total_skipped_papers} paper(s) skipped — see above)")


if __name__ == "__main__":
    main()
