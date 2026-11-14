"""Report kiosk results by rehearsal scenario.

Reads the attempts the backend saved (SQLite, or the running API) and reports, per
scenario: outcome counts, attack acceptance, genuine rejection, recapture rate,
latency percentiles and deadline misses. Optionally re-scores the saved per-frame
evidence at other thresholds so threshold changes can be argued from data.

Standard library only. Run from the project root:

    .venv/bin/python scripts/evaluate.py                         # data/kiosk.sqlite
    .venv/bin/python scripts/evaluate.py --db data/final/kiosk.sqlite --out results/final
    .venv/bin/python scripts/evaluate.py --api http://127.0.0.1:8000
    .venv/bin/python scripts/evaluate.py --since 2026-11-14T09:00:00Z --sweep

Use tuning captures (HID_DATA_DIR=data/tuning) for --sweep and threshold changes, and
report final numbers only from a separate database (HID_DATA_DIR=data/final).
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from math import ceil, isfinite
from pathlib import Path
from statistics import fmean, median
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Keep the report in step with the thresholds the backend actually uses.
    from app import policy as _policy
    DEFAULT_THRESHOLDS = {
        "live_pass": _policy.LIVE_PASS, "spoof_deny": _policy.SPOOF_DENY,
        "match_pass": _policy.MATCH_PASS, "mismatch_deny": _policy.MISMATCH_DENY,
    }
except ImportError:  # pragma: no cover - only when run outside the repository
    DEFAULT_THRESHOLDS = {"live_pass": 0.9, "spoof_deny": 0.2, "match_pass": 0.5, "mismatch_deny": 0.3}

OUTCOMES = ("ALLOW", "REQUEST_VERIFICATION", "DENY")
SCENARIOS = ("genuine", "poor_light", "wrong_identity", "print_photo", "screen_photo", "video_replay")
ATTACKS = ("print_photo", "screen_photo", "video_replay")
GENUINE = ("genuine", "poor_light")
CAPTURE_REASONS = ("no_face", "multiple_faces", "poor_quality", "blink_required")
UI_DEADLINE_MS = 2000
TARGET_ATTEMPTS = 20


# ---------------------------------------------------------------- loading

def default_db():
    return Path(os.environ.get("HID_DATA_DIR", ROOT / "data")) / "kiosk.sqlite"


def load_sqlite(path):
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"No database at {path}. Run the kiosk first or pass --db / --api.")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        has_jev = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='second_checks'").fetchone()
        check_column = ", s.result AS second_check" if has_jev else ""
        check_join = "LEFT JOIN second_checks s USING (attempt_id)" if has_jev else ""
        rows = db.execute(f"""
            SELECT a.*, p.name AS participant_name {check_column}
            FROM attempts a LEFT JOIN participants p USING (participant_id)
            {check_join}
            ORDER BY a.created_at, a.rowid
        """).fetchall()
    finally:
        db.close()
    attempts = []
    for row in rows:
        item = dict(row)
        for field in ("frames", "stage_ms"):
            item[field] = json.loads(item[field] or ("[]" if field == "frames" else "{}"))
        for field in ("deadline_missed", "ui_deadline_missed"):
            if item[field] is not None:
                item[field] = bool(item[field])
        if item.get("second_check"):
            item["second_check"] = json.loads(item["second_check"])
        attempts.append(item)
    return attempts


def load_api(base, limit=1000):
    base = base.rstrip("/")
    def get(path):
        with urlopen(Request(base + path, headers={"Accept": "application/json"}), timeout=10) as response:
            return json.load(response)
    names = {p["participant_id"]: p["name"] for p in get("/api/participants")}
    attempts = get(f"/api/attempts?limit={limit}")
    for item in attempts:
        item["participant_name"] = names.get(item["participant_id"])
    attempts.sort(key=lambda a: a["created_at"])
    return attempts


def in_window(attempt, since, until):
    created = attempt["created_at"]
    return (since is None or created >= since) and (until is None or created < until)


# ---------------------------------------------------------------- statistics

def percentile(values, p):
    """Nearest-rank percentile: the reported value is one that was actually observed."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(p / 100 * len(ordered)) - 1)]


