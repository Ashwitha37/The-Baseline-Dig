"""Step 1 of The Baseline Dig: turn a typed topic into a small map of concepts and their prerequisites.

The AI proposes the map; this file checks it (so a bad reply never reaches the rest of the app)
and puts the concepts in a valid learning order.
"""
from ai import ask_json

PROMPT = """You are building a learning map for a diagnostic learning app.

The learner typed this topic: "__TOPIC__"

First decide whether it is a real, learnable topic. If it is too vague (for example "stuff"),
nonsense, or something harmful, reply with exactly:
{"ok": false, "reason": "<one friendly sentence telling the learner how to rephrase>"}

Otherwise reply with:
{"ok": true, "topic": "<clean topic name>", "concepts": [
  {"id": "c1", "name": "<short name>", "description": "<one plain-language line>",
   "type": "prerequisite" or "core", "depends_on": ["<ids of concepts this one needs>"]}
]}

Rules for "concepts":
- Give 5 to 6 concepts, aimed at a learner who is new to the topic.
- Mix "prerequisite" concepts (foundations the topic stands on) with "core" concepts
  (the main domains of the topic itself).
- Together they must cover every important domain of the topic, with no overlap.
- "depends_on" may only contain ids from this same list. No cycles.
  At least one concept has an empty list.
- List the concepts so each one comes after the concepts it depends on.
Reply with JSON only.
"""

VALID_TYPES = {"prerequisite", "core"}


def _topological_order(concepts):
    """Order concepts so every concept comes after its prerequisites. None means a cycle."""
    remaining = list(concepts)
    done, ordered = set(), []
    while remaining:
        ready = [c for c in remaining if all(d in done for d in c["depends_on"])]
        if not ready:
            return None
        for c in ready:
            ordered.append(c)
            done.add(c["id"])
        remaining = [c for c in remaining if c["id"] not in done]
    return ordered


def validate_map(data):
    """Return (ordered_concepts, None) if the AI's map is usable, else (None, reason)."""
    concepts = data.get("concepts")
    if not isinstance(concepts, list) or not 4 <= len(concepts) <= 7:
        return None, "give 5 to 6 concepts in a list called concepts"

    ids, clean = set(), []
    for c in concepts:
        try:
            deps = c["depends_on"]
            if not isinstance(deps, list):
                raise TypeError
            item = {
                "id": str(c["id"]).strip(),
                "name": str(c["name"]).strip(),
                "description": str(c["description"]).strip(),
                "type": str(c["type"]).strip().lower(),
                "depends_on": [str(d).strip() for d in deps],
            }
        except (KeyError, TypeError):
            return None, "every concept needs id, name, description, type and depends_on (a list)"
        if item["type"] not in VALID_TYPES:
            return None, "type must be either prerequisite or core"
        if item["id"] in ids:
            return None, "concept ids must be unique"
        ids.add(item["id"])
        clean.append(item)

    for item in clean:
        for d in item["depends_on"]:
            if d not in ids or d == item["id"]:
                return None, f"concept {item['id']} depends on an unknown id or on itself: {d}"

    ordered = _topological_order(clean)
    if ordered is None:
        return None, "the dependencies contain a cycle"
    return ordered, None


def build_concept_map(topic, attempts=3):
    """Ask the AI for a concept map and keep asking (with feedback) until it is valid.

    Returns {"ok": False, "reason": ...} if the topic is unusable,
    or {"ok": True, "topic": ..., "concepts": [...]} in learning order.
    """
    prompt = PROMPT.replace("__TOPIC__", topic.replace('"', "'"))
    problem = None
    for _ in range(attempts):
        text = prompt
        if problem:
            text += f"\nYour previous reply was rejected because: {problem}. Fix it."
        data = ask_json(text)
        if not isinstance(data, dict):
            problem = "the reply must be a single JSON object"
            continue
        if data.get("ok") is False:
            return {"ok": False, "reason": str(data.get("reason", "Please try a clearer topic."))}
        concepts, problem = validate_map(data)
        if concepts:
            return {
                "ok": True,
                "topic": str(data.get("topic") or topic).strip(),
                "concepts": concepts,
            }
    raise RuntimeError(f"Could not build a valid concept map: {problem}")
