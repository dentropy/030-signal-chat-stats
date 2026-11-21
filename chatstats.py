#!/usr/bin/env python3
"""Statistics for a sigexport Signal chat file.

The input is NDJSON, or a JSON array, of Message objects written by
carderne/signal-export. Each record is one message. This script totals
the fields and writes a single self-contained HTML report. Print that
page to PDF if you want a file to keep.

    python3 chatstats.py sample
    python3 chatstats.py report testdata/sample.ndjson -o out/sample-report.html
"""

from __future__ import annotations

import argparse
import html
import json
import math
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_SAMPLE = ROOT / "testdata" / "sample.ndjson"
DEFAULT_EXAMPLES = ROOT / "testdata" / "examples.json"

FIELDS = (
    "date",
    "sender",
    "body",
    "quote",
    "sticker",
    "reactions",
    "attachments",
    "deleted",
    "call",
    "missed",
)

PALETTE = ("#1f6b4a", "#c46b2c", "#2c5282", "#8a3d5b", "#5b6b2a", "#6b4c7a")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MEDIA_QUOTES = {"photo", "video", "voice message", "attachment"}

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".svg", ".apng"}
AUDIO_EXT = {".m4a", ".aac", ".mp3", ".ogg", ".opus", ".wav", ".flac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".mpeg", ".qt"}

STOP = {
    "a", "an", "the", "and", "or", "but", "if", "so", "to", "of", "in", "on",
    "for", "with", "at", "by", "from", "is", "it", "be", "as", "was", "were",
    "are", "am", "this", "that", "these", "those", "i", "you", "we", "they",
    "he", "she", "me", "my", "your", "our", "their", "do", "did", "does",
    "just", "not", "no", "have", "has", "had", "will", "can", "could", "about",
    "what", "when", "how", "who", "got", "get", "its", "it's", "i'm", "im",
    "don't", "dont", "that's", "there's", "we're", "you're", "let", "lets",
    "let's", "up", "out", "into", "over", "than", "then", "too", "very",
    "really", "of", "off", "as", "at", "be", "we", "ll", "re", "ve",
}

LINK_RE = re.compile(r"https?://\S+")
TOKEN_RE = re.compile(r"[a-zA-Z']{2,}")
CALL_DUR_RE = re.compile(r"accepted, (?:(\d+)m )?(\d+)s")

# A session breaks when the next message arrives at least this long after
# the previous one. Replies are speaker changes inside a session.
DEFAULT_GAP_MINUTES = 30


def parse_date(value: object) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"bad date {value!r}") from exc
    else:
        raise ValueError(f"bad date {value!r}")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_reactions(value: object) -> list[tuple[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("reactions is not an array")
    out: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            emoji = str(item.get("emoji") or "")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            name = str(item[0])
            emoji = str(item[1])
        else:
            raise ValueError("reaction is not a [name, emoji] pair")
        out.append((name, emoji))
    return out


def parse_sticker(value: object) -> dict | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return {"id": "", "packId": "", "packKey": "", "emoji": value, "extension": None}
    if not isinstance(value, dict):
        raise ValueError("sticker is not an object")
    extension = value.get("extension")
    return {
        "id": str(value.get("id") or value.get("stickerId") or ""),
        "packId": str(value.get("packId") or ""),
        "packKey": str(value.get("packKey") or ""),
        "emoji": str(value.get("emoji") or ""),
        "extension": None if extension is None else str(extension),
    }


def parse_attachments(value: object) -> list[dict]:
    if not value:
        return []
    if not isinstance(value, list):
        raise ValueError("attachments is not an array")
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("attachment is not an object")
        missing = item.get("missing_kind")
        out.append(
            {
                "name": str(item.get("name") or ""),
                "path": str(item.get("path") or ""),
                "missing_kind": None if missing is None else str(missing),
            }
        )
    return out


def normalize(obj: object) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("expected an object")
    if "date" not in obj:
        raise ValueError("missing date")
    sender = obj.get("sender")
    if sender is None or sender == "":
        sender = "No-Sender"
    return {
        "date": parse_date(obj["date"]),
        "sender": str(sender),
        "body": str(obj.get("body") or "").strip(),
        "quote": str(obj.get("quote") or ""),
        "sticker": parse_sticker(obj.get("sticker")),
        "reactions": parse_reactions(obj.get("reactions") or []),
        "attachments": parse_attachments(obj.get("attachments") or []),
        "deleted": bool(obj.get("deleted")),
        "call": bool(obj.get("call")),
        "missed": bool(obj.get("missed")),
    }


def parse_text(text: str) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    stripped = text.lstrip("\ufeff \t\r\n")
    raw_items: list[tuple[int, object]] = []
    if not stripped:
        return [], []
    if stripped[0] == "[":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return [], [f"JSON array: {exc.msg}"]
        if not isinstance(data, list):
            return [], ["JSON root is not an array"]
        raw_items = list(enumerate(data, 1))
    else:
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw_items.append((i, json.loads(line)))
            except json.JSONDecodeError as exc:
                errors.append(f"record {i}: {exc.msg}")
    messages = []
    for i, item in raw_items:
        try:
            messages.append(normalize(item))
        except ValueError as exc:
            errors.append(f"record {i}: {exc}")
    return messages, errors


def norm_text(value: str) -> str:
    lines = []
    for line in (value or "").splitlines():
        line = line.strip()
        if line.startswith(">"):
            line = line[1:].strip()
        if line:
            lines.append(line)
    if not lines and value:
        return " ".join(value.split())
    return " ".join(lines)


def quote_kind(quote: str) -> str:
    text = norm_text(quote)
    if not text:
        return ""
    if text.casefold() in MEDIA_QUOTES:
        return "media"
    return "text"


def tokens(body: str) -> list[str]:
    text = LINK_RE.sub(" ", body.replace("’", "'"))
    return [tok.lower() for tok in TOKEN_RE.findall(text)]


def word_count(body: str) -> int:
    return len(re.findall(r"\S+", body.strip()))


def clip(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def display_body(message: dict) -> str:
    text = message["body"].strip()
    if text:
        return text
    sticker = message.get("sticker")
    if sticker:
        return f"[{sticker.get('emoji') or 'sticker'} sticker]"
    if message.get("attachments"):
        return "[attachment]"
    if message.get("call"):
        return "[call]"
    if message.get("deleted"):
        return "[deleted]"
    return ""


def parse_call_seconds(body: str) -> int | None:
    match = CALL_DUR_RE.search(body)
    if not match:
        return None
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2))
    return minutes * 60 + seconds


def file_kind(attachment: dict) -> str:
    if attachment.get("missing_kind"):
        return "missing"
    suffix = Path(attachment.get("name") or attachment.get("path") or "").suffix.lower()
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in AUDIO_EXT:
        return "audio"
    if suffix in VIDEO_EXT:
        return "video"
    return "other"


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    idx = q * (len(xs) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(xs[lo])
    frac = idx - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    whole = int(round(seconds))
    if whole < 60:
        return f"{whole}s"
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        if secs and minutes < 10:
            return f"{minutes}m {secs}s"
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def num(value: float | int) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def pct(part: float, whole: float) -> str:
    if not whole:
        return "0%"
    return f"{100 * part / whole:.1f}%"


def human_date(dt: datetime) -> str:
    return f"{dt.day} {dt.strftime('%b')} {dt.year}"


def human_dt(dt: datetime) -> str:
    return f"{human_date(dt)}, {dt:%H:%M}"


def is_late(dt: datetime) -> bool:
    return dt.hour >= 23 or dt.hour < 6


def is_conversational(message: dict) -> bool:
    return not message["call"] and not message["deleted"]


def month_labels(start: datetime, end: datetime) -> list[str]:
    year, month = start.year, start.month
    labels = []
    while (year, month) <= (end.year, end.month):
        labels.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return labels


def empty_person() -> dict:
    return {
        "messages": 0,
        "talk": 0,
        "words": 0,
        "chars": 0,
        "questions": 0,
        "links": 0,
        "media": 0,
        "stickers": 0,
        "calls": 0,
        "missed": 0,
        "deleted": 0,
        "reactions_given": 0,
        "reactions_received": 0,
        "quotes": 0,
        "quoted": 0,
        "sessions_started": 0,
        "sessions_ended": 0,
        "double_texts": 0,
        "longest_streak": 0,
        "late": 0,
        "weekend": 0,
        "hours": [0] * 24,
        "reply_seconds": [],
        "tokens": 0,
        "types": set(),
        "first": None,
        "last": None,
        "emoji_received": Counter(),
        "word_counts": Counter(),
    }


def analyze(messages: list[dict], *, gap_minutes: int = DEFAULT_GAP_MINUTES, errors: list[str] | None = None) -> dict:
    errors = list(errors or [])
    messages = sorted(messages, key=lambda m: m["date"])
    gap = timedelta(minutes=gap_minutes)
    if not messages:
        return {
            "gap_minutes": gap_minutes,
            "errors": errors,
            "overview": {"messages": 0},
            "senders": [],
        }

    people: dict[str, dict] = defaultdict(empty_person)
    global_words: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    repeated: Counter[str] = Counter()
    hours = [0] * 24
    weekdays = [0] * 7
    heatmap = [[0] * 24 for _ in range(7)]
    day_counts: Counter[str] = Counter()
    month_counts: Counter[str] = Counter()
    emoji_counts: Counter[str] = Counter()
    reaction_matrix: dict[str, Counter[str]] = defaultdict(Counter)
    kind_counts: Counter[str] = Counter()
    missing_kinds: Counter[str] = Counter()
    sticker_counts: Counter[str] = Counter()
    call_seconds = []
    incoming = outgoing = 0

    for message in messages:
        who = message["sender"]
        person = people[who]
        person["messages"] += 1
        person["first"] = person["first"] or message["date"]
        person["last"] = message["date"]
        hour = message["date"].hour
        weekday = message["date"].weekday()
        hours[hour] += 1
        weekdays[weekday] += 1
        heatmap[weekday][hour] += 1
        day_counts[message["date"].date().isoformat()] += 1
        month_counts[message["date"].strftime("%Y-%m")] += 1
        person["hours"][hour] += 1
        if is_late(message["date"]):
            person["late"] += 1
        if weekday >= 5:
            person["weekend"] += 1
        if message["deleted"]:
            person["deleted"] += 1
        if message["call"]:
            person["calls"] += 1
            if message["missed"]:
                person["missed"] += 1
            body = message["body"]
            if body.startswith("Incoming") or body.startswith("Missed"):
                incoming += 1
            elif body.startswith("Outgoing"):
                outgoing += 1
            duration = parse_call_seconds(body)
            if duration is not None:
                call_seconds.append(duration)
        if message["sticker"]:
            person["stickers"] += 1
            sticker_counts[message["sticker"].get("emoji") or "sticker"] += 1
        for attachment in message["attachments"]:
            person["media"] += 1
            kind_counts[file_kind(attachment)] += 1
            if attachment.get("missing_kind"):
                missing_kinds[attachment["missing_kind"]] += 1
        for reactor, emoji in message["reactions"]:
            emoji_counts[emoji or "?"] += 1
            people[reactor]["reactions_given"] += 1
            person["reactions_received"] += 1
            person["emoji_received"][emoji or "?"] += 1
            reaction_matrix[reactor][who] += 1
        if is_conversational(message):
            body = message["body"]
            person["talk"] += 1
            person["words"] += word_count(body)
            person["chars"] += len(body)
            if "?" in body:
                person["questions"] += 1
            person["links"] += len(LINK_RE.findall(body))
            toks = tokens(body)
            person["tokens"] += len(toks)
            person["types"].update(toks)
            content = [(index, tok) for index, tok in enumerate(toks) if tok not in STOP]
            for _, tok in content:
                person["word_counts"][tok] += 1
                global_words[tok] += 1
            for (left_at, left), (right_at, right) in zip(content, content[1:]):
                if right_at == left_at + 1:
                    bigrams[f"{left} {right}"] += 1
            key = norm_text(body)
            if len(key) >= 2:
                repeated[key] += 1
            if quote_kind(message["quote"]):
                person["quotes"] += 1

    sessions: list[list[dict]] = []
    current = [messages[0]]
    for previous, message in zip(messages, messages[1:]):
        if message["date"] - previous["date"] >= gap:
            sessions.append(current)
            current = [message]
        else:
            current.append(message)
    sessions.append(current)

    reply_seconds: list[float] = []
    pair_seconds: dict[tuple[str, str], list[float]] = defaultdict(list)
    fastest = None
    slowest = None
    session_lengths = []
    multi_durations = []
    longest_messages = None
    longest_duration = None

    for session in sessions:
        session_lengths.append(len(session))
        people[session[0]["sender"]]["sessions_started"] += 1
        people[session[-1]["sender"]]["sessions_ended"] += 1
        span = (session[-1]["date"] - session[0]["date"]).total_seconds()
        summary = {
            "messages": len(session),
            "seconds": span,
            "start": session[0]["date"],
            "end": session[-1]["date"],
        }
        if longest_messages is None or len(session) > longest_messages["messages"]:
            longest_messages = summary
        if longest_duration is None or span > longest_duration["seconds"]:
            longest_duration = summary
        if len(session) >= 2:
            multi_durations.append(span)

        prev_talk = None
        run_sender = None
        run = 0
        for message in session:
            if not is_conversational(message):
                prev_talk = None
                run_sender = None
                run = 0
                continue
            who = message["sender"]
            if run_sender == who:
                run += 1
                people[who]["double_texts"] += 1
            else:
                run_sender = who
                run = 1
            people[who]["longest_streak"] = max(people[who]["longest_streak"], run)
            if prev_talk is not None and prev_talk["sender"] != who:
                seconds = (message["date"] - prev_talk["date"]).total_seconds()
                if seconds >= 0:
                    reply_seconds.append(seconds)
                    people[who]["reply_seconds"].append(seconds)
                    pair_seconds[(prev_talk["sender"], who)].append(seconds)
                    record = {
                        "seconds": seconds,
                        "from": prev_talk["sender"],
                        "to": who,
                        "prompt": clip(display_body(prev_talk)),
                        "reply": clip(display_body(message)),
                        "at": message["date"],
                    }
                    if record["reply"]:
                        if fastest is None or seconds < fastest["seconds"]:
                            fastest = record
                        if slowest is None or seconds > slowest["seconds"]:
                            slowest = record
            prev_talk = message

    silences = []
    for previous, message in zip(messages, messages[1:]):
        seconds = (message["date"] - previous["date"]).total_seconds()
        if seconds <= 0:
            continue
        silences.append(
            {
                "seconds": seconds,
                "start": previous["date"],
                "end": message["date"],
                "before": previous["sender"],
                "after": message["sender"],
            }
        )
    silences.sort(key=lambda item: item["seconds"], reverse=True)

    last_body: dict[str, dict] = {}
    quote_examples = []
    matched = unmatched = media_quotes = 0
    quoted_counts: Counter[str] = Counter()
    for message in messages:
        kind = quote_kind(message["quote"])
        if kind == "media":
            media_quotes += 1
        elif kind == "text":
            key = norm_text(message["quote"])
            previous = last_body.get(key)
            if previous:
                matched += 1
                quoted_counts[previous["sender"]] += 1
                quote_examples.append(
                    {
                        "by": message["sender"],
                        "target": previous["sender"],
                        "quote": clip(key),
                        "reply": clip(display_body(message)),
                        "at": message["date"],
                    }
                )
            else:
                unmatched += 1
        if is_conversational(message):
            key = norm_text(message["body"])
            if key:
                last_body[key] = message

    for name, count in quoted_counts.items():
        people[name]["quoted"] += count

    senders = []
    for name, person in people.items():
        replies = person["reply_seconds"]
        senders.append(
            {
                "name": name,
                "messages": person["messages"],
                "talk": person["talk"],
                "words": person["words"],
                "chars": person["chars"],
                "avg_words": (person["words"] / person["talk"]) if person["talk"] else 0,
                "questions": person["questions"],
                "links": person["links"],
                "media": person["media"],
                "stickers": person["stickers"],
                "calls": person["calls"],
                "missed": person["missed"],
                "deleted": person["deleted"],
                "reactions_given": person["reactions_given"],
                "reactions_received": person["reactions_received"],
                "quotes": person["quotes"],
                "quoted": person["quoted"],
                "sessions_started": person["sessions_started"],
                "sessions_ended": person["sessions_ended"],
                "double_texts": person["double_texts"],
                "longest_streak": person["longest_streak"],
                "late": person["late"],
                "weekend": person["weekend"],
                "hours": person["hours"],
                "reply_count": len(replies),
                "median_reply": median(replies),
                "tokens": person["tokens"],
                "types": len(person["types"]),
                "ttr": (len(person["types"]) / person["tokens"]) if person["tokens"] else 0,
                "first": person["first"],
                "last": person["last"],
                "top_emoji": person["emoji_received"].most_common(4),
                "distinctive": distinctive_words(person["word_counts"], global_words),
            }
        )
    senders.sort(key=lambda item: (-item["messages"], item["name"].casefold()))
    colors = {sender["name"]: PALETTE[i % len(PALETTE)] for i, sender in enumerate(senders)}

    start, end = messages[0]["date"], messages[-1]["date"]
    daily_unit, daily_points = daily_series(day_counts, start, end)
    peak_count, peak_day, peak_hour = max(
        (heatmap[day][hour], day, hour) for day in range(7) for hour in range(24)
    )
    busiest = sorted(
        ({"label": label, "count": count} for label, count in day_counts.items()),
        key=lambda item: (-item["count"], item["label"]),
    )[:5]

    talk_messages = sum(person["talk"] for person in people.values())
    words = sum(sender["words"] for sender in senders)
    reacted_messages = sum(1 for message in messages if message["reactions"])
    reaction_events = sum(emoji_counts.values())

    bucket_edges = [0, 30, 120, 600, 1800, 7200, 43200, math.inf]
    bucket_labels = ["< 30s", "30s–2m", "2–10m", "10–30m", "30m–2h", "2–12h", "> 12h"]
    gap_seconds = gap.total_seconds()
    buckets = []
    for index, label in enumerate(bucket_labels):
        if bucket_edges[index] >= gap_seconds:
            continue
        lo, hi = bucket_edges[index], bucket_edges[index + 1]
        buckets.append(
            {
                "label": label,
                "count": sum(1 for value in reply_seconds if lo <= value < hi),
            }
        )

    pairs = []
    for (source, target), values in pair_seconds.items():
        pairs.append(
            {
                "from": source,
                "to": target,
                "count": len(values),
                "median": median(values),
            }
        )
    pairs.sort(key=lambda item: (-item["count"], item["from"], item["to"]))

    top_reacted = sorted(
        (message for message in messages if message["reactions"]),
        key=lambda message: (-len(message["reactions"]), message["date"]),
    )[:8]

    return {
        "gap_minutes": gap_minutes,
        "errors": errors,
        "colors": colors,
        "overview": {
            "messages": len(messages),
            "people": len(senders),
            "start": start,
            "end": end,
            "span_days": (end.date() - start.date()).days + 1,
            "active_days": len(day_counts),
            "words": words,
            "chars": sum(sender["chars"] for sender in senders),
            "talk_messages": talk_messages,
            "sessions": len(sessions),
            "median_reply": median(reply_seconds),
            "p90_reply": quantile(reply_seconds, 0.9),
            "reply_count": len(reply_seconds),
            "reaction_events": reaction_events,
            "reacted_messages": reacted_messages,
            "calls": sum(sender["calls"] for sender in senders),
            "missed": sum(sender["missed"] for sender in senders),
            "deleted": sum(sender["deleted"] for sender in senders),
            "stickers": sum(sender["stickers"] for sender in senders),
            "attachments": sum(kind_counts.values()),
            "quotes": matched + unmatched + media_quotes,
            "questions": sum(sender["questions"] for sender in senders),
            "links": sum(sender["links"] for sender in senders),
            "peak": {"weekday": peak_day, "hour": peak_hour, "count": peak_count},
        },
        "senders": senders,
        "months": [
            {"label": label, "count": month_counts[label]}
            for label in month_labels(start, end)
        ],
        "weekdays": weekdays,
        "hours": hours,
        "heatmap": heatmap,
        "daily": {"unit": daily_unit, "points": daily_points},
        "sessions": {
            "count": len(sessions),
            "one_line": sum(1 for length in session_lengths if length == 1),
            "median_messages": median(session_lengths),
            "median_duration": median(multi_durations),
            "longest_messages": longest_messages,
            "longest_duration": longest_duration,
        },
        "latency": {
            "count": len(reply_seconds),
            "median": median(reply_seconds),
            "p90": quantile(reply_seconds, 0.9),
            "buckets": buckets,
            "pairs": pairs[:8],
            "fastest": fastest,
            "slowest": slowest,
        },
        "silences": silences[:5],
        "busiest_days": busiest,
        "reactions": {
            "events": reaction_events,
            "emojis": emoji_counts.most_common(12),
            "matrix_names": [sender["name"] for sender in senders[:8]],
            "matrix": reaction_matrix,
            "top_messages": [
                {
                    "at": message["date"],
                    "sender": message["sender"],
                    "body": clip(display_body(message)),
                    "reactions": message["reactions"],
                }
                for message in top_reacted
            ],
        },
        "quotes": {
            "count": matched + unmatched + media_quotes,
            "matched": matched,
            "unmatched": unmatched,
            "media": media_quotes,
            "targets": quoted_counts,
            "examples": evenly(quote_examples, 8),
        },
        "calls": {
            "count": sum(sender["calls"] for sender in senders),
            "missed": sum(sender["missed"] for sender in senders),
            "incoming": incoming,
            "outgoing": outgoing,
            "seconds": sum(call_seconds),
            "parsed": len(call_seconds),
            "median": median(call_seconds),
        },
        "media": {
            "kinds": kind_counts,
            "missing_kinds": missing_kinds,
            "stickers": sticker_counts.most_common(8),
        },
        "language": {
            "words": [item for item in global_words.most_common(40) if item[1] >= 3][:18],
            "bigrams": [item for item in bigrams.most_common(30) if item[1] >= 3][:12],
            "repeated": [item for item in repeated.most_common(30) if item[1] >= 4][:8],
        },
    }


def distinctive_words(personal: Counter[str], overall: Counter[str], limit: int = 6) -> list[tuple[str, int]]:
    scored = []
    for word, count in personal.items():
        if count < 2:
            continue
        others = overall[word] - count
        scored.append((count / (1 + others), count, word))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [(word, count) for _, count, word in scored[:limit]]


def daily_series(day_counts: Counter[str], start: datetime, end: datetime) -> tuple[str, list[dict]]:
    span = (end.date() - start.date()).days + 1
    if span > 420:
        counts: Counter[str] = Counter()
        for label, count in day_counts.items():
            day = datetime.fromisoformat(label).date()
            week = day - timedelta(days=day.weekday())
            counts[week.isoformat()] += count
        points = []
        cursor = start.date() - timedelta(days=start.weekday())
        last = end.date() - timedelta(days=end.weekday())
        while cursor <= last:
            points.append({"label": cursor.isoformat(), "count": counts[cursor.isoformat()]})
            cursor += timedelta(days=7)
        return "week", points

    points = []
    cursor = start.date()
    while cursor <= end.date():
        label = cursor.isoformat()
        points.append({"label": label, "count": day_counts[label]})
        cursor += timedelta(days=1)
    return "day", points


def evenly(items: list, count: int) -> list:
    if len(items) <= count:
        return items
    indexes = [round(i * (len(items) - 1) / (count - 1)) for i in range(count)]
    seen = set()
    picked = []
    for index in indexes:
        if index not in seen:
            seen.add(index)
            picked.append(items[index])
    return picked


def lede(stats: dict) -> list[str]:
    overview = stats["overview"]
    senders = stats["senders"]
    sentences = []
    names = ", ".join(sender["name"] for sender in senders[:4])
    if len(senders) > 4:
        names += f", and {len(senders) - 4} more"
    sentences.append(
        f"{num(overview['messages'])} messages from {names}, "
        f"{human_date(overview['start'])} to {human_date(overview['end'])} "
        f"({num(overview['span_days'])} days, {num(overview['active_days'])} of them with a message)."
    )
    if len(senders) >= 2 and overview["words"]:
        writer = max(senders, key=lambda sender: sender["words"])
        word_share = writer["words"] / overview["words"]
        message_share = writer["messages"] / overview["messages"]
        if abs(word_share - message_share) >= 0.05:
            sentences.append(
                f"{writer['name']} sent {pct(writer['messages'], overview['messages'])} of messages "
                f"and {pct(writer['words'], overview['words'])} of the words."
            )
    if overview["reply_count"]:
        quick = [
            sender
            for sender in senders
            if sender["reply_count"] >= 5 and sender["median_reply"] is not None
        ]
        sentence = (
            f"Inside a {stats['gap_minutes']}-minute session, the median reply is "
            f"{format_duration(overview['median_reply'])} "
            f"(90th percentile {format_duration(overview['p90_reply'])})."
        )
        if quick:
            fastest = min(quick, key=lambda sender: sender["median_reply"])
            sentence += (
                f" {fastest['name']} answers fastest, "
                f"median {format_duration(fastest['median_reply'])}."
            )
        sentences.append(sentence)
    silences = stats["silences"]
    if silences and silences[0]["seconds"] >= 12 * 3600:
        gap = silences[0]
        sentences.append(
            f"Longest silence was {format_duration(gap['seconds'])}, "
            f"after {gap['before']} on {human_dt(gap['start'])} "
            f"until {gap['after']} on {human_dt(gap['end'])}."
        )
    peak = overview["peak"]
    if peak["count"]:
        sentences.append(
            f"{WEEKDAYS[peak['weekday']]} at {peak['hour']:02d}:00 is the busiest hour "
            f"({num(peak['count'])} messages)."
        )
    emojis = stats["reactions"]["emojis"]
    if emojis:
        emoji, count = emojis[0]
        giver = max(senders, key=lambda sender: sender["reactions_given"])
        sentences.append(
            f"{emoji} shows up most ({num(count)}). "
            f"{giver['name']} leaves the most reactions ({num(giver['reactions_given'])})."
        )
    if overview["calls"]:
        calls = stats["calls"]
        sentence = (
            f"{num(calls['count'])} calls, {num(calls['missed'])} missed, unanswered, or declined."
        )
        if calls["parsed"]:
            sentence += f" Accepted calls add up to {format_duration(calls['seconds'])}."
        sentences.append(sentence)
    return sentences


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


class Raw:
    """HTML that is already escaped and safe to splice into the page."""

    def __init__(self, value: str):
        self.value = value


def html_table(headers: list[str], rows: list[list[object]], text_cols: set[int] | None = None) -> str:
    if not rows:
        return '<p class="muted">None.</p>'
    text_cols = text_cols or {0}
    head = "".join(
        f"<th class=\"{'t' if i in text_cols else 'n'}\">{esc(header)}</th>"
        for i, header in enumerate(headers)
    )
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            cls = "t" if i in text_cols else "n"
            content = cell.value if isinstance(cell, Raw) else esc(cell)
            cells.append(f'<td class="{cls}">{content}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def name_cell(name: str, color: str) -> Raw:
    return Raw(f'<span class="dot" style="background:{color}"></span>{esc(name)}')


def bar_list(items: list[tuple[str, float]], color: str | dict[str, str]) -> str:
    if not items:
        return '<p class="muted">None.</p>'
    peak = max(value for _, value in items) or 1
    rows = []
    for label, value in items:
        paint = color.get(label, "#1f6b4a") if isinstance(color, dict) else color
        width = 100 * value / peak
        shown = num(value) if not isinstance(value, float) else num(value)
        rows.append(
            '<div class="hrow">'
            f'<div class="hname" title="{esc(label)}">{esc(label)}</div>'
            f'<div class="htrack"><div class="hfill" style="width:{width:.1f}%;background:{paint}"></div></div>'
            f'<div class="hval">{esc(shown)}</div>'
            "</div>"
        )
    return "".join(rows)


def mini_hours(hours: list[int], color: str) -> str:
    peak = max(hours) or 1
    rects = []
    for i, count in enumerate(hours):
        height = 1 if count == 0 else max(2, round(26 * count / peak))
        rects.append(
            f'<rect x="{i * 4}" y="{28 - height}" width="3" height="{height}" rx="0.5" fill="{color}"/>'
        )
    return (
        '<svg class="spark" viewBox="0 0 96 32" width="168" height="52" role="img" '
        'aria-label="Messages by hour of day">' + "".join(rects) + "</svg>"
    )


def svg_daily(points: list[dict], unit: str) -> str:
    if not points:
        return ""
    width, height = 920, 132
    left, right, top, bottom = 8, 8, 16, 22
    inner_w = width - left - right
    inner_h = height - top - bottom
    peak = max(point["count"] for point in points) or 1
    step = inner_w / max(len(points), 1)
    bars = []
    labels = []
    last_month = None
    for index, point in enumerate(points):
        bar_h = 0 if point["count"] == 0 else max(1, inner_h * point["count"] / peak)
        x = left + index * step
        y = top + inner_h - bar_h
        fill = "#d9d3c7" if point["count"] == 0 else "#1f6b4a"
        bars.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(step - 0.4, 0.4):.2f}" '
            f'height="{bar_h:.2f}" fill="{fill}"><title>{esc(point["label"])}: {point["count"]}</title></rect>'
        )
        month = point["label"][:7]
        if month != last_month:
            last_month = month
            stamp = datetime.fromisoformat(point["label"][:10])
            text = stamp.strftime("%b %Y") if index == 0 or stamp.month == 1 else stamp.strftime("%b")
            labels.append(
                f'<text x="{x:.1f}" y="{height - 6}" fill="#5e584f" font-size="11">{esc(text)}</text>'
            )
    busiest = max(points, key=lambda point: point["count"])
    label = f"Messages per {unit}. Busiest {unit} {busiest['label']} with {busiest['count']}."
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}">'
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )


