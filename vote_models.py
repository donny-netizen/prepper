#!/usr/bin/env python3
"""Call three free models, then judgement.py. Used by GitHub Actions only."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import judgement

CARDS = judgement.SUPPLY_CARDS
MODELS = ("groq", "gemini", "openrouter")
PROMPT = (
    "You judge emergency-supply readiness cards. Reply with exactly three lines, "
    "one per card in this order: Food, Water storage, Power. Each line must be "
    "only one word: bump, hold, or lower. No other text.\n\nDay bundle:\n"
)


def _http(url, headers=None, body=None, timeout=90, retries=1):
    data = None if body is None else json.dumps(body).encode()
    hdrs = {"User-Agent": "PreppingJudge/1.0 (+https://github.com/donny-netizen/prepper)", "Accept": "application/json"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:300]
            raise RuntimeError("HTTP %s for %s: %s" % (err.code, url.split("?")[0], detail)) from None
        except (TimeoutError, urllib.error.URLError) as err:
            last = err
            if attempt >= retries:
                break
    raise RuntimeError("Timed out calling %s: %s" % (url.split("?")[0], last)) from None


def _parse_votes(text: str) -> dict:
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    words = []
    for line in lines:
        match = re.search(r"\b(bump|hold|lower)\b", line)
        if match:
            words.append(match.group(1))
    if len(words) < 3:
        raise RuntimeError("model did not return three votes: %r" % (text[:200],))
    return {card: words[i] for i, card in enumerate(CARDS)}


def vote_groq(day: dict) -> dict:
    key = os.environ["GROQ_API_KEY"]
    payload = {
        # Groq free tier retired llama-3.1-8b-instant; gpt-oss-20b is the free replacement.
        "model": "openai/gpt-oss-20b",
        "temperature": 0,
        "messages": [{"role": "user", "content": PROMPT + json.dumps(day, separators=(",", ":"))}],
    }
    result = _http(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        body=payload,
    )
    return _parse_votes(result["choices"][0]["message"]["content"])


def vote_gemini(day: dict) -> dict:
    key = os.environ["GEMINI_API_KEY"]
    payload = {
        "contents": [{"parts": [{"text": PROMPT + json.dumps(day, separators=(",", ":"))}]}],
        "generationConfig": {"temperature": 0},
    }
    result = _http(
        # Google now routes new free keys to gemini-3.8-flash.
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key=" + key,
        headers={"Content-Type": "application/json"},
        body=payload,
        timeout=120,
        retries=1,
    )
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_votes(text)


def vote_openrouter(day: dict) -> dict:
    key = os.environ["OPENROUTER_API_KEY"]
    payload = {
        "model": "meta-llama/llama-3.1-8b-instruct:free",
        "temperature": 0,
        "messages": [{"role": "user", "content": PROMPT + json.dumps(day, separators=(",", ":"))}],
    }
    result = _http(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/donny-netizen/prepper",
            "X-Title": "prepper daily judge",
        },
        body=payload,
    )
    return _parse_votes(result["choices"][0]["message"]["content"])


def collect_votes(day: dict) -> dict:
    callers = {"groq": vote_groq, "gemini": vote_gemini, "openrouter": vote_openrouter}
    by_model = {}
    for name in MODELS:
        by_model[name] = callers[name](day)
    votes = {}
    for card in CARDS:
        votes[card] = {name: by_model[name][card] for name in MODELS}
    return votes


def hub_get(path: str):
    base = os.environ["PREPPING_HUB_URL"].rstrip("/")
    token = os.environ["PREPPING_HUB_TOKEN"].strip()
    if not base or not token:
        raise RuntimeError("PREPPING_HUB_URL and PREPPING_HUB_TOKEN are required")
    return _http(
        base + path,
        headers={"Authorization": "Bearer " + token},
        timeout=60,
    )


def hub_post(path: str, body: dict):
    base = os.environ["PREPPING_HUB_URL"].rstrip("/")
    token = os.environ["PREPPING_HUB_TOKEN"].strip()
    if not base or not token:
        raise RuntimeError("PREPPING_HUB_URL and PREPPING_HUB_TOKEN are required")
    return _http(
        base + path,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        body=body,
        timeout=60,
    )

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    day_path = Path("day.json")
    if "--local-day" in argv:
        day = json.loads(day_path.read_text())
    else:
        day = hub_get("/v1/judge-day")
        day_path.write_text(json.dumps(day, indent=2) + "\n")
    day.setdefault("tie_break", {"Food": "groq", "Water storage": "gemini", "Power": "openrouter"})
    if "votes" not in day:
        day["votes"] = collect_votes(day)
    result = judgement.judge(day)
    result["votes"] = day["votes"]
    result["run_on"] = (day.get("now") or "")[:10]
    Path("judgement-result.json").write_text(json.dumps(result, indent=2) + "\n")
    if dry or result.get("skipped"):
        print(json.dumps({"posted": False, "result": result}, indent=2))
        return 0
    posted = hub_post("/v1/judge-result", result)
    print(json.dumps({"posted": True, "hub": posted, "notify": result.get("notify")}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, KeyError, RuntimeError, judgement.JudgementError) as err:
        print(json.dumps({"error": str(err)}), file=sys.stderr)
        raise SystemExit(1)
