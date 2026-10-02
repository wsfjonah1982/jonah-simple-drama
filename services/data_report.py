"""Summarise _data/events.jsonl: popularity, failure rate, failure reasons, timings, email delivery.

    python -m services.data_report              # everything
    python -m services.data_report --since 2026-10-01
    python -m services.data_report --json       # the same numbers as JSON, for other tools
    python -m services.data_report --include-tests   # also count events marked "test" (the owner's own runs)
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict

from services import data_log, genres


def summarise(events):
    titles = {g["id"]: g["title"] for g in genres.GENRES}
    per_genre = defaultdict(lambda: Counter())
    failures, rejected, emails, devices, days = Counter(), Counter(), Counter(), Counter(), Counter()
    timings = defaultdict(list)
    plots = plots_known = 0
    for e in events:
        kind, genre = e.get("event"), e.get("genre")
        if kind == "created":
            per_genre[genre]["created"] += 1
            devices[e.get("device") or "unknown"] += 1
            days[e["t"][:10]] += 1
            if "plot" in e:  # imported history does not record it
                plots_known += 1
                plots += bool(e["plot"])
        elif kind == "finished":
            per_genre[genre][e.get("status")] += 1
            if e.get("status") == "failed":
                failures[f"{e.get('stage')}: {e.get('code') or 'no code'}"] += 1
            else:
                for k, v in (e.get("timing") or {}).items():
                    timings[k].append(v)
        elif kind == "rejected":
            rejected[e.get("reason")] += 1
        elif kind == "email":
            emails[e.get("status")] += 1
        elif kind == "liked":
            per_genre[genre]["likes"] += 1 if e.get("liked") else -1

    created = sum(c["created"] for c in per_genre.values())
    rows = []
    for gid, c in sorted(per_genre.items(), key=lambda kv: -kv[1]["created"]):
        done = c["succeeded"] + c["failed"]
        rows.append({
            "genre": gid, "title": titles.get(gid, "(not recorded)" if gid == "unknown" else gid), "created": c["created"],
            "share_pct": round(100 * c["created"] / created, 1) if created else 0,
            "succeeded": c["succeeded"], "failed": c["failed"],
            "failure_pct": round(100 * c["failed"] / done, 1) if done else None,
            "likes": c["likes"],
        })
    for g in genres.GENRES:  # dramas nobody picked yet
        if g["id"] not in per_genre:
            rows.append({"genre": g["id"], "title": g["title"], "created": 0, "share_pct": 0, "succeeded": 0,
                         "failed": 0, "failure_pct": None, "likes": 0})
    done = sum(r["succeeded"] + r["failed"] for r in rows)
    return {
        "events": len(events),
        "first": events[0]["t"] if events else None,
        "last": events[-1]["t"] if events else None,
        "created": created,
        "finished": done,
        "failure_pct": round(100 * sum(r["failed"] for r in rows) / done, 1) if done else None,
        "with_story_idea_pct": round(100 * plots / plots_known, 1) if plots_known else None,
        "genres": rows,
        "failure_reasons": failures.most_common(),
        "timing_s": {k: {"median": round(statistics.median(v), 1), "max": round(max(v), 1), "n": len(v)}
                     for k, v in timings.items()},
        "rejected": dict(rejected),
        "emails": dict(emails),
        "devices": dict(devices),
        "per_day": dict(sorted(days.items())),
    }


def print_report(s):
    print(f"{s['events']} events, {s['first']} .. {s['last']}")
    story = "n/a" if s["with_story_idea_pct"] is None else f"{s['with_story_idea_pct']}%"
    print(f"{s['created']} generations started, {s['finished']} finished, failure rate {s['failure_pct']}%, "
          f"story idea typed in {story}\n")
    print(f"{'Drama':<26}{'picked':>7}{'share':>8}{'ok':>5}{'failed':>8}{'fail%':>7}{'likes':>7}")
    for r in s["genres"]:
        fail = "" if r["failure_pct"] is None else f"{r['failure_pct']}"
        print(f"{r['title'][:25]:<26}{r['created']:>7}{r['share_pct']:>7}%{r['succeeded']:>5}{r['failed']:>8}"
              f"{fail:>7}{r['likes']:>7}")
    sections = [
        ("Failure reasons (stage: code)", s["failure_reasons"]),
        ("Timings of successful runs, seconds (median / max / n)",
         [(k, f"{v['median']} / {v['max']} / {v['n']}") for k, v in s["timing_s"].items()]),
        ("Turned away", list(s["rejected"].items())),
        ("Emails", list(s["emails"].items())),
        ("Devices", list(s["devices"].items())),
        ("Generations per day", list(s["per_day"].items())),
    ]
    for title, items in sections:
        print(f"\n{title}:")
        for k, v in items or [("(none)", "")]:
            print(f"  {k}  {v}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="only events on or after this date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of tables")
    parser.add_argument("--include-tests", action="store_true", help="also count events marked \"test\"")
    args = parser.parse_args(argv)
    events = [e for e in data_log.load() if (not args.since or e.get("t", "") >= args.since)
              and (args.include_tests or not e.get("test"))]
    summary = summarise(events)
    if args.json:
        json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(summary)


if __name__ == "__main__":
    main()