def svg_heatmap(heatmap: list[list[int]]) -> str:
    cell, left, top = 18, 36, 8
    width = left + 24 * cell + 4
    height = top + 7 * cell + 20
    peak = max(value for row in heatmap for value in row) or 1
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Messages by weekday and hour">'
    ]
    for day in range(7):
        parts.append(
            f'<text x="0" y="{top + day * cell + 13}" fill="#5e584f" font-size="11">{WEEKDAYS[day]}</text>'
        )
        for hour in range(24):
            value = heatmap[day][hour]
            color = heat_color(value, peak)
            x = left + hour * cell
            y = top + day * cell
            parts.append(
                f'<rect x="{x}" y="{y}" width="16" height="16" rx="2" fill="{color}">'
                f"<title>{WEEKDAYS[day]} {hour:02d}:00 · {value}</title></rect>"
            )
    for hour in range(0, 24, 3):
        parts.append(
            f'<text x="{left + hour * cell}" y="{height - 4}" fill="#5e584f" font-size="11">{hour:02d}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def heat_color(value: int, peak: int) -> str:
    if value <= 0 or peak <= 0:
        return "#f3efe8"
    ratio = value / peak
    if ratio < 0.25:
        return "#d5ebdd"
    if ratio < 0.5:
        return "#8fc7a6"
    if ratio < 0.75:
        return "#2f7d55"
    return "#143d2c"


def card(value: object, label: str, sub: str = "") -> str:
    extra = f'<div class="sub">{esc(sub)}</div>' if sub else ""
    return (
        '<div class="card">'
        f'<div class="num">{esc(value)}</div>'
        f'<div class="lbl">{esc(label)}</div>{extra}</div>'
    )


def section(section_id: str, title: str, body: str) -> str:
    return f'<section id="{section_id}"><h2>{esc(title)}</h2>{body}</section>'


def render_html(stats: dict, *, title: str, source: str) -> str:
    overview = stats["overview"]
    if not overview.get("messages"):
        skipped = ""
        if stats.get("errors"):
            skipped = "<ul>" + "".join(f"<li>{esc(err)}</li>" for err in stats["errors"]) + "</ul>"
        body = "<h1>Signal chat statistics</h1><p>No messages in this file.</p>" + skipped
        return page(title, body)

    parts = [
        "<header>",
        "<p class=\"kicker\">Signal chat statistics</p>",
        f"<h1>{esc(title)}</h1>",
        "<p class=\"muted\">"
        + esc(source)
        + f" · {esc(human_date(overview['start']))} – {esc(human_date(overview['end']))}"
        + "</p>",
        '<div class="lede">' + "".join(f"<p>{esc(sentence)}</p>" for sentence in lede(stats)) + "</div>",
        "</header>",
        nav_html(),
        section_overview(stats),
        section_people(stats),
        section_when(stats),
        section_rhythm(stats),
        section_reactions(stats),
        section_replies(stats),
        section_media(stats),
        section_language(stats),
        footer_html(stats, source),
    ]
    return page(title, "\n".join(parts))


def nav_html() -> str:
    links = [
        ("overview", "Overview"),
        ("people", "People"),
        ("when", "When"),
        ("rhythm", "Rhythm"),
        ("reactions", "Reactions"),
        ("replies", "Replies"),
        ("media", "Media and calls"),
        ("language", "Language"),
    ]
    return '<nav>' + "".join(f'<a href="#{id_}">{esc(label)}</a>' for id_, label in links) + "</nav>"


def section_overview(stats: dict) -> str:
    overview = stats["overview"]
    per_day = overview["messages"] / overview["active_days"] if overview["active_days"] else 0
    cards = "".join(
        [
            card(num(overview["messages"]), "Messages", f"{per_day:.1f} per active day"),
            card(num(overview["people"]), "People", f"{num(overview['talk_messages'])} text messages"),
            card(num(overview["span_days"]), "Days", f"{num(overview['active_days'])} active"),
            card(num(overview["words"]), "Words", f"{num(overview['chars'])} characters"),
            card(num(overview["sessions"]), "Sessions", f"{stats['gap_minutes']} min gap"),
            card(format_duration(overview["median_reply"]), "Median reply", f"{num(overview['reply_count'])} replies"),
            card(num(overview["reaction_events"]), "Reactions", pct(overview["reacted_messages"], overview["messages"]) + " of messages"),
            card(num(overview["calls"]), "Calls", f"{num(overview['missed'])} missed"),
        ]
    )
    return section("overview", "Overview", f'<div class="cards">{cards}</div>')


def section_people(stats: dict) -> str:
    colors = stats["colors"]
    senders = stats["senders"]
    total_messages = stats["overview"]["messages"]
    total_words = stats["overview"]["words"]
    rows = []
    for sender in senders:
        rows.append(
            [
                name_cell(sender["name"], colors[sender["name"]]),
                num(sender["messages"]),
                pct(sender["messages"], total_messages),
                num(sender["words"]),
                pct(sender["words"], total_words),
                f"{sender['avg_words']:.1f}",
                num(sender["questions"]),
                num(sender["links"]),
                num(sender["double_texts"]),
                format_duration(sender["median_reply"]),
                num(sender["reactions_given"]),
                num(sender["reactions_received"]),
            ]
        )
    table = html_table(
        [
            "Person",
            "Messages",
            "Share",
            "Words",
            "Word share",
            "Avg words",
            "Questions",
            "Links",
            "Double texts",
            "Median reply",
            "Reacted",
            "Got reactions",
        ],
        rows,
    )
    message_bars = bar_list(
        [(sender["name"], sender["messages"]) for sender in senders[:12]],
        colors,
    )
    word_bars = bar_list(
        [(sender["name"], sender["words"]) for sender in senders[:12]],
        colors,
    )
    cards = []
    shown = senders if len(senders) <= 8 else senders[:8]
    for sender in shown:
        words = ", ".join(f"{esc(word)} × {count}" for word, count in sender["distinctive"]) or "Not enough repeated words."
        emoji = ", ".join(f"{esc(symbol)} {count}" for symbol, count in sender["top_emoji"]) or "—"
        late = pct(sender["late"], sender["messages"])
        weekend = pct(sender["weekend"], sender["messages"])
        cards.append(
            "<article class=\"card person\">"
            f"<h3>{name_cell(sender['name'], colors[sender['name']])}</h3>"
            f"<p class=\"muted\">Hours across the day</p>{mini_hours(sender['hours'], colors[sender['name']])}"
            "<dl>"
            f"<dt>Late (23:00–05:59)</dt><dd>{esc(late)}</dd>"
            f"<dt>Weekend</dt><dd>{esc(weekend)}</dd>"
            f"<dt>Sessions started</dt><dd>{num(sender['sessions_started'])}</dd>"
            f"<dt>Longest streak</dt><dd>{num(sender['longest_streak'])}</dd>"
            f"<dt>Vocabulary</dt><dd>{num(sender['types'])} words</dd>"
            f"<dt>Type–token</dt><dd>{sender['ttr']:.2f}</dd>"
            f"<dt>Media</dt><dd>{num(sender['media'])}</dd>"
            f"<dt>Stickers</dt><dd>{num(sender['stickers'])}</dd>"
            f"<dt>Calls</dt><dd>{num(sender['calls'])} ({num(sender['missed'])} missed)</dd>"
            f"<dt>Deleted</dt><dd>{num(sender['deleted'])}</dd>"
            f"<dt>Quoted others</dt><dd>{num(sender['quotes'])}</dd>"
            f"<dt>Was quoted</dt><dd>{num(sender['quoted'])}</dd>"
            "</dl>"
            f"<p><span class=\"muted\">Words that lean toward them.</span> {words}</p>"
            f"<p><span class=\"muted\">Emoji they receive.</span> {emoji}</p>"
            "</article>"
        )
    note = ""
    if len(senders) > len(shown):
        note = f'<p class="muted">Cards show the {len(shown)} people with the most messages.</p>'
    body = (
        "<div class=\"split\">"
        f"<div><h3>Messages</h3>{message_bars}</div>"
        f"<div><h3>Words</h3>{word_bars}</div>"
        "</div>"
        + table
        + note
        + f'<div class="people">{"".join(cards)}</div>'
    )
    return section("people", "People", body)


def section_when(stats: dict) -> str:
    daily = stats["daily"]
    chart = svg_daily(daily["points"], daily["unit"])
    months = bar_list([(item["label"], item["count"]) for item in stats["months"]], "#1f6b4a")
    weekday_bars = bar_list(list(zip(WEEKDAYS, stats["weekdays"])), "#2c5282")
    hour_bars = bar_list([(f"{hour:02d}", count) for hour, count in enumerate(stats["hours"])], "#c46b2c")
    busiest_rows = [[item["label"], num(item["count"])] for item in stats["busiest_days"]]
    body = (
        f'<div class="chart">{chart}<p class="muted">Each bar is one {esc(daily["unit"])}. '
        "Empty stretches are the pale ticks.</p></div>"
        '<div class="split">'
        f"<div><h3>Months</h3>{months}</div>"
        f"<div><h3>Weekday</h3>{weekday_bars}</div>"
        "</div>"
        f"<h3>Hour of day</h3>{hour_bars}"
        + svg_wrap(svg_heatmap(stats["heatmap"]), "Darker cells are busier. Time is whatever the export stored, usually the phone's local time.")
        + "<h3>Busiest days</h3>"
        + html_table(["Day", "Messages"], busiest_rows)
    )
    return section("when", "When", body)


def section_rhythm(stats: dict) -> str:
    sessions = stats["sessions"]
    latency = stats["latency"]
    longest = sessions["longest_messages"]
    longest_time = sessions["longest_duration"]
    facts = (
        "<div class=\"cards\">"
        + card(num(sessions["count"]), "Sessions", f"{num(sessions['one_line'])} were a single message")
        + card(f"{sessions['median_messages']:.1f}" if sessions["median_messages"] is not None else "—", "Median length", "messages")
        + card(format_duration(sessions["median_duration"]), "Median duration", "sessions of 2+")
        + card(num(longest["messages"]) if longest else "—", "Longest session", human_dt(longest["start"]) if longest else "")
        + card(format_duration(longest_time["seconds"]) if longest_time else "—", "Longest stretch", human_dt(longest_time["start"]) if longest_time else "")
        + "</div>"
        + f"<p>A new session starts when {stats['gap_minutes']} minutes pass with nobody writing. "
        "Double texts are extra messages from the same person before anyone else talks. "
        "Reply time is the gap when the speaker changes, and only inside a session, "
        "so an overnight pause is a silence rather than a slow reply.</p>"
    )
    buckets = bar_list([(item["label"], item["count"]) for item in latency["buckets"]], "#1f6b4a")
    silence_rows = [
        [
            format_duration(item["seconds"]),
            human_dt(item["start"]),
            item["before"],
            human_dt(item["end"]),
            item["after"],
        ]
        for item in stats["silences"]
    ]
    pair_rows = [
        [item["from"], item["to"], num(item["count"]), format_duration(item["median"])]
        for item in latency["pairs"]
    ]
    featured = ""
    if latency["fastest"]:
        featured += reply_block("Fastest reply", latency["fastest"])
    if latency["slowest"] and latency["slowest"] is not latency["fastest"]:
        featured += reply_block("Slowest reply inside a session", latency["slowest"])
    body = (
        facts
        + "<h3>How long until the other person talks</h3>"
        + buckets
        + featured
        + "<h3>Who replies to whom</h3>"
        + html_table(["Previous", "Replier", "Replies", "Median"], pair_rows, text_cols={0, 1})
        + "<h3>Longest silences</h3>"
        + html_table(["Gap", "After", "Who", "Until", "Who"], silence_rows, text_cols={1, 2, 3, 4})
    )
    return section("rhythm", "Rhythm", body)


def reply_block(title: str, record: dict) -> str:
    return (
        '<div class="card quote-card">'
        f"<h3>{esc(title)} · {esc(format_duration(record['seconds']))}</h3>"
        f"<p class=\"muted\">{esc(record['from'])} · {esc(human_dt(record['at']))}</p>"
        f"<blockquote>{esc(record['prompt'])}</blockquote>"
        f"<p><strong>{esc(record['to'])}</strong> {esc(record['reply'])}</p>"
        "</div>"
    )


def section_reactions(stats: dict) -> str:
    reactions = stats["reactions"]
    colors = stats["colors"]
    emoji_bars = bar_list(reactions["emojis"], "#8a3d5b")
    names = reactions["matrix_names"]
    header = ["Reactor"] + names
    rows = []
    for reactor in names:
        row = [name_cell(reactor, colors.get(reactor, "#1f6b4a"))]
        counts = reactions["matrix"].get(reactor, {})
        for author in names:
            row.append(num(counts.get(author, 0)))
        rows.append(row)
    top_rows = []
    for message in reactions["top_messages"]:
        bits = ", ".join(f"{name} {emoji}" for name, emoji in message["reactions"])
        top_rows.append([human_dt(message["at"]), message["sender"], message["body"], bits])
    body = (
        emoji_bars
        + "<h3>Who reacts to whom</h3>"
        + "<p class=\"muted\">Rows leave the reaction. Columns wrote the message. The diagonal is someone reacting to themself.</p>"
        + html_table(header, rows, text_cols={0})
        + "<h3>Messages that drew the most reactions</h3>"
        + html_table(["When", "Who", "Message", "Reactions"], top_rows, text_cols={0, 1, 2, 3})
    )
    return section("reactions", "Reactions", body)


def section_replies(stats: dict) -> str:
    quotes = stats["quotes"]
    cards = (
        '<div class="cards">'
        + card(num(quotes["count"]), "Quotes", "replies that named an earlier text")
        + card(num(quotes["matched"]), "Matched", "same text appeared earlier")
        + card(num(quotes["unmatched"]), "Unmatched", "quoted text was not in this file")
        + card(num(quotes["media"]), "Media replies", "Photo, video, or voice note")
        + "</div>"
    )
    targets = sorted(quotes["targets"].items(), key=lambda item: (-item[1], item[0].casefold()))
    target_bars = bar_list(targets, stats["colors"])
    rows = [
        [human_dt(item["at"]), item["by"], item["target"], item["quote"], item["reply"]]
        for item in quotes["examples"]
    ]
    body = (
        cards
        + "<p>sigexport stores the quoted text, not the quoted person. "
        "A match means that text equals some earlier message in this file, most recent first. "
        "Replies to a picture or voice note with no caption are counted separately.</p>"
        + "<h3>Whose words get quoted</h3>"
        + target_bars
        + "<h3>A spread of matched replies</h3>"
        + html_table(["When", "Quoter", "Quoted", "Quoted text", "Reply"], rows, text_cols={0, 1, 2, 3, 4})
    )
    return section("replies", "Replies", body)


def section_media(stats: dict) -> str:
    media = stats["media"]
    calls = stats["calls"]
    kind_order = ["image", "audio", "video", "other", "missing"]
    kind_rows = [[kind, num(media["kinds"].get(kind, 0))] for kind in kind_order]
    missing_rows = [[kind, num(count)] for kind, count in sorted(media["missing_kinds"].items())]
    sticker_bars = bar_list(media["stickers"], "#c46b2c")
    call_cards = (
        '<div class="cards">'
        + card(num(calls["count"]), "Calls")
        + card(num(calls["missed"]), "Missed")
        + card(num(calls["incoming"]), "Incoming")
        + card(num(calls["outgoing"]), "Outgoing")
        + card(format_duration(calls["seconds"]) if calls["parsed"] else "—", "Accepted time", f"{num(calls['parsed'])} with a duration")
        + card(format_duration(calls["median"]), "Median accepted call")
        + "</div>"
    )
    per_person = html_table(
        ["Person", "Attachments", "Stickers", "Calls", "Missed", "Deleted"],
        [
            [
                name_cell(sender["name"], stats["colors"][sender["name"]]),
                num(sender["media"]),
                num(sender["stickers"]),
                num(sender["calls"]),
                num(sender["missed"]),
                num(sender["deleted"]),
            ]
            for sender in stats["senders"]
        ],
    )
    body = (
        "<h3>Attachments</h3>"
        + html_table(["Kind", "Count"], kind_rows)
        + "<p class=\"muted\">missing_kind is set when Signal had a file and the export did not copy it. "
        "sigexport uses \"media\" for visual files and \"attachment\" otherwise. The path is empty in that case.</p>"
        + "<h3>Why files were missing</h3>"
        + html_table(["missing_kind", "Count"], missing_rows)
        + "<h3>Stickers</h3>"
        + sticker_bars
        + "<h3>Calls</h3>"
        + "<p class=\"muted\">Missed includes unanswered and declined, which is how sigexport sets the flag. "
        "Duration is read from phrases like \"accepted, 3m 12s\".</p>"
        + call_cards
        + per_person
    )
    return section("media", "Media and calls", body)


def section_language(stats: dict) -> str:
    language = stats["language"]
    body = (
        "<p>Calls and deleted messages are left out of the word counts. "
        "Common English function words are left out of the lists. "
        "A repeated line is the same text sent at least four times.</p>"
        + "<h3>Words</h3>"
        + bar_list(language["words"], "#1f6b4a")
        + "<h3>Pairs of words</h3>"
        + bar_list(language["bigrams"], "#2c5282")
        + "<h3>Lines people actually repeat</h3>"
        + html_table(
            ["Times", "Line"],
            [[num(count), text] for text, count in language["repeated"]],
            text_cols={1},
        )
    )
    return section("language", "Language", body)


def svg_wrap(svg: str, caption: str) -> str:
    return f'<div class="chart">{svg}<p class="muted">{esc(caption)}</p></div>'


def footer_html(stats: dict, source: str) -> str:
    skipped = ""
    if stats["errors"]:
        items = "".join(f"<li>{esc(err)}</li>" for err in stats["errors"][:20])
        extra = ""
        if len(stats["errors"]) > 20:
            extra = f"<p>{len(stats['errors']) - 20} more.</p>"
        skipped = f"<p>{len(stats['errors'])} lines skipped.</p><ul>{items}</ul>{extra}"
    return (
        "<footer>"
        f"<p>Source {esc(source)}. Session gap {stats['gap_minutes']} minutes. "
        "Word counts ignore calls and deleted messages. "
        "A reply is a speaker change inside a session. "
        "Quoted people are inferred by matching the quoted text to an earlier message. "
        "Print this page to save a PDF.</p>"
        + skipped
        + "</footer>"
    )


CSS = """
:root {
  --ink: #1c1915;
  --muted: #5e584f;
  --line: #e4ddd2;
  --paper: #f6f3ec;
  --card: #fffdf9;
  --green: #1f6b4a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 16px/1.45 Georgia, "Iowan Old Style", Palatino, "Palatino Linotype", serif;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 2.4rem 1.2rem 4rem; }
h1 { font-weight: 500; font-size: 2.3rem; letter-spacing: -0.03em; margin: 0.1rem 0 0.3rem; }
h2 { font-weight: 500; font-size: 1.45rem; margin: 2.2rem 0 0.7rem; }
h3 { font-weight: 500; font-size: 1.05rem; margin: 1.1rem 0 0.4rem; }
p { margin: 0.45rem 0; }
a { color: var(--green); }
.kicker, .lbl, .muted, nav, table, .hrow, dl, footer {
  font-family: "Avenir Next", "Segoe UI", sans-serif;
}
.kicker { text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.75rem; color: var(--muted); margin: 0; }
.muted { color: var(--muted); }
.lede { font-size: 1.12rem; max-width: 42rem; }
nav { margin: 1rem 0 0.2rem; }
nav a { margin-right: 0.85rem; text-decoration: none; font-size: 0.92rem; }
nav a:hover { text-decoration: underline; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); gap: 0.6rem; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 0.75rem 0.85rem; }
.num { font-family: "Avenir Next", "Segoe UI", sans-serif; font-size: 1.7rem; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
.lbl { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; }
.sub { color: var(--muted); font-family: "Avenir Next", "Segoe UI", sans-serif; font-size: 0.78rem; margin-top: 0.15rem; }
.split { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
.people { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0.7rem; margin-top: 0.8rem; }
.person h3 { margin-top: 0; }
dl { display: grid; grid-template-columns: 1fr auto; gap: 0.18rem 0.8rem; margin: 0.5rem 0; font-size: 0.9rem; }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
dt { color: var(--muted); }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
th, td { padding: 0.38rem 0.55rem 0.38rem 0; vertical-align: top; }
th { text-align: left; font-weight: 600; color: var(--muted); border-bottom: 1px solid var(--ink); }
td { border-bottom: 1px solid var(--line); }
th.n, td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.dot { display: inline-block; width: 0.55rem; height: 0.55rem; border-radius: 50%; margin-right: 0.35rem; transform: translateY(1px); }
.hrow { display: grid; grid-template-columns: 7.2rem 1fr auto; gap: 0.45rem; align-items: center; margin: 0.28rem 0; font-size: 0.86rem; }
.hname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.htrack { background: #f3efe8; border-radius: 99px; height: 0.55rem; }
.hfill { height: 100%; border-radius: 99px; }
.hval { font-variant-numeric: tabular-nums; color: var(--muted); min-width: 3.2rem; text-align: right; }
.chart { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 0.7rem 0.8rem 0.3rem; margin: 0.8rem 0 1rem; overflow-x: auto; }
svg { width: 100%; height: auto; display: block; }
svg text { font-family: "Avenir Next", "Segoe UI", sans-serif; }
blockquote { margin: 0.3rem 0; padding-left: 0.7rem; border-left: 3px solid var(--green); }
.quote-card { margin: 0.7rem 0; }
footer { margin-top: 2.5rem; color: var(--muted); font-size: 0.85rem; }
@media (max-width: 700px) {
  .split, .hrow { grid-template-columns: 1fr; }
  h1 { font-size: 1.8rem; }
}
@media print {
  body { background: white; }
  .wrap { max-width: none; padding: 0; }
  nav { display: none; }
  .card, .chart, article, tr { break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
}
"""


def page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>" + esc(title) + "</title>\n<style>\n" + CSS + "</style>\n</head>\n"
        '<body>\n<div class="wrap">\n' + body + "\n</div>\n</body>\n</html>\n"
    )


