"""Daily card judgement for the supply watch.

Three supply cards (Food, Water storage, Power) each take three votes:
bump, hold, or lower. Two or three agreeing sets the card. A 1-1-1 split
is broken by the model prechosen for that card. The split does not become
a hold.

Ammo is one built-in reading, not a second vote. It covers five Caliber
King series only, uses the full history, and returns one of: continued
rise, spike, flat, falling, unknown. Supply votes are not recomputed from
that label.

Slack text is returned only when a supply card changes, the ammo label
changes, or a store is still waiting on Chrome. A quiet day returns none.
This module does not send the message.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, time, timezone

SUPPLY_CARDS = ("Food", "Water storage", "Power")
VOTES = ("bump", "hold", "lower")
CALIBER_KING_SERIES = ("9mm", "12 gauge", ".22 LR", ".45 ACP", "5.56x45")
CALIBER_KING_PAGE = "https://www.caliberking.com/ammo-prices"
AMMO_LABELS = ("continued rise", "spike", "flat", "falling", "unknown")
AMMO_CARD_PRIORITY = ("spike", "continued rise", "falling", "flat")

COLLECTOR_UTC = time(14, 0)
MIN_POINTS = 12
LONG_MOVE = 0.08
SHARP_JUMP = 0.15


class JudgementError(ValueError):
    """The day's inputs do not match the decision contract."""


