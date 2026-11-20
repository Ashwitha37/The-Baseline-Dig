"""Step 2 of The Baseline Dig: the diagnostic quiz.

For each difficulty stage the AI writes one multiple-choice question per concept, a second
AI call double-checks the answer key, and the learner answers in the terminal. Every answer
is recorded (correct?, guessing?, which misconception was picked, how long it took) so the
next part can analyse it.
"""
import os
import random
import textwrap
import time

from ai import ask_json, background

STAGES = [
    ("Warm-up", "recall of the key ideas and vocabulary"),
    ("Understand", "explaining or applying an idea in a simple situation"),
    ("Apply", "multi-step reasoning on a realistic example"),
    ("Analyse", "edge cases, comparing approaches and spotting errors"),
    ("Master", "design decisions, trade-offs and unfamiliar situations"),
]
LETTERS = "ABCD"

GEN_PROMPT = """You write diagnostic multiple-choice questions for a learning app.

Topic: "__TOPIC__"
Stage __STAGE__ of 5: __STAGE_NAME__ (focus: __STAGE_FOCUS__)

Write exactly ONE question for each concept below.
__CONCEPTS__

Rules:
- Assume the learner is new to the topic. Difficulty must match the stage.
- One clearly correct answer, plus exactly 3 wrong options. Each wrong option must be plausible
  and reflect a real, common mistake. For each wrong option give a short "misconception":
  the mistaken belief, in plain words, that would make someone choose it.
- No "all of the above" or "none of the above". No trick wording.
- The question must make sense on its own. Keep the question under 60 words and every option
  under 20 words.
- Vary the style across questions: predict the result, spot the mistake, choose the best
  approach, or apply the idea to a real situation.
- If a question needs a small table or code, write it as plain text on a few short lines
  (no markdown, no backticks).
__EXTRA__
Reply with JSON only, in this shape:
{"questions": [{"concept_id": "c1", "question": "...", "correct": "...",
  "wrong": [{"text": "...", "misconception": "..."}, {"text": "...", "misconception": "..."},
            {"text": "...", "misconception": "..."}],
  "explanation": "one or two sentences on why the correct answer is right"}]}
"""

VERIFY_PROMPT = """Answer these multiple-choice questions as an expert teacher. For each one,
choose the single best answer. If more than one option could reasonably be correct, or none is
correct, set "ambiguous" to true.

__QUESTIONS__

Reply with JSON only, in this shape:
{"answers": [{"n": 1, "choice": "B", "ambiguous": false}]}
"""


# ---------- making the questions ----------

def _concept_lines(concepts):
    return "\n".join(
        f"- id: {c['id']} | name: {c['name']} | meaning: {c['description']}" for c in concepts
    )


def _extra_notes(history, asked):
    notes = []
    weak = {}
    for h in history:
        if not h["correct"] and h["misconception"]:
            weak[h["concept_name"]] = h["misconception"]
    if weak:
        shown = "; ".join(f"{name}: {m}" for name, m in weak.items())
        notes.append(
            "The learner already showed these mistaken beliefs. Probe those concepts again from "
            f"a fresh angle (do not repeat the question): {shown}"
        )
    if asked:
        notes.append(
            "Never repeat or closely copy these earlier questions: "
            + " || ".join(q[:80] for q in asked)
        )
    return ("\n".join(notes) + "\n") if notes else ""


def _clean_questions(data, concepts):
    """Check the AI's questions and shuffle the options. Returns {concept_id: question}."""
    ids = {c["id"] for c in concepts}
    items = data.get("questions") if isinstance(data, dict) else None
    out = {}
    for q in items if isinstance(items, list) else []:
        try:
            cid = str(q["concept_id"]).strip()
            text = str(q["question"]).strip()
            correct = str(q["correct"]).strip()
            wrong = [
                (str(w["text"]).strip(), str(w.get("misconception", "")).strip())
                for w in q["wrong"]
            ]
            explanation = str(q.get("explanation", "")).strip()
        except (KeyError, TypeError, AttributeError):
            continue
        texts = [correct] + [w[0] for w in wrong]
        if cid not in ids or cid in out or not text or len(wrong) != 3:
            continue
        if any(not t for t in texts) or len({t.lower() for t in texts}) != 4:
            continue
        options = [(correct, "")] + wrong
        random.shuffle(options)
        names = [o[0] for o in options]
        out[cid] = {
            "concept_id": cid,
            "question": text,
            "options": names,
            "misconceptions": [o[1] for o in options],
            "answer": names.index(correct),
            "explanation": explanation,
        }
    return out