# --- fictional sample -------------------------------------------------------

PEOPLE = ("Me", "Aya", "Jim")
PLACES = ("the studio", "the usual spot", "the station", "Jim's", "the river path")
DAYS = ("Saturday", "Sunday", "Friday", "Thursday")
DRY_START = datetime(2025, 8, 2)
DRY_END = datetime(2025, 8, 20, 19, 0)
TRIP = datetime(2025, 9, 13, 10, 0)
TRIP_END = datetime(2025, 9, 15)

ME_LINES = (
    "Can we do {place} on {day}?",
    "I keep thinking about the pineapple protocol.",
    "On my way. The train is late again.",
    "Did you see that? Sending the photo.",
    "Keys are under the mat.",
    "Let's not lose the plot on this one.",
    "I wrote the plan down so we stop re-deciding it in the chat every week. Studio at seven, keys under the mat, pineapple protocol says we leave by eleven.",
    "Bringing the speaker if the rain holds.",
    "The pineapple protocol says we leave by eleven.",
    "Can someone grab oat milk on the way?",
    "Map is here: https://example.com/usual-spot",
    "I can do {day} after six, not before.",
    "That photo from {place} is the one to keep.",
    "Leaving now, see you at {place}.",
    "We should decide the food before we get there this time.",
    "If Jim is late again we start without a speech about it.",
)
AYA_LINES = (
    "Yes, {place} works.",
    "On my way.",
    "Did you see that?",
    "Pineapple protocol accepted.",
    "I can do {day}.",
    "Send the photo when you have it.",
    "Home. That was a long one.",
    "Don't forget the keys.",
    "Jim is going to be late, obviously.",
    "{day} is better than {place} being a surprise.",
    "I saved us a table, don't drift.",
    "Laughing, but also we do need a plan.",
    "Oat milk is in the bag.",
    "The train excuse is undefeated.",
)
JIM_LINES = (
    "running late",
    "send the address",
    "omw",
    "can't tonight",
    "the pineapple thing is fine",
    "at the station",
    "need a ride from {place}",
    "voice note from the train",
    "the usual spot is closed on {day}",
    "I missed the call, text me",
    "sure",
    "lol ok",
    "be there in twenty",
    "which platform?",
)
EMOJIS = ("❤️", "😂", "👍", "🔥", "🍍", "😮", "😭")
EMOJI_WEIGHTS = (5, 4, 3, 2, 2, 1, 1)
STICKERS = (
    {"id": "wave-1", "packId": "pack-waves", "packKey": "sample-key-waves", "emoji": "👋", "extension": "webp"},
    {"id": "laugh-2", "packId": "pack-waves", "packKey": "sample-key-waves", "emoji": "😂", "extension": "webp"},
    {"id": "fruit-3", "packId": "pack-fruit", "packKey": "sample-key-fruit", "emoji": "🍍", "extension": "png"},
    {"id": "flame-4", "packId": "pack-fruit", "packKey": "sample-key-fruit", "emoji": "🔥", "extension": None},
)


