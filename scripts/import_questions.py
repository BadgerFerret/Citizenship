#!/usr/bin/env python3
"""
import_questions.py — Extract GCSE Citizenship questions from PDF past papers
using Claude's document understanding.

Usage:
    python scripts/import_questions.py \\
        --paper "June 2023" \\
        --code "J560/01" \\
        --paper-pdf path/to/paper.pdf \\
        --ms-pdf path/to/markscheme.pdf

Output: JSON array of question objects (stdout). Redirect to a file, review,
then paste into the Admin → Import tab in the web app.

Example:
    python scripts/import_questions.py \\
        --paper "June 2023" --code "J560/01" \\
        --paper-pdf papers/2023_j1_paper.pdf \\
        --ms-pdf papers/2023_j1_ms.pdf \\
        > /tmp/june2023_questions.json
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path

# Add parent dir to path so config.py is importable when run from scripts/
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
    "exam_board": "OCR"
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
- Set "topic" based on the subject matter of the question. Use your knowledge of the OCR J560
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

    print(f"Sending PDFs to Claude ({Config.CLAUDE_MODEL})...", file=sys.stderr)

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

    # Validate and clean up
    valid = []
    for i, q in enumerate(questions):
        errors = []
        for field in ["id", "topic", "question_text", "marks", "mark_scheme"]:
            if not q.get(field):
                errors.append(f"missing '{field}'")
        if q.get("topic") not in TOPICS:
            print(
                f"  Warning: question {q.get('id', i+1)} has unknown topic '{q.get('topic')}' — "
                f"defaulting to 'life_in_modern_britain'",
                file=sys.stderr,
            )
            q["topic"] = "life_in_modern_britain"
        if errors:
            print(f"  Skipping question {i+1}: {', '.join(errors)}", file=sys.stderr)
            continue
        if not isinstance(q["mark_scheme"].get("indicative_points"), list):
            q["mark_scheme"]["indicative_points"] = []
        q.setdefault("active", True)
        q.setdefault("difficulty", "medium")
        q.setdefault("question_type", "explain")
        valid.append(q)

    print(f"Extracted {len(valid)} questions ({len(questions) - len(valid)} skipped).", file=sys.stderr)
    return valid


def main():
    parser = argparse.ArgumentParser(description="Extract GCSE Citizenship questions from PDFs")
    parser.add_argument("--paper", required=True, help='Paper label, e.g. "June 2023"')
    parser.add_argument("--code", required=True, help='Paper code, e.g. "J560/01"')
    parser.add_argument("--paper-pdf", required=True, help="Path to the question paper PDF")
    parser.add_argument("--ms-pdf", required=True, help="Path to the mark scheme PDF")
    args = parser.parse_args()

    if not Config.ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set. Create a .env file with your API key.", file=sys.stderr)
        sys.exit(1)

    for path in [args.paper_pdf, args.ms_pdf]:
        if not Path(path).exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)

    questions = extract_questions(args.paper_pdf, args.ms_pdf, args.paper, args.code)

    # Print JSON to stdout so it can be reviewed and redirected
    print(json.dumps(questions, indent=2))


if __name__ == "__main__":
    main()