def latency(values):
    values = [v for v in values if v is not None]
    return {
        "n": len(values),
        "median": median(values) if values else None,
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }


def rate(part, whole):
    return None if whole == 0 else part / whole


def summarize(group):
    n = len(group)
    outcomes = Counter(a["outcome"] for a in group)
    reasons = Counter(a["reason"] for a in group)
    capture_retries = sum(reasons[r] for r in CAPTURE_REASONS)
    server_missed = sum(bool(a["deadline_missed"]) for a in group)
    ui_missed = sum(bool(a.get("ui_deadline_missed")) for a in group)
    timed = [a for a in group if a.get("click_to_render_ms") is not None]
    within = sum(
        1 for a in timed if not a.get("ui_deadline_missed") and a["click_to_render_ms"] <= UI_DEADLINE_MS
    )
    stages = {}
    for stage in ("capture", "detect", "liveness", "match", "policy"):
        values = [a["stage_ms"].get(stage) for a in group if a["stage_ms"].get(stage) is not None]
        stages[stage] = median(values) if values else None
    return {
        "n": n,
        "outcomes": {o: outcomes[o] for o in OUTCOMES},
        "allow_rate": rate(outcomes["ALLOW"], n),
        "recapture_rate": rate(outcomes["REQUEST_VERIFICATION"], n),
        "deny_rate": rate(outcomes["DENY"], n),
        "reasons": dict(reasons.most_common()),
        "capture_retries": capture_retries,
        "server_deadline_missed": server_missed,
        "ui_deadline_missed": ui_missed,
        "client_timed": len(timed),
        "within_ui_deadline": within,
        "server_ms": latency([a["elapsed_ms"] for a in group]),
        "click_to_render_ms": latency([a.get("click_to_render_ms") for a in group]),
        "stage_median_ms": stages,
        "participants": len({a["participant_id"] for a in group}),
    }


def summarize_jev(attempts):
    result = {}
    for scenario in SCENARIOS:
        checks = [a["second_check"] for a in attempts if a["scenario"] == scenario and a.get("second_check")]
        if checks:
            result[scenario] = {
                "n": len(checks),
                "denied": sum(c["outcome"] == "DENY" for c in checks),
                "recapture": sum(c["outcome"] == "REQUEST_VERIFICATION" for c in checks),
                "second_check_ms": latency([c["second_check_ms"] for c in checks]),
                "jev_ms": latency([c["jev_ms"] for c in checks]),
            }
    return result


def evaluate(attempts):
    by_scenario = {s: [a for a in attempts if a["scenario"] == s] for s in SCENARIOS}
    labeled = [a for a in attempts if a["scenario"] != "unlabeled"]
    attacks = [a for s in ATTACKS for a in by_scenario[s]]
    genuine = [a for s in GENUINE for a in by_scenario[s]]
    report = {
        "total_attempts": len(attempts),
        "unlabeled": len(attempts) - len(labeled),
        "scenarios": {s: summarize(group) for s, group in by_scenario.items()},
        "all_attacks": summarize(attacks),
        "all_genuine": summarize(genuine),
        "all_labeled": summarize(labeled),
        "accepted_attacks": [
            {k: a.get(k) for k in ("attempt_id", "created_at", "scenario", "participant_name",
                                   "liveness_score", "match_score", "elapsed_ms")}
            for a in attacks + by_scenario["wrong_identity"] if a["outcome"] == "ALLOW"
        ],
        "thresholds": DEFAULT_THRESHOLDS,
        "jev_second_checks": summarize_jev(labeled),
    }
    return report


# ---------------------------------------------------------------- threshold sweep

def _valid(value, low, high):
    return value is not None and isfinite(value) and low <= value <= high