def message(
    date: datetime,
    sender: str,
    body: str,
    *,
    quote: str = "",
    sticker: dict | None = None,
    reactions: list[tuple[str, str]] | None = None,
    attachments: list[dict] | None = None,
    deleted: bool = False,
    call: bool = False,
    missed: bool = False,
) -> dict:
    return {
        "date": date,
        "sender": sender,
        "body": body.strip(),
        "quote": quote,
        "sticker": sticker,
        "reactions": list(reactions or []),
        "attachments": list(attachments or []),
        "deleted": deleted,
        "call": call,
        "missed": missed,
    }


def format_quote(text: str) -> str:
    text = text.rstrip("\n").replace("\n", "\n> ")
    return f"\n\n> {text}\n\n"


def fill_line(rng: random.Random, sender: str) -> str:
    pool = {"Me": ME_LINES, "Aya": AYA_LINES, "Jim": JIM_LINES}[sender]
    return rng.choice(pool).format(place=rng.choice(PLACES), day=rng.choice(DAYS))


def pick_sender(rng: random.Random, hour: int, avoid: str | None = None) -> str:
    weights = []
    base = {"Me": 4.5, "Aya": 4.0, "Jim": 1.6}
    for name in PEOPLE:
        weight = base[name]
        if hour >= 22 or hour < 2:
            if name == "Me":
                weight += 3
        if 8 <= hour <= 11 and name == "Aya":
            weight += 2.2
        if name == avoid:
            weight *= 0.05
        weights.append(weight)
    return rng.choices(PEOPLE, weights=weights)[0]


