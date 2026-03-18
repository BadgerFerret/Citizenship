#!/usr/bin/env python3
"""One-off migration: parse MCQ options out of question_text into structured fields."""
import json, re, os, sys

QUESTIONS_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'questions.json')


def parse_mcq(text):
    """Return (stem, options) or (None, None) if not an MCQ question."""
    if '  A)' not in text:
        return None, None

    first_opt = re.search(r'\n\s{2,}A\)', text)
    if not first_opt:
        return None, None

    stem = text[:first_opt.start()].strip()
    stem = re.sub(r'\s*\(\d+\)\s*$', '', stem).strip()

    options_text = text[first_opt.start():]
    # Split on option markers like "\n  A) ", "\n  B) " etc.
    parts = re.split(r'\n\s{2,}([A-F])\)\s*', options_text)

    options = []
    i = 1
    while i < len(parts) - 1:
        letter = parts[i]
        raw = parts[i + 1]
        # Clean control characters and collapse word-wrapped lines
        raw = raw.replace('\x07', '').replace('\t', ' ')
        opt_text = ' '.join(line.strip() for line in raw.splitlines() if line.strip())
        # Truncate at 250 chars to cut off any source text that bled in during PDF parse
        opt_text = opt_text[:250].strip()
        if opt_text:
            options.append({"letter": letter, "text": opt_text})
        i += 2

    if len(options) < 2:
        return None, None
    return stem, options


def main():
    with open(QUESTIONS_PATH) as f:
        data = json.load(f)

    changed = 0
    for q in data['questions']:
        if q.get('options'):          # already migrated
            continue
        stem, options = parse_mcq(q['question_text'])
        if not options:
            continue

        q['question_text'] = stem
        q['options'] = options
        is_multi = bool(re.search(r'[Ww]hich two', stem))
        q['question_type'] = 'multiple_choice_multi' if is_multi else 'multiple_choice'

        # Promote "Correct answer: X" from indicative_points to a dedicated field
        for pt in q['mark_scheme']['indicative_points']:
            m = re.search(r'[Cc]orrect answer:\s*([A-Fa-f])', pt)
            if m:
                q['mark_scheme']['correct_answer'] = m.group(1).upper()
                break

        changed += 1

    with open(QUESTIONS_PATH, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Migrated {changed} questions to structured MCQ format.")


if __name__ == '__main__':
    main()