def run_is_due(now: datetime, last_run_on: date | None) -> bool:
    """True once per UTC date, only after the 14:00 UTC collector."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise JudgementError("now must be timezone-aware")
    current = now.astimezone(timezone.utc)
    if current.timetz().replace(tzinfo=None) <= COLLECTOR_UTC:
        return False
    return last_run_on != current.date()


def resolve_supply_card(votes: dict, tie_break_model: str) -> dict:
    """Set one card from exactly three model votes."""
    if not isinstance(votes, dict) or len(votes) != 3:
        raise JudgementError("a supply card needs exactly three model votes")
    if any(choice not in VOTES for choice in votes.values()):
        raise JudgementError("votes must be bump, hold, or lower")
    tally = {choice: 0 for choice in VOTES}
    for choice in votes.values():
        tally[choice] += 1
    winners = [choice for choice, count in tally.items() if count >= 2]
    if winners:
        return {"vote": winners[0], "tally": tally, "tie_break_applied": False}
    if tie_break_model not in votes:
        raise JudgementError("a 1-1-1 split needs the prechosen model for this card")
    return {"vote": votes[tie_break_model], "tally": tally, "tie_break_applied": True}


def classify_series(prices, complete: bool) -> str:
    """Read one full Caliber King history.

    Continued rise means the history before the recent stretch slopes up.
    A spike is a sharp jump in that recent stretch while the longer history
    is flat. Falling and flat are read from the same longer history. A
    missing, partial, or unreadable series is unknown.
    """
    if complete is not True or not isinstance(prices, (list, tuple)):
        return "unknown"
    if len(prices) < MIN_POINTS:
        return "unknown"
    try:
        series = tuple(float(point) for point in prices)
    except (TypeError, ValueError):
        return "unknown"
    if any(not math.isfinite(point) or point <= 0 for point in series):
        return "unknown"

    recent_n = max(3, len(series) // 10)
    longer = series[:-recent_n]
    recent = series[-recent_n:]
    if len(longer) < 9:
        return "unknown"
    long_change = _third_change(longer)
    if long_change is None:
        return "unknown"
    if long_change >= LONG_MOVE:
        return "continued rise"
    baseline = _median(longer)
    if baseline <= 0:
        return "unknown"
    recent_jump = (_median(recent) - baseline) / baseline
    if recent_jump >= SHARP_JUMP and abs(long_change) < LONG_MOVE:
        return "spike"
    if long_change <= -LONG_MOVE:
        return "falling"
    return "flat"


def read_ammo(series: dict) -> dict:
    """Label the five Caliber King series and roll them into one card label."""
    if not isinstance(series, dict):
        raise JudgementError("ammo history must be an object of the five series")
    extra = set(series) - set(CALIBER_KING_SERIES)
    if extra:
        raise JudgementError("ammo is limited to the five Caliber King series")
    labels = {}
    for name in CALIBER_KING_SERIES:
        entry = series.get(name)
        if not isinstance(entry, dict):
            labels[name] = "unknown"
            continue
        labels[name] = classify_series(entry.get("prices"), entry.get("complete") is True)
    return {
        "source": CALIBER_KING_PAGE,
        "series": labels,
        "label": _ammo_card_label(labels),
    }


def slack_text(previous: dict | None, cards: dict, ammo_label: str, chrome_waiting) -> str | None:
    """Text to send, or None on a quiet day. No webhook and no delivery."""
    if ammo_label not in AMMO_LABELS:
        raise JudgementError("ammo label is not one of the five readings")
    waiting = _waiting_stores(chrome_waiting)
    lines = []
    prior_cards = previous.get("cards", {}) if isinstance(previous, dict) else None
    if isinstance(prior_cards, dict):
        for card in SUPPLY_CARDS:
            before = prior_cards.get(card)
            after = cards[card]["vote"]
            if before != after:
                lines.append(f"{card}: {before or 'unset'} -> {after}")
        before_ammo = previous.get("ammo_label") if isinstance(previous, dict) else None
        if before_ammo != ammo_label:
            lines.append(f"Ammo: {before_ammo or 'unset'} -> {ammo_label}")
    if waiting:
        lines.append("Still waiting on Chrome: " + ", ".join(waiting))
    if not lines:
        return None
    return "Supply watch\n" + "\n".join(lines)


def judge(day: dict) -> dict:
    """Judge one daily input. Skips when the collector slot has not passed."""
    if not isinstance(day, dict):
        raise JudgementError("day input must be an object")
    now = _parse_time(day.get("now"))
    last_run_on = _parse_date(day.get("last_run_on"))
    if not run_is_due(now, last_run_on):
        return {
            "skipped": True,
            "reason": "before the 14:00 UTC collector, or this UTC date was already judged",
            "notify": False,
            "slack": None,
        }

    tie_break = day.get("tie_break")
    votes = day.get("votes")
    if not isinstance(tie_break, dict) or not isinstance(votes, dict):
        raise JudgementError("votes and tie_break are required")
    cards = {}
    for card in SUPPLY_CARDS:
        cards[card] = resolve_supply_card(votes.get(card), tie_break.get(card))
    ammo = read_ammo(day.get("ammo") or {})
    text = slack_text(day.get("previous"), cards, ammo["label"], day.get("chrome_waiting") or [])
    return {
        "skipped": False,
        "schedule": "once each UTC date, after 14:00 UTC",
        "cards": cards,
        "ammo": ammo,
        "notify": text is not None,
        "slack": text,
    }


def _ammo_card_label(labels: dict) -> str:
    present = set(labels.values())
    for label in AMMO_CARD_PRIORITY:
        if label in present:
            return label
    return "unknown"


def _waiting_stores(stores) -> list:
    if stores is None:
        return []
    if not isinstance(stores, (list, tuple)):
        raise JudgementError("chrome_waiting must be a list of store ids")
    names = []
    for store in stores:
        if not isinstance(store, str):
            raise JudgementError("a waiting store id must be text")
        name = " ".join(store.split())
        if not name or "://" in name or "http" in name.lower() or "webhook" in name.lower():
            raise JudgementError("a waiting store id cannot carry a URL")
        names.append(name)
    return names


def _median(values) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _third_change(prices) -> float | None:
    third = max(1, len(prices) // 3)
    start = _median(prices[:third])
    end = _median(prices[-third:])
    if start <= 0:
        return None
    return (end - start) / start


def _parse_time(value) -> datetime:
    if not isinstance(value, str):
        raise JudgementError("now must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JudgementError("now must be an ISO-8601 UTC timestamp") from exc
    return parsed


def _parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise JudgementError("last_run_on must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise JudgementError("last_run_on must be YYYY-MM-DD") from exc


def _self_check() -> None:
    assert run_is_due(datetime(2026, 11, 25, 13, 59, tzinfo=timezone.utc), None) is False
    assert run_is_due(datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc), None) is False
    assert run_is_due(datetime(2026, 11, 25, 14, 1, tzinfo=timezone.utc), None) is True
    assert run_is_due(datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc), date(2026, 11, 25)) is False
    assert run_is_due(datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc), date(2026, 11, 24)) is True

    agreed = resolve_supply_card({"a": "hold", "b": "hold", "c": "bump"}, "a")
    assert agreed["vote"] == "hold" and agreed["tie_break_applied"] is False
    unanimous = resolve_supply_card({"a": "lower", "b": "lower", "c": "lower"}, "b")
    assert unanimous["vote"] == "lower" and unanimous["tie_break_applied"] is False
    split = resolve_supply_card({"a": "bump", "b": "hold", "c": "lower"}, "c")
    assert split["vote"] == "lower" and split["tie_break_applied"] is True
    try:
        resolve_supply_card({"a": "bump", "b": "hold", "c": "lower"}, "missing")
    except JudgementError:
        pass
    else:
        raise AssertionError("a 1-1-1 split must not default to hold")

    rise = [1 + i * 0.01 for i in range(40)]
    assert classify_series(rise, True) == "continued rise"
    spike = [1.0] * 40 + [1.25] * 4
    assert classify_series(spike, True) == "spike"
    flat = [1.0, 1.01, 0.99] * 16
    assert classify_series(flat, True) == "flat"
    falling = [1.5 - i * 0.01 for i in range(40)]
    assert classify_series(falling, True) == "falling"
    assert classify_series(rise[:8], True) == "unknown"
    assert classify_series(rise, False) == "unknown"
    assert classify_series([None] * 20, True) == "unknown"

    ammo = read_ammo({
        "9mm": {"complete": True, "prices": spike},
        "12 gauge": {"complete": True, "prices": flat},
        ".22 LR": {"complete": False, "prices": rise},
        ".45 ACP": {"complete": True, "prices": falling},
    })
    assert ammo["series"]["9mm"] == "spike"
    assert ammo["series"]["5.56x45"] == "unknown"
    assert ammo["series"][".22 LR"] == "unknown"
    assert ammo["label"] == "spike"
    try:
        read_ammo({".300 BLK": {"complete": True, "prices": flat}})
    except JudgementError:
        pass
    else:
        raise AssertionError("ammo must stay on the five series")

    previous = {
        "cards": {"Food": "hold", "Water storage": "hold", "Power": "hold"},
        "ammo_label": "flat",
    }
    same = {card: {"vote": "hold"} for card in SUPPLY_CARDS}
    assert slack_text(previous, same, "flat", []) is None
    changed = slack_text(previous, {**same, "Food": {"vote": "bump"}}, "spike", ["store-7"])
    assert changed is not None
    assert "Food: hold -> bump" in changed
    assert "Ammo: flat -> spike" in changed
    assert "store-7" in changed
    assert "http" not in changed.lower()
    assert slack_text(None, {card: {"vote": "bump"} for card in SUPPLY_CARDS}, "spike", []) is None
    assert slack_text(None, same, "flat", ["store-7"]) == "Supply watch\nStill waiting on Chrome: store-7"

    quiet = judge({
        "now": "2026-11-25T15:00:00Z",
        "last_run_on": "2026-11-24",
        "tie_break": {"Food": "a", "Water storage": "a", "Power": "a"},
        "votes": {card: {"a": "hold", "b": "hold", "c": "hold"} for card in SUPPLY_CARDS},
        "ammo": {name: {"complete": True, "prices": flat} for name in CALIBER_KING_SERIES},
        "previous": previous,
        "chrome_waiting": [],
    })
    assert quiet["skipped"] is False and quiet["notify"] is False and quiet["slack"] is None
    early = judge({"now": "2026-11-25T14:00:00Z", "last_run_on": None})
    assert early["skipped"] is True and early["slack"] is None


def main(argv: list[str]) -> int:
    if argv == ["--self-check"]:
        _self_check()
        print("ok")
        return 0
    if len(argv) != 1:
        print("usage: python3 judgement.py day.json", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as handle:
        day = json.load(handle)
    json.dump(judge(day), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except JudgementError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