def call_duration(rng: random.Random) -> str:
    minutes = rng.randint(0, 22)
    seconds = rng.randint(0, 59)
    if minutes == 0 and seconds == 0:
        seconds = 12
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def photo_attachment(rng: random.Random) -> dict:
    name = f"IMG_{rng.randint(1000, 9999)}.jpg"
    return {"name": name, "path": f"media/{name}", "missing_kind": None}


def voice_attachment(rng: random.Random) -> dict:
    name = f"voice-{rng.randint(100, 999)}.m4a"
    return {"name": name, "path": f"media/{name}", "missing_kind": None}


def advance(rng: random.Random, current: datetime) -> datetime:
    before = current
    if TRIP <= current < TRIP_END:
        current = current + timedelta(minutes=rng.randint(90, 220))
    else:
        roll = rng.random()
        if roll < 0.42:
            current = current + timedelta(minutes=rng.randint(100, 8 * 60))
        elif roll < 0.78:
            current = current + timedelta(hours=rng.randint(10, 20))
        elif roll < 0.93:
            current = current + timedelta(days=1, hours=rng.randint(0, 8))
        elif roll < 0.985:
            current = current + timedelta(days=rng.randint(2, 4), hours=rng.randint(0, 6))
        else:
            current = current + timedelta(days=rng.randint(5, 8))
        if before < TRIP and current >= TRIP_END:
            current = TRIP
    if 2 <= current.hour <= 7:
        current = current.replace(
            hour=rng.choice((9, 10, 12, 18, 19, 20, 21)),
            minute=rng.randint(0, 59),
            second=rng.randint(0, 59),
        )
    if DRY_START <= current < DRY_END:
        current = DRY_END
    return current


