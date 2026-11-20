"""The Baseline Dig: find where your understanding actually breaks.

Part 1: type any topic -> the AI builds a map of its prerequisite and core concepts.
Part 2: a staged quiz over those concepts, with instant right/wrong feedback.
Part 3: the analysis: where your understanding breaks, and which mistaken beliefs you showed.
Part 4: longer, step-by-step explanations of your gaps, based on the answers you chose.
Run:  python main.py            (normal)
      python main.py --models   (list the AI models your key can use)
"""
import sys

from ai import list_models
from analysis import analyse, print_analysis
from concept_map import build_concept_map
from quiz import run_diagnostic, start_stage
from teach import start_lessons, teach


def show_map(result):
    concepts = result["concepts"]
    names = {c["id"]: c["name"] for c in concepts}
    print(f"\nTo understand {result['topic']}, these are the pieces:\n")
    for i, c in enumerate(concepts, 1):
        needs = ", ".join(names[d] for d in c["depends_on"]) or "nothing else (a starting point)"
        print(f"{i}. {c['name']}  [{c['type']}]")
        print(f"   {c['description']}")
        print(f"   needs: {needs}\n")


def ask_for_topic():
    """Keep asking until the AI accepts the topic and returns a valid map."""
    while True:
        topic = input("What do you want to understand? ").strip()
        if not topic:
            continue
        print("Mapping your topic...")
        result = build_concept_map(topic)
        if result["ok"]:
            return result
        print(result["reason"] + "\n")


def main():
    if "--models" in sys.argv:
        list_models()
        return
    print("=== The Baseline Dig ===")
    print("Type any topic. We'll find where your understanding really breaks.\n")
    try:
        concept_map = ask_for_topic()
        concepts = concept_map["concepts"]
        show_map(concept_map)
        # Stage 1 is prepared in the background while you read the map above.
        first_stage = start_stage(concept_map["topic"], concepts, 0, [], [])
        input("Press Enter to start the quiz... ")
        log = run_diagnostic(concept_map, first_stage)
        if not log:
            return
        result = analyse(concepts, log)
        # The explanations are written in the background while you read your results.
        lessons = start_lessons(concept_map["topic"], concepts, result, log)
        print_analysis(concepts, result, log)
        if result["plan"]:
            input("\nPress Enter to start your explanations... ")
            teach(concept_map["topic"], concepts, result, log, lessons)
            print("\nThat covers what was missing. Next part: a quick recheck with new questions,")
            print("to see whether you improved.")
    except KeyboardInterrupt:
        print("\nBye!")
    except RuntimeError as e:
        print(f"\n{e}")


if __name__ == "__main__":
    main()
