"""Thin helper around the Gemini API.

Every AI call in The Baseline Dig goes through ask_json(), which always returns parsed JSON
(a dict or list) or raises a clear error. If a model is busy or missing, it waits and then
falls back to the next model in the list. background() runs any function in the background so
the app can prepare the next step while the learner is busy.

Troubleshooting switches (environment variables):
  GEMINI_MODEL=name      try this model first
  BASELINE_DEBUG=1       print how long each AI call took
  BASELINE_VERIFY=0      skip the second "double-check the questions" call (faster, less safe)
"""
import json
import os
import re
import threading
import time
from concurrent.futures import Future

# Tried in this order. To force a specific one first, set GEMINI_MODEL
# (run `python main.py --models` to see the names your key can use).
DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.7-flash", "gemini-2.5-flash"]
DEBUG = os.environ.get("BASELINE_DEBUG") == "1"

TIMEOUT_MS = 60_000   # give up on one request after 60 seconds instead of waiting forever
COOL_SECONDS = 90     # after a model fails (busy or slow), avoid it for this long

_WORKING = {}  # model name -> which generation settings worked last time (saves a failed call)
_COOL = {}     # model name -> time until which we skip it


def model_names():
    chosen = os.environ.get("GEMINI_MODEL")
    if chosen:
        return [chosen] + [m for m in DEFAULT_MODELS if m != chosen]
    return list(DEFAULT_MODELS)


def _client():
    # Imported here so the rest of the app can be tested without the SDK installed.
    from google import genai

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit(
            "GEMINI_API_KEY is not set.\n"
            "Create a free key in Google AI Studio, then set it as an environment variable "
            "(never paste it into the code)."
        )
    try:
        from google.genai import types

        return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    except Exception:  # an older library version without the timeout option
        return genai.Client(api_key=key)


def list_models():
    """Print the models your key can use, so you can choose a valid GEMINI_MODEL."""
    for m in _client().models.list():
        print(m.name)


def _extract_json(text):
    """Parse JSON from the model's reply, tolerating stray code fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


def _settings(types):
    """Generation settings to try, fastest first.

    Newer models "think" before answering, which is slow and not needed here. We ask for low
    thinking first, then no thinking, then plain settings, because different models accept
    different options.
    """
    base = {"response_mime_type": "application/json", "temperature": 0.4}
    found = []
    for thinking in ({"thinking_level": "low"}, {"thinking_budget": 0}, None):
        try:
            extra = {"thinking_config": types.ThinkingConfig(**thinking)} if thinking else {}
            found.append(types.GenerateContentConfig(**base, **extra))
        except Exception:
            continue  # this version of the library does not know that option
    return found


def ask_json(prompt, retries=1, quiet=False):
    """Send a prompt, get parsed JSON back.

    If a model is busy, slow (over 60 seconds) or missing, we switch to the next model straight
    away and avoid the failing one for a while. We go through all models twice before giving up.
    Broken JSON is retried on the same model. quiet=True hides progress notes (used for
    background work so it does not clutter the screen).
    """
    from google.genai import types

    client = _client()
    settings = _settings(types)
    last_error = None
    for round_number in range(2):
        now = time.monotonic()
        models = [m for m in model_names() if _COOL.get(m, 0) <= now] or model_names()
        for model in models:
            index = min(_WORKING.get(model, 0), len(settings) - 1)
            json_tries = 0
            while True:
                started = time.monotonic()
                try:
                    response = client.models.generate_content(
                        model=model, contents=prompt, config=settings[index]
                    )
                    data = _extract_json(response.text)
                    _WORKING[model] = index
                    if DEBUG:
                        print(f"  [debug] {model} answered in {time.monotonic() - started:.1f}s")
                    return data
                except json.JSONDecodeError as e:
                    last_error = e  # broken JSON: ask the same model again
                    json_tries += 1
                    if json_tries > retries:
                        break
                except Exception as e:  # busy, slow, rate limited, unknown model...
                    last_error = e
                    msg = str(e)
                    if "404" in msg or "NOT_FOUND" in msg:
                        _COOL[model] = time.monotonic() + 3600  # not available to this key
                        break
                    if ("thinking" in msg.lower() or "INVALID_ARGUMENT" in msg) and index < len(settings) - 1:
                        index += 1  # this model dislikes that setting: try a plainer one
                        continue
                    _COOL[model] = time.monotonic() + COOL_SECONDS
                    if DEBUG:
                        print(f"  [debug] {model} failed after {time.monotonic() - started:.1f}s: {msg[:120]}")
                    if not quiet:
                        print(f"  ({model} is busy or slow, switching to another model...)")
                    break
        if round_number == 0:
            time.sleep(4)  # everything failed once: rest briefly, then go round again
    raise RuntimeError(
        f"The AI could not answer: {last_error}\n"
        "Google's models may be overloaded right now. Wait a minute and run the app again, "
        "or try another model: python main.py --models, then set GEMINI_MODEL."
    )


def background(fn, *args, **kwargs):
    """Run fn(*args) in a background thread. Returns a Future: .done() and .result()."""
    future = Future()

    def run():
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as e:  # hand any error to whoever asks for the result
            future.set_exception(e)

    threading.Thread(target=run, daemon=True).start()
    return future