def compose(rng: random.Random, when: datetime, sender: str, recent: list[str]) -> dict:
    roll = rng.random()
    if roll < 0.018:
        missed = rng.random() < 0.36
        outgoing = rng.random() < 0.55
        kind = rng.choice(("voice", "video"))
        duration = call_duration(rng)
        if outgoing:
            sender = "Me"
            if missed:
                body = f"Outgoing {kind} call (unanswered)"
            else:
                body = f"Outgoing {kind} call (accepted, {duration})"
        else:
            sender = rng.choice(("Aya", "Jim"))
            if missed:
                body = f"Missed {kind} call" if rng.random() < 0.55 else f"Incoming {kind} call (declined)"
            else:
                body = f"Incoming {kind} call (accepted, {duration})"
        return message(when, sender, body, call=True, missed=missed)

    if roll < 0.032:
        body = rng.choice(("forget that", "wrong chat", "nvm that was mean"))
        return message(when, sender, body, deleted=True)

    body = fill_line(rng, sender)
    quote = ""
    sticker = None
    attachments: list[dict] = []
    if rng.random() < 0.045:
        sticker = dict(rng.choice(STICKERS))
        if rng.random() < 0.45:
            body = ""
    elif sender == "Jim" and rng.random() < 0.16:
        attachments.append(
            {"name": "", "path": "", "missing_kind": "attachment"}
            if rng.random() < 0.22
            else voice_attachment(rng)
        )
    elif sender == "Me" and rng.random() < 0.13:
        if rng.random() < 0.18:
            attachments.append({"name": "", "path": "", "missing_kind": "media"})
        elif rng.random() < 0.12:
            attachments.append({"name": "notes.pdf", "path": "media/notes.pdf", "missing_kind": None})
        else:
            attachments.append(photo_attachment(rng))
    elif sender == "Aya" and rng.random() < 0.05:
        attachments.append(photo_attachment(rng))

    if body and recent and rng.random() < 0.12:
        quote = format_quote(rng.choice(recent[-30:]))

    reactions: list[tuple[str, str]] = []
    if rng.random() < 0.30:
        pool = [name for name in PEOPLE if name != sender]
        count = 1 if rng.random() < 0.75 else min(2, len(pool))
        for _ in range(count):
            weights = [3.2 if name == "Aya" else 1 for name in pool]
            reactor = rng.choices(pool, weights=weights)[0]
            pool.remove(reactor)
            reactions.append((reactor, rng.choices(EMOJIS, weights=EMOJI_WEIGHTS)[0]))

    made = message(
        when,
        sender,
        body,
        quote=quote,
        sticker=sticker,
        reactions=reactions,
        attachments=attachments,
    )
    if len(body) > 20:
        recent.append(body)
        del recent[:-50]
    return made


