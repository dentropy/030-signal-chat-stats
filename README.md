# 030 — Signal chat statistics

Reads a [sigexport](https://github.com/carderne/signal-export) chat (`data.json`,
one JSON object per line) and writes one self-contained HTML report. Print
that page to PDF if you want a file to keep. Nothing leaves the machine.

The schema is `schema/message.schema.json`. A schema inferred from one
message often types `sticker` and `missing_kind` as `null` only. Real lines
also use a sticker object, and `missing_kind` is `"media"` or `"attachment"`
when a file was left out of the export. Reactions are `[name, emoji]` pairs
because sigexport's `Reaction` is a namedtuple.

## Report

```bash
python3 chatstats.py report testdata/sample.ndjson -o out/sample-report.html
```

On a real export (the file is named `data.json` and is still NDJSON):

```bash
python3 chatstats.py report "/path/to/Signal Export/Aya/data.json" -o aya.html
```

A JSON array of the same objects works too. `--gap-minutes` (default 30)
is the pause that starts a new session. Reply times are only counted inside
a session, so an overnight gap shows up as silence.

The report covers volume, words versus message share, hour/weekday heatmap,
sessions, reply latency, who replies to whom, reactions, quoted text matched
back to an earlier message, attachments, stickers, calls, and repeated lines.

## Sample data

Fictional chat with Me, Aya, and Jim, so you can see the shape without a
Signal backup. Same seed always writes the same file.

```bash
python3 chatstats.py sample
```

- `testdata/sample.ndjson` — about 900 messages, one object per line
- `testdata/examples.json` — a short pretty-printed tour of the variants
- `out/sample-report.html` — the report for that sample

The file starts the way the sigexport README does. Bodies end in two spaces,
which is sigexport forcing a Markdown line break:

```json
{"date": "2025-06-02T15:04:00", "sender": "Me", "body": "How is everyone?  ", "quote": "", "sticker": null, "reactions": [], "attachments": [], "deleted": false, "call": false, "missed": false}
{"date": "2025-06-02T15:10:00", "sender": "Aya", "body": "We're great!  ", "quote": "", "sticker": null, "reactions": [["Me", "❤️"]], "attachments": [], "deleted": false, "call": false, "missed": false}
{"date": "2025-06-02T15:20:00", "sender": "Jim", "body": "I'm not.  ", "quote": "", "sticker": null, "reactions": [], "attachments": [], "deleted": false, "call": false, "missed": false}
```

`examples.json` is the thing to open if you want one of each: a quote, a
sticker with and without a file extension, a real attachment, a missing
attachment, a call, a missed call, and a deleted message.

## Tests

```bash
python3 -m unittest tests.test_chatstats
```
