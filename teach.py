"""Step 4 of The Baseline Dig: explain the gaps, based on the learner's own answers.

For each weak concept (prerequisites first) the AI gets the exact answers the learner chose and
the mistaken belief each one suggests, and writes a clear, step-by-step explanation aimed at
those beliefs.
"""
import threading

from ai import ask_json, background
from quiz import wrap_text

_LIMIT = threading.BoundedSemaphore(3)  # at most 3 lessons are written at the same time

LESSON_PROMPT = """You are a kind, clear tutor. A learner just took a diagnostic quiz on "__TOPIC__".

Concept to teach: __NAME__ (__DESC__)
Verdict: __VERDICT__ (Misconception = sure but wrong, Gap = wrong and unsure, Shaky = right but guessing)
The learner's status on the concepts this one builds on: __PREREQ__

What the learner answered on this concept:
__ANSWERS__

Write a clear, friendly lesson aimed at THIS learner's answers. Assume a beginner and explain every
technical term the first time you use it. Use short paragraphs.
- "what_went_wrong": 3 to 4 sentences, speaking to the learner as "you". Name the specific mistaken
  belief(s) their answers suggest, explain why that belief feels logical, then say plainly where it
  goes wrong. Be kind, never judgmental.
- "explanation": the correct idea in 150 to 250 words, as 2 to 4 short paragraphs separated by a blank
  line. Build it step by step from the basics and directly correct the mistaken belief(s). If this
  concept builds on a prerequisite the learner is also weak in, briefly recall that idea first.
- "analogy": an everyday analogy in 2 to 4 sentences that maps clearly onto the idea.
- "example": one worked example, shown step by step with a short reason for each step. Plain text on
  short lines (a small table written as plain text is fine).
- "mistakes": 2 to 3 common mistakes to avoid, each one sentence saying what people do wrong and why
  it fails.
- "takeaways": 3 to 4 short sentences worth remembering.
Plain text only: no markdown, no asterisks, no backticks.
Reply with JSON only:
{"what_went_wrong": "...", "explanation": "...", "analogy": "...", "example": "...",
 "mistakes": ["...", "..."], "takeaways": ["...", "...", "..."]}
"""


def _answer_lines(entries):
    lines = []
    for e in entries:
        question = e["question"].replace("\n", " / ")
        if e["correct"]:
            if e["guessing"]:
                lines.append(
                    f'- (stage {e["stage"]}) Question: "{question}" | chose the right answer '
                    f'"{e["picked_text"]}" but marked it as a guess'
                )
            continue
        sure = "marked as a guess" if e["guessing"] else "was sure"
        lines.append(
            f'- (stage {e["stage"]}) Question: "{question}" | chose: "{e["picked_text"]}" ({sure}) '
            f'| correct answer: "{e["correct_text"]}" | belief this choice suggests: '
            f'"{e["misconception"] or "unknown"}"'
        )
    return "\n".join(lines) or "- (answered correctly and confidently)"


def _text_list(value, limit):
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _clean_lesson(data):
    if not isinstance(data, dict):
        return None
    text = lambda key: str(data.get(key) or "").strip()
    lesson = {
        "went": text("what_went_wrong"),
        "idea": text("explanation"),
        "analogy": text("analogy"),
        "example": text("example"),
        "mistakes": _text_list(data.get("mistakes"), 3),
        "takeaways": _text_list(data.get("takeaways"), 4),
    }
    return lesson if lesson["idea"] else None


def make_lesson(topic, concept, verdict, entries, prereq_status):
    prompt = (
        LESSON_PROMPT.replace("__TOPIC__", topic.replace('"', "'"))
        .replace("__NAME__", concept["name"])
        .replace("__DESC__", concept["description"])
        .replace("__VERDICT__", verdict)
        .replace("__PREREQ__", prereq_status or "this concept has no prerequisites")
        .replace("__ANSWERS__", _answer_lines(entries))
    )
    for _ in range(2):
        try:
            lesson = _clean_lesson(ask_json(prompt, quiet=True))
        except RuntimeError:
            return None
        if lesson:
            return lesson
    return None


def _show(lesson):
    if lesson["went"]:
        print("\nWhat happened in your answers:")
        print(wrap_text(lesson["went"], "  ", "  "))
    print("\nThe idea, step by step:")
    print(wrap_text(lesson["idea"], "  ", "  "))
    if lesson["analogy"]:
        print("\nThink of it like this:")
        print(wrap_text(lesson["analogy"], "  ", "  "))
    if lesson["example"]:
        print("\nWorked example:")
        print(wrap_text(lesson["example"], "  ", "  "))
    if lesson["mistakes"]:
        print("\nCommon mistakes to avoid:")
        for m in lesson["mistakes"]:
            print(wrap_text(m, "  - ", "    "))
    if lesson["takeaways"]:
        print("\nRemember:")
        for t in lesson["takeaways"]:
            print(wrap_text(t, "  - ", "    "))


def _fallback(entries):
    """If the AI cannot write a lesson, at least show the reasoning behind the right answers."""
    print("\nThe AI could not write a lesson right now. Here is what the quiz can tell you:")
    shown = False
    for e in entries:
        if not e["correct"] and e["explanation"]:
            print(wrap_text(f'  Question: {e["question"]}', "", "  "))
            print(wrap_text(f'  Right answer: {e["correct_text"]}. {e["explanation"]}', "", "  "))
            shown = True
    if not shown:
        print("  Try this concept again after a short break.")


def _prereq_status(concept, by_id, result):
    return "; ".join(f"{by_id[d]['name']}: {result['verdicts'][d]}" for d in concept["depends_on"])


def _lesson_job(topic, concept, verdict, entries, prereq_status):
    with _LIMIT:
        return make_lesson(topic, concept, verdict, entries, prereq_status)


def start_lessons(topic, concepts, result, log):
    """Start writing every lesson in the background. Returns {concept_id: Future}."""
    by_id = {c["id"]: c for c in concepts}
    jobs = {}
    for cid in result["plan"]:
        concept = by_id[cid]
        entries = [e for e in log if e["concept_id"] == cid]
        jobs[cid] = background(
            _lesson_job, topic, concept, result["verdicts"][cid], entries,
            _prereq_status(concept, by_id, result),
        )
    return jobs


def teach(topic, concepts, result, log, lessons=None):
    by_id = {c["id"]: c for c in concepts}
    plan = result["plan"]
    lessons = lessons or start_lessons(topic, concepts, result, log)
    for i, cid in enumerate(plan, 1):
        concept = by_id[cid]
        verdict = result["verdicts"][cid]
        entries = [e for e in log if e["concept_id"] == cid]
        if cid in result["blocked"]:
            tag = "builds on: " + ", ".join(result["blocked"][cid])
        else:
            tag = "start here"
        print("\n" + "=" * 60)
        print(f"{i} of {len(plan)}: {concept['name']}   [{verdict}, {tag}]")
        print("=" * 60)
        job = lessons[cid]
        if not job.done():
            print("Writing your explanation... almost there.")
        try:
            lesson = job.result()
        except Exception:
            lesson = None
        if lesson:
            _show(lesson)
        else:
            _fallback(entries)
        if i < len(plan):
            input("\nPress Enter for the next concept... ")