def _verify(questions, quiet=False):
    """Second opinion: an AI call answers each question without seeing the key.

    Questions it answers differently, or calls ambiguous, are dropped.
    """
    if not questions or os.environ.get("BASELINE_VERIFY") == "0":
        return questions
    ids = list(questions)
    blocks = []
    for n, cid in enumerate(ids, 1):
        q = questions[cid]
        opts = "\n".join(f"  {LETTERS[i]}. {t}" for i, t in enumerate(q["options"]))
        blocks.append(f"Question {n}: {q['question']}\n{opts}")
    prompt = VERIFY_PROMPT.replace("__QUESTIONS__", "\n\n".join(blocks))
    try:
        data = ask_json(prompt, quiet=quiet)
        answers = {int(a["n"]): a for a in data["answers"]}
    except Exception:
        if not quiet:
            print("  (could not double-check the questions this time, continuing without it)")
        return questions
    good = {}
    for n, cid in enumerate(ids, 1):
        q, a = questions[cid], answers.get(n)
        if a is None:
            good[cid] = q  # no opinion from the checker is not evidence of an error
            continue
        letter = str(a.get("choice", "")).strip().upper()[:1]
        if letter == LETTERS[q["answer"]] and a.get("ambiguous") is not True:
            good[cid] = q
    return good


def generate_stage(topic, concepts, stage_index, history, asked, quiet=False):
    """One checked question per concept. Rejected questions are regenerated once."""
    name, focus = STAGES[stage_index]
    good = {}
    for _ in range(2):
        missing = [c for c in concepts if c["id"] not in good]
        if not missing:
            break
        prompt = (
            GEN_PROMPT.replace("__TOPIC__", topic.replace('"', "'"))
            .replace("__STAGE__", str(stage_index + 1))
            .replace("__STAGE_NAME__", name)
            .replace("__STAGE_FOCUS__", focus)
            .replace("__CONCEPTS__", _concept_lines(missing))
            .replace("__EXTRA__", _extra_notes(history, asked))
        )
        good.update(_verify(_clean_questions(ask_json(prompt, quiet=quiet), missing), quiet))
    return [good[c["id"]] for c in concepts if c["id"] in good]


def start_stage(topic, concepts, stage_index, history, asked):
    """Start preparing a stage in the background. Returns a Future (.done(), .result())."""
    return background(
        generate_stage, topic, concepts, stage_index, list(history), list(asked), True
    )


# ---------- playing ----------

def wrap_text(text, first="", rest=""):
    """Wrap long lines for the terminal but keep line breaks (tables, small code)."""
    lines = text.splitlines() or [""]
    out = []
    for i, line in enumerate(lines):
        out.append(textwrap.fill(line, 78, initial_indent=first if i == 0 else rest,
                                 subsequent_indent=rest) if line.strip() else "")
    return "\n".join(out)