def redecide(frames, t):
    """Mirror app.policy.decide_frames with explicit thresholds. Returns the outcome."""
    if len(frames) != 3 or any(not f["face_found"] or not f["quality_ok"] for f in frames):
        return None  # capture issue or deadline: thresholds do not change the outcome
    lives = [f["liveness_score"] for f in frames]
    matches = [f["match_score"] for f in frames]
    if any(_valid(s, 0, 1) and s <= t["spoof_deny"] for s in lives):
        return "DENY"
    if not all(_valid(s, 0, 1) for s in lives):
        return "REQUEST_VERIFICATION"
    live = min(lives)
    if not all(_valid(s, -1, 1) for s in matches):
        return "REQUEST_VERIFICATION"
    match = fmean(matches)
    if match <= t["mismatch_deny"]:
        return "DENY"
    if live < t["live_pass"] or match < t["match_pass"]:
        return "REQUEST_VERIFICATION"
    return "ALLOW"


def sweep(attempts, key, values):
    rows = []
    usable = [
        a for a in attempts
        if a["scenario"] in SCENARIOS and a["reason"] != "blink_required"
        and redecide(a["frames"], DEFAULT_THRESHOLDS)
    ]
    for value in values:
        t = {**DEFAULT_THRESHOLDS, key: value}
        outcome = {a["attempt_id"]: redecide(a["frames"], t) for a in usable}
        def share(scenarios, wanted):
            group = [a for a in usable if a["scenario"] in scenarios]
            return rate(sum(outcome[a["attempt_id"]] == wanted for a in group), len(group)), len(group)
        rows.append({
            key: value,
            "attack_accept": share(ATTACKS, "ALLOW"),
            "wrong_id_accept": share(("wrong_identity",), "ALLOW"),
            "genuine_allow": share(("genuine",), "ALLOW"),
            "genuine_deny": share(("genuine",), "DENY"),
            "poor_light_deny": share(("poor_light",), "DENY"),
        })
    return rows


# ---------------------------------------------------------------- formatting

def pct(value):
    return "—" if value is None else f"{value * 100:.0f}%"


def ms(value):
    return "—" if value is None else f"{value:.0f}"


def frac(part, whole):
    return f"{part}/{whole} ({pct(rate(part, whole))})" if whole else "—"