def ensure_variants(messages: list[dict]) -> None:
    """Drop any missing schema edge into the timeline, not onto the last day."""

    def add(when: datetime, **kwargs):
        messages.append(message(when, **kwargs))

    if not any(norm_text(item["quote"]) == "How is everyone?" for item in messages):
        add(
            datetime(2025, 6, 3, 11, 15),
            sender="Aya",
            body="Still a good question.",
            quote=format_quote("How is everyone?"),
            reactions=[("Me", "🍍")],
        )
    if not any(item["sticker"] and item["sticker"].get("extension") for item in messages):
        add(datetime(2025, 6, 4, 19, 5), sender="Aya", body="", sticker=dict(STICKERS[0]))
    if not any(item["sticker"] and item["sticker"].get("extension") is None for item in messages):
        add(datetime(2025, 6, 6, 12, 40), sender="Me", body="this one", sticker=dict(STICKERS[3]))
    if not any(any("%20" in attachment.get("path", "") for attachment in item["attachments"]) for item in messages):
        add(
            datetime(2025, 9, 13, 16, 10),
            sender="Me",
            body="Sending the trip photo.",
            attachments=[{"name": "trip photo.jpg", "path": "media/trip%20photo.jpg", "missing_kind": None}],
            reactions=[("Aya", "❤️"), ("Jim", "👍")],
        )
    if not any(any(attachment.get("missing_kind") for attachment in item["attachments"]) for item in messages):
        add(
            datetime(2025, 7, 9, 21, 12),
            sender="Jim",
            body="the voice note didn't come through",
            attachments=[{"name": "", "path": "", "missing_kind": "attachment"}],
        )
    if not any(item["call"] and not item["missed"] for item in messages):
        add(datetime(2025, 7, 18, 20, 2), sender="Me", body="Outgoing voice call (accepted, 4m 12s)", call=True, missed=False)
    if not any(item["call"] and item["missed"] for item in messages):
        add(datetime(2025, 10, 4, 9, 16), sender="Aya", body="Missed voice call", call=True, missed=True)
    if not any(item["deleted"] for item in messages):
        add(datetime(2025, 6, 11, 22, 41), sender="Me", body="wrong chat", deleted=True)


