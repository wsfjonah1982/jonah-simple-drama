# _data

Anonymous usage data for refining the app. Written by the app at runtime and never cleaned up with `_project/`.

- `events.jsonl`: one JSON line per event (`created`, `rejected`, `finished`, `email`, `liked`); fields are listed in `services/data_log.py`.
- `genre_counts.json`: how often each drama was picked; orders the cards on the create page.
- `stress_test_2026-09-30.json`: results of a load test (26 synthetic runs): success rate, failure reason, timings. Kept apart so it never counts as real visitors.

The first lines of `events.jsonl` were imported from the earlier drama-flow-00 deployment (its projects were deleted on 2026-10-01). They carry an `imported` field naming the source, and they record less than live events: drama, outcome and email only, with the email time as `t`. Events marked `"test": true` are the site owner's own runs; the report leaves them out unless you pass `--include-tests`.

No personal data is stored here: no names, emails, IP addresses, photos, prompts or story text.
Only this README is committed; the data files are git-ignored. Summarise them with `python -m services.data_report`.