def ask_one(q, concept_name, stage_number, stage_name, number, total, streak):
    """Show one question, read the answer, then show right/wrong with a short reason.

    Returns (log entry, new streak).
    """
    print(f"\nStage {stage_number}: {stage_name}  |  Question {number} of {total}  |  {concept_name}")
    print("-" * 60)
    print(wrap_text(q["question"]) + "\n")
    for i, text in enumerate(q["options"]):
        print(wrap_text(text, f"  {LETTERS[i]}. ", "     "))
    print()
    start = time.monotonic()
    while True:
        raw = input("Your answer (A-D). Add ? if you are guessing, e.g. B?  > ").strip().upper()
        guessing = raw.endswith("?")
        letter = raw.rstrip("?").strip()
        if len(letter) == 1 and letter in LETTERS[: len(q["options"])]:
            break
        print("  Please type A, B, C or D.")
    seconds = round(time.monotonic() - start, 1)
    picked = LETTERS.index(letter)
    correct = picked == q["answer"]
    if correct:
        streak += 1
        print("  Correct!" + (f"  Streak: {streak}" if streak >= 2 else ""))
    else:
        streak = 0
        right = f"Not quite. The answer was {LETTERS[q['answer']]}: {q['options'][q['answer']]}"
        print(wrap_text(right, "  ", "  "))
    if q["explanation"]:
        print(wrap_text(q["explanation"], "  ", "  "))
    entry = {
        "concept_id": q["concept_id"],
        "concept_name": concept_name,
        "stage": stage_number,
        "correct": correct,
        "guessing": guessing,
        "misconception": "" if correct else q["misconceptions"][picked],
        "seconds": seconds,
        "question": q["question"],
        "picked_text": q["options"][picked],
        "correct_text": q["options"][q["answer"]],
        "explanation": q["explanation"],
    }
    return entry, streak


def run_diagnostic(concept_map, first_stage=None):
    """Play the stages. Returns the log of every answer given.

    While the learner answers one stage, the next one is already being prepared in the
    background, so there is very little waiting. `first_stage` can be a Stage 1 job that was
    started earlier (for example while the learner was reading the concept map).
    """
    topic, concepts = concept_map["topic"], concept_map["concepts"]
    names = {c["id"]: c["name"] for c in concepts}
    log, asked, streak = [], [], 0
    print("\nQuiz time! After each answer you will see whether it was right, with a short reason.")
    print("Type a letter, or add ? if you are guessing (for example B?). A full explanation of")
    print("whatever you are missing comes after your results.")

    pending = first_stage or start_stage(topic, concepts, 0, [], [])
    for stage_index, (stage_name, _) in enumerate(STAGES):
        stage_number = stage_index + 1
        if not pending.done():
            print(f"\nPreparing Stage {stage_number} ({stage_name})... almost there.")
        try:
            questions = pending.result()
        except Exception as e:
            print(f"\nCould not prepare this stage: {e}")
            break
        if not questions:
            print("No usable questions came back for this stage, so we stop here.")
            break

        if stage_index + 1 < len(STAGES):  # get the next stage ready while this one is played
            pending = start_stage(
                topic, concepts, stage_index + 1, log, asked + [q["question"] for q in questions]
            )

        stage_log = []
        for number, q in enumerate(questions, 1):
            entry, streak = ask_one(
                q, names[q["concept_id"]], stage_number, stage_name, number, len(questions), streak
            )
            stage_log.append(entry)
            asked.append(q["question"])
        log.extend(stage_log)

        score, total = sum(e["correct"] for e in stage_log), len(stage_log)
        print(f"\nStage {stage_number} score: {score} of {total}")
        passed = score * 3 >= total * 2  # two thirds or more unlocks the next stage
        if passed and stage_index < len(STAGES) - 1:
            print("Stage cleared! Moving up.")
            continue
        print("You cleared every stage." if passed else "That is enough to see where things wobble.")
        break
    return log


# ---------- a first look at the results ----------

def _symbol(entry):
    if entry is None:
        return "-"
    if entry["correct"]:
        return "~" if entry["guessing"] else "OK"
    return "x" if entry["guessing"] else "x!"


def print_grid(concepts, log):
    """Concepts down the side, stages across the top."""
    stages = sorted({e["stage"] for e in log})
    if not stages:
        return
    width = min(30, max(len(c["name"]) for c in concepts)) + 2
    print("Concept".ljust(width) + "".join(f"Stage {s}".ljust(9) for s in stages))
    for c in concepts:
        row = c["name"][:width - 2].ljust(width)
        for s in stages:
            entry = next((e for e in log if e["concept_id"] == c["id"] and e["stage"] == s), None)
            row += _symbol(entry).ljust(9)
        print(row)
    print("\nOK = correct and sure   ~ = correct but guessing   x = wrong, guessing   "
          "x! = wrong and sure   - = not reached")