def generate_messages(n: int = 900, seed: int = 30) -> list[dict]:
    """Fictional group chat in the sigexport Message shape. Deterministic for a seed."""
    if n < 1:
        raise ValueError("n must be >= 1")
    rng = random.Random(seed)
    opening = [
        message(datetime(2025, 6, 2, 15, 4), "Me", "How is everyone?"),
        message(datetime(2025, 6, 2, 15, 10), "Aya", "We're great!", reactions=[("Me", "❤️")]),
        message(datetime(2025, 6, 2, 15, 20), "Jim", "I'm not."),
    ]
    messages = opening[:n]
    current = datetime(2025, 6, 2, 18, 42)
    recent: list[str] = []
    guard = 0
    while len(messages) < n and guard < n * 6:
        guard += 1
        current = advance(rng, current)
        on_trip = TRIP <= current < TRIP_END
        length = rng.randint(3, 7) if on_trip else rng.choices(
            (1, 2, 3, 4, 5, 6, 8, 11),
            weights=(8, 16, 18, 16, 12, 8, 4, 2),
        )[0]
        length = max(1, min(length, n - len(messages)))
        sender = pick_sender(rng, current.hour)
        for index in range(length):
            if index:
                current += timedelta(seconds=rng.randint(20, 420))
                if rng.random() < 0.72:
                    sender = pick_sender(rng, current.hour, avoid=sender)
            made = compose(rng, current, sender, recent)
            messages.append(made)
            sender = made["sender"]
    if n >= 30:
        ensure_variants(messages)
    messages.sort(key=lambda item: item["date"])
    return messages


def to_export(message_obj: dict) -> dict:
    body = message_obj["body"]
    if not body.endswith("  "):
        body = f"{body}  "
    sticker = message_obj["sticker"]
    if sticker is not None:
        sticker = {
            "id": sticker.get("id") or "",
            "packId": sticker.get("packId") or "",
            "packKey": sticker.get("packKey") or "",
            "emoji": sticker.get("emoji") or "",
            "extension": sticker.get("extension"),
        }
    attachments = [
        {
            "name": attachment.get("name") or "",
            "path": attachment.get("path") or "",
            "missing_kind": attachment.get("missing_kind"),
        }
        for attachment in message_obj["attachments"]
    ]
    return {
        "date": message_obj["date"].isoformat(),
        "sender": message_obj["sender"],
        "body": body,
        "quote": message_obj["quote"],
        "sticker": sticker,
        "reactions": [[name, emoji] for name, emoji in message_obj["reactions"]],
        "attachments": attachments,
        "deleted": message_obj["deleted"],
        "call": message_obj["call"],
        "missed": message_obj["missed"],
    }


def pick_examples(messages: list[dict]) -> list[dict]:
    exported = [to_export(item) for item in messages]
    picks: list[dict] = []

    def take(pred) -> None:
        for src, obj in zip(messages, exported):
            if pred(src) and obj not in picks:
                picks.append(obj)
                return

    take(lambda item: item["body"] == "How is everyone?")
    take(lambda item: bool(item["reactions"]))
    take(lambda item: norm_text(item["quote"]) == "How is everyone?")
    take(lambda item: item["sticker"] and item["sticker"].get("extension"))
    take(lambda item: item["sticker"] and item["sticker"].get("extension") is None)
    take(
        lambda item: any(
            attachment.get("name") and attachment.get("missing_kind") is None and "%20" not in attachment.get("path", "")
            for attachment in item["attachments"]
        )
    )
    take(lambda item: any("%20" in attachment.get("path", "") for attachment in item["attachments"]))
    take(lambda item: any(attachment.get("missing_kind") for attachment in item["attachments"]))
    take(lambda item: item["call"] and not item["missed"])
    take(lambda item: item["call"] and item["missed"])
    take(lambda item: item["deleted"])
    return picks


def dump_ndjson(messages: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(to_export(item), ensure_ascii=False) for item in messages]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def dump_examples(messages: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = pick_examples(messages)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def report_title(path: Path) -> str:
    if path.name in {"data.json", "sample.ndjson", "sample.json", "examples.json"}:
        parent = path.parent.name
        if parent and parent not in {".", "testdata", "out"}:
            return parent
        return "Signal chat"
    return path.stem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Statistics for a sigexport Signal chat file.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sample = sub.add_parser("sample", help="write a fictional chat in the sigexport shape")
    sample.add_argument("-o", "--out", type=Path, default=DEFAULT_SAMPLE)
    sample.add_argument("--examples", type=Path, default=None)
    sample.add_argument("--messages", type=int, default=900)
    sample.add_argument("--seed", type=int, default=30)

    report = sub.add_parser("report", help="read NDJSON or a JSON array and write an HTML report")
    report.add_argument("input", nargs="?", type=Path, default=DEFAULT_SAMPLE)
    report.add_argument("-o", "--out", type=Path, default=None)
    report.add_argument("--gap-minutes", type=int, default=DEFAULT_GAP_MINUTES)

    args = parser.parse_args(argv)
    if args.cmd == "sample":
        messages = generate_messages(args.messages, args.seed)
        dump_ndjson(messages, args.out)
        examples = args.examples or args.out.parent / "examples.json"
        dump_examples(messages, examples)
        first, last = messages[0]["date"], messages[-1]["date"]
        print(f"Wrote {len(messages)} messages to {args.out}")
        print(f"Wrote {examples}")
        print(f"{human_dt(first)} → {human_dt(last)}")
        return 0

    if args.gap_minutes < 1:
        print("gap must be at least 1 minute", file=sys.stderr)
        return 1
    path = args.input
    if not path.is_file():
        print(f"file not found: {path}", file=sys.stderr)
        return 1
    messages, errors = parse_text(path.read_text(encoding="utf-8-sig"))
    stats = analyze(messages, gap_minutes=args.gap_minutes, errors=errors)
    out = args.out or Path("out") / f"{path.stem}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(stats, title=report_title(path), source=str(path)), encoding="utf-8")
    print(f"Wrote {out}")
    overview = stats["overview"]
    if overview.get("messages"):
        print(
            f"{overview['messages']} messages, {overview['people']} people, "
            f"{overview['span_days']} days, median reply {format_duration(overview['median_reply'])}"
        )
    else:
        print("no messages")
    if errors:
        print(f"{len(errors)} lines skipped", file=sys.stderr)
    return 0 if overview.get("messages") else 1


if __name__ == "__main__":
    raise SystemExit(main())
