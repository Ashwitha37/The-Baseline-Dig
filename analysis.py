"""Step 3 of The Baseline Dig: turn the answers into a diagnosis.

Everything here is plain logic (no AI): a verdict per concept, the root gaps found by walking
the dependency links, and the mistaken beliefs the learner's wrong answers revealed.
"""
from quiz import print_grid

WEAK = {"Shaky", "Gap", "Misconception"}
BROKEN = {"Gap", "Misconception"}  # actually answered wrong
MEANING = {
    "Misconception": "you were sure, but your answer was wrong",
    "Gap": "your answer was wrong, and you were unsure",
    "Shaky": "right answers, but you were guessing",
}


def verdict_for(concept_id, log):
    entries = [e for e in log if e["concept_id"] == concept_id]
    if not entries:
        return "Not tested"
    if any(not e["correct"] and not e["guessing"] for e in entries):
        return "Misconception"
    if any(not e["correct"] for e in entries):
        return "Gap"
    if any(e["guessing"] for e in entries):
        return "Shaky"
    return "Solid"


def _stages_cleared(log):
    cleared = 0
    for s in sorted({e["stage"] for e in log}):
        stage = [e for e in log if e["stage"] == s]
        if sum(e["correct"] for e in stage) * 3 >= len(stage) * 2:
            cleared += 1
        else:
            break
    return cleared


def analyse(concepts, log):
    """Work out verdicts, root gaps (weak concepts whose prerequisites did not fail) and a plan."""
    by_id = {c["id"]: c for c in concepts}
    verdicts = {c["id"]: verdict_for(c["id"], log) for c in concepts}
    root, blocked = [], {}
    for c in concepts:
        if verdicts[c["id"]] not in WEAK:
            continue
        failed_prereqs = [by_id[d]["name"] for d in c["depends_on"] if verdicts[d] in BROKEN]
        if failed_prereqs:
            blocked[c["id"]] = failed_prereqs
        else:
            root.append(c["id"])
    seen, beliefs = set(), []
    for e in log:
        if not e["correct"] and e["misconception"] and (e["concept_id"], e["misconception"]) not in seen:
            seen.add((e["concept_id"], e["misconception"]))
            beliefs.append((e["concept_name"], e["misconception"]))
    return {
        "verdicts": verdicts,
        "root": root,
        "blocked": blocked,
        "plan": [c["id"] for c in concepts if verdicts[c["id"]] in WEAK],  # prerequisites first
        "beliefs": beliefs,
        "stages_cleared": _stages_cleared(log),
        "fast_share": sum(e["seconds"] < 3 for e in log) / len(log) if log else 0,
    }


def print_analysis(concepts, result, log):
    by_id = {c["id"]: c for c in concepts}
    right = sum(e["correct"] for e in log)
    n = result["stages_cleared"]
    print("\n" + "=" * 60)
    print("YOUR RESULTS")
    print("=" * 60)
    print(f"\nYou answered {right} of {len(log)} questions correctly and cleared "
          f"{n} stage{'s' if n != 1 else ''}.\n")
    print_grid(concepts, log)

    print("\nHow you did on each concept:")
    for c in concepts:
        v = result["verdicts"][c["id"]]
        note = MEANING.get(v, "you answered correctly and were sure" if v == "Solid" else "")
        print(f"  {c['name']}: {v}" + (f" ({note})" if note else ""))

    if not result["plan"]:
        print("\nNo gaps found: everything you answered was right, and you were sure about it.")
        return

    print("\nWhere you are lacking:")
    for cid in result["plan"]:
        if cid in result["blocked"]:
            tag = "builds on " + ", ".join(result["blocked"][cid])
        else:
            tag = "start here"
        print(f"  - {by_id[cid]['name']} [{tag}]")
    if result["root"]:
        names = ", ".join(by_id[c]["name"] for c in result["root"])
        print(f"\nThe best place to start: {names}.")
        print("Concepts that build on a weak one may only look weak because of it, so fix the base first.")
    if result["beliefs"]:
        print("\nMistaken beliefs your answers suggest:")
        for name, belief in result["beliefs"]:
            print(f"  - {name}: {belief}")
    if len(log) >= 4 and result["fast_share"] >= 0.5:
        print("\nNote: many answers came in under 3 seconds. Fast answers are weaker evidence,")
        print("so treat parts of this result with some caution.")