def markdown(report, source, sweeps=None):
    out = []
    w = out.append
    s = report["scenarios"]
    attacks, genuine = report["all_attacks"], report["all_genuine"]
    labeled = report["all_labeled"]
    w("# Kiosk evaluation\n")
    w(f"Source: `{source}`. Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.\n")
    t = report["thresholds"]
    w(f"Thresholds: liveness pass {t['live_pass']}, spoof deny {t['spoof_deny']}, "
      f"match pass {t['match_pass']}, mismatch deny {t['mismatch_deny']}.\n")
    if report["unlabeled"]:
        w(f"> **{report['unlabeled']} attempt(s) are unlabeled** and excluded below. "
          "Label them in the kiosk's recent attempts table.\n")

    w("## Headline\n")
    w("| Measure | Result |")
    w("| --- | --- |")
    w(f"| Attacks accepted (print, screen, replay) | {frac(attacks['outcomes']['ALLOW'], attacks['n'])} |")
    for name in ATTACKS:
        g = s[name]
        w(f"| — {name.replace('_', ' ')} accepted | {frac(g['outcomes']['ALLOW'], g['n'])} |")
    w(f"| Wrong identity accepted | {frac(s['wrong_identity']['outcomes']['ALLOW'], s['wrong_identity']['n'])} |")
    w(f"| Genuine allowed first time | {frac(s['genuine']['outcomes']['ALLOW'], s['genuine']['n'])} |")
    w(f"| Genuine denied (wrongly rejected) | {frac(s['genuine']['outcomes']['DENY'], s['genuine']['n'])} |")
    w(f"| Genuine asked to recapture | {frac(s['genuine']['outcomes']['REQUEST_VERIFICATION'], s['genuine']['n'])} |")
    w(f"| Poor light denied | {frac(s['poor_light']['outcomes']['DENY'], s['poor_light']['n'])} |")
    w(f"| Server latency median / p95 / max (ms) | {ms(labeled['server_ms']['median'])} / "
      f"{ms(labeled['server_ms']['p95'])} / {ms(labeled['server_ms']['max'])} |")
    w(f"| Click-to-render median / p95 / max (ms) | {ms(labeled['click_to_render_ms']['median'])} / "
      f"{ms(labeled['click_to_render_ms']['p95'])} / {ms(labeled['click_to_render_ms']['max'])} |")
    w(f"| Rendered within {UI_DEADLINE_MS} ms | {frac(labeled['within_ui_deadline'], labeled['client_timed'])} |")
    w(f"| Server deadline misses | {frac(labeled['server_deadline_missed'], labeled['n'])} |")
    w(f"| UI deadline misses | {frac(labeled['ui_deadline_missed'], labeled['n'])} |")
    w(f"| Capture retries (face, lighting or blink) | "
      f"{frac(labeled['capture_retries'], labeled['n'])} |\n")

    if report["jev_second_checks"]:
        w("## Jev second checks\n")
        w("| Scenario | Checks | Deny | Recapture | Total ms median / p95 / max | TypeSafe ms median / p95 / max |")
        w("| --- | ---: | ---: | ---: | --- | --- |")
        for name, g in report["jev_second_checks"].items():
            total = " / ".join(ms(g["second_check_ms"][metric]) for metric in ("median", "p95", "max"))
            hosted = " / ".join(ms(g["jev_ms"][metric]) for metric in ("median", "p95", "max"))
            w(f"| {name} | {g['n']} | {g['denied']} | {g['recapture']} | {total} | {hosted} |")
        w("")

    w("## By scenario\n")
    w("| Scenario | n | ALLOW | REQ. VERIF. | DENY | Capture retries | Server misses | UI misses "
      "| Server ms median / p95 / max | Click-to-render ms median / p95 / max |")
    w("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |")
    for name in SCENARIOS:
        g = s[name]
        flag = " ⚠" if 0 < g["n"] < TARGET_ATTEMPTS else (" ✗" if g["n"] == 0 else "")
        o = g["outcomes"]
        w(f"| {name}{flag} | {g['n']} | {o['ALLOW']} | {o['REQUEST_VERIFICATION']} | {o['DENY']} "
          f"| {g['capture_retries']} | {g['server_deadline_missed']} | {g['ui_deadline_missed']} "
          f"| {ms(g['server_ms']['median'])} / {ms(g['server_ms']['p95'])} / {ms(g['server_ms']['max'])} "
          f"| {ms(g['click_to_render_ms']['median'])} / {ms(g['click_to_render_ms']['p95'])} / "
          f"{ms(g['click_to_render_ms']['max'])} |")
    w(f"\n⚠ fewer than {TARGET_ATTEMPTS} attempts. ✗ not collected yet.\n")

    w("## Reasons by scenario\n")
    for name in SCENARIOS:
        if s[name]["n"]:
            reasons = ", ".join(f"{r} {c}" for r, c in s[name]["reasons"].items())
            w(f"- **{name}**: {reasons}")
    w("")

    w("## Median stage times (ms, labeled attempts)\n")
    st = labeled["stage_median_ms"]
    w("| capture | detect | liveness | match | policy |")
    w("| ---: | ---: | ---: | ---: | ---: |")
    w(f"| {ms(st['capture'])} | {ms(st['detect'])} | {ms(st['liveness'])} | {ms(st['match'])} | {ms(st['policy'])} |\n")

    if report["accepted_attacks"]:
        w("## Accepted attacks and wrong identities (explain each in the demo)\n")
        w("| Time | Scenario | Enrolled as | Liveness | Match | Server ms | Attempt |")
        w("| --- | --- | --- | ---: | ---: | ---: | --- |")
        for a in report["accepted_attacks"]:
            live = "—" if a["liveness_score"] is None else f"{a['liveness_score']:.3f}"
            match = "—" if a["match_score"] is None else f"{a['match_score']:.3f}"
            w(f"| {a['created_at']} | {a['scenario']} | {a['participant_name'] or '?'} | {live} | {match} "
              f"| {a['elapsed_ms']} | `{a['attempt_id'][:8]}` |")
        w("")

    for title, rows in (sweeps or {}).items():
        key = next(iter(rows[0])) if rows else title
        w(f"## Threshold sweep: {key}\n")
        w("Re-scores saved frame evidence. Attempts with capture issues or deadline misses are left out. "
          "Use only on tuning captures.\n")
        w(f"| {key} | Attacks accepted | Wrong ID accepted | Genuine allowed | Genuine denied | Poor light denied |")
        w("| ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in rows:
            cells = [f"{pct(row[c][0])} (n={row[c][1]})" for c in
                     ("attack_accept", "wrong_id_accept", "genuine_allow", "genuine_deny", "poor_light_deny")]
            marker = " ←" if row[key] == DEFAULT_THRESHOLDS[key] else ""
            w(f"| {row[key]}{marker} | " + " | ".join(cells) + " |")
        w("")
    return "\n".join(out)


def write_csv(path, attempts):
    fields = ["created_at", "attempt_id", "participant_name", "participant_id", "scenario", "outcome",
              "reason", "liveness_score", "match_score", "elapsed_ms", "deadline_missed",
              "click_to_render_ms", "ui_deadline_missed", "capture_ms", "detect_ms", "liveness_ms",
              "match_ms", "policy_ms", "frame_liveness", "frame_match",
              "second_check_outcome", "second_check_reason", "second_check_ms", "jev_ms", "jev_label", "jev_live_probability"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for a in attempts:
            row = dict(a)
            for stage in ("capture", "detect", "liveness", "match", "policy"):
                row[f"{stage}_ms"] = a["stage_ms"].get(stage)
            row["frame_liveness"] = ";".join("" if f["liveness_score"] is None else f"{f['liveness_score']:.4f}"
                                             for f in a["frames"])
            row["frame_match"] = ";".join("" if f["match_score"] is None else f"{f['match_score']:.4f}"
                                          for f in a["frames"])
            check = a.get("second_check") or {}
            row.update(second_check_outcome=check.get("outcome"), second_check_reason=check.get("reason"))
            for field in ("second_check_ms", "jev_ms", "jev_label", "jev_live_probability"):
                row[field] = check.get(field)
            writer.writerow(row)


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, help="SQLite file (default: $HID_DATA_DIR/kiosk.sqlite or data/kiosk.sqlite)")
    source.add_argument("--api", help="Read from a running kiosk instead, e.g. http://127.0.0.1:8000")
    parser.add_argument("--since", help="Only attempts created at or after this ISO time (UTC, e.g. 2026-11-14T09:00:00Z)")
    parser.add_argument("--until", help="Only attempts created before this ISO time")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "latest",
                        help="Folder for summary.md, metrics.json and attempts.csv (default results/latest)")
    parser.add_argument("--no-write", action="store_true", help="Print only, do not write files")
    parser.add_argument("--sweep", action="store_true", help="Add threshold sweeps (use on tuning captures only)")
    args = parser.parse_args(argv)

    if args.api:
        attempts, label = load_api(args.api), args.api
    else:
        db = args.db or default_db()
        attempts, label = load_sqlite(db), str(db)
    attempts = [a for a in attempts if in_window(a, args.since, args.until)]
    if not attempts:
        raise SystemExit("No attempts in this window.")

    report = evaluate(attempts)
    sweeps = None
    if args.sweep:
        sweeps = {
            "live_pass": sweep(attempts, "live_pass", [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98]),
            "spoof_deny": sweep(attempts, "spoof_deny", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]),
            "match_pass": sweep(attempts, "match_pass", [0.35, 0.4, 0.45, 0.5, 0.55, 0.6]),
        }
        report["sweeps"] = sweeps
    text = markdown(report, label, sweeps)
    print(text)

    if not args.no_write:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.md").write_text(text + "\n", encoding="utf-8")
        (args.out / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        write_csv(args.out / "attempts.csv", attempts)
        print(f"\nWrote {args.out / 'summary.md'}, metrics.json and attempts.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
