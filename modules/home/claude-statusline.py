"""Claude Code status line: working dir, model, and 5h / weekly usage limits.

Claude Code passes a JSON payload on stdin. It carries rate_limits only after
the session has made at least one API request (the numbers come from
anthropic-ratelimit-unified-* response headers), so a freshly started session
has none. Fall back to the cachedUsageUtilization block in ~/.claude.json,
which Claude refreshes in the background, so the line is never blank.

The two payloads disagree on shape, and both are handled. Verified by logging a
real stdin payload; do not infer these from the bundle:
  stdin  five_hour = {used_percentage: 77, resets_at: 1785966000}   epoch seconds
  cache  five_hour = {utilization: 45, resets_at: "2026-08-04T21:00:00+00:00"}
"""

import json
import os
import sys
import time

RESET = "\x1b[0m"
DIM = "\x1b[2m"

# Rising effort reads as rising spend, so the ramp matches the limit colors.
EFFORT_COLORS = {
    "low": "2",  # dim
    "medium": "34",  # blue
    "high": "36",  # cyan
    "xhigh": "33",  # yellow
    "max": "1;33",  # bold yellow
}

EFFORT_SHORT = {"low": "L", "medium": "M", "high": "H", "xhigh": "XH", "max": "MAX"}

# Eighth-width blocks: 4 cells resolve 32 levels, so the bar stays as short as
# the "46%" it replaces while reading at a glance.
#
# The unfilled portion is spaces over a background track, NOT a shade glyph like
# the light shade. A partial block paints only the left fraction of its cell, so
# putting a glyph next to it left a visible seam splitting filled from unfilled;
# with a background track that leftover fraction renders as track and the bar
# stays continuous. Spaces also dodge any font-fallback width mismatch.
PARTIALS = "▏▎▍▌▋▊▉"  # 1/8 .. 7/8
FULL = "█"
TRACK_BG = "48;5;238"  # dark grey track
BAR_WIDTH = 4


def bar(pct, width=BAR_WIDTH):
    pct = max(0.0, min(100.0, float(pct)))
    # Floor, not round, so a completely full bar means exactly 100% rather than
    # anything above 98.5%; and any non-zero usage shows at least a sliver.
    eighths = int(pct / 100.0 * width * 8)
    if pct >= 100:
        eighths = width * 8
    elif pct > 0 and eighths == 0:
        eighths = 1
    full, rest = divmod(eighths, 8)
    cells = FULL * full + (PARTIALS[rest - 1] if rest else "")
    cells += " " * (width - len(cells))
    return color(severity_code(pct) + ";" + TRACK_BG, cells)


def color(code, text):
    return "\x1b[" + code + "m" + text + RESET


def severity_code(pct):
    if pct >= 90:
        return "1;31"  # bold red
    if pct >= 75:
        return "31"  # red
    if pct >= 50:
        return "33"  # yellow
    return "32"  # green


def read_stdin():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def limits_from_payload(payload):
    limits = payload.get("rate_limits")
    return limits if isinstance(limits, dict) else {}


def load_cache():
    """Return (utilization, age_seconds). Age is None when unknown.

    Measured in practice at anything from ~1 minute to ~3 hours old, so anything
    sourced from here is flagged stale rather than shown as live.
    """
    path = os.path.join(os.path.expanduser("~"), ".claude.json")
    try:
        with open(path) as handle:
            cached = json.load(handle).get("cachedUsageUtilization") or {}
    except Exception:
        return {}, None
    block = cached.get("utilization")
    if not isinstance(block, dict):
        return {}, None
    fetched = cached.get("fetchedAtMs")
    age = time.time() - fetched / 1000.0 if isinstance(fetched, (int, float)) else None
    return block, age


def scoped_limit(cache_block):
    """The per-model weekly cap (kind weekly_scoped).

    Absent from the stdin payload, which only carries five_hour, seven_day,
    seven_day_overage_included and overage, so this is cache-only.
    """
    entries = cache_block.get("limits")
    if not isinstance(entries, list):
        return None, None
    scoped = [
        e
        for e in entries
        if isinstance(e, dict) and e.get("kind") == "weekly_scoped" and percent_of(e) is not None
    ]
    if not scoped:
        return None, None
    active = [e for e in scoped if e.get("is_active")]
    entry = active[0] if active else max(scoped, key=percent_of)
    model = ((entry.get("scope") or {}).get("model") or {}).get("display_name")
    return entry, model


def as_epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Imported lazily: only the cache path uses ISO timestamps, and the
        # stdin path (the common case) never reaches here.
        from datetime import datetime

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def time_left(resets_at):
    epoch = as_epoch(resets_at)
    if epoch is None:
        return None
    remaining = int(epoch - time.time())
    if remaining <= 0:
        return "now"
    days, rest = divmod(remaining, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return str(days) + "d" + str(hours) + "h"
    if hours:
        return str(hours) + "h" + str(minutes) + "m"
    return str(minutes) + "m"


def percent_of(entry):
    if not isinstance(entry, dict):
        return None
    # Three spellings in play: used_percentage (stdin), utilization (cache
    # five_hour/seven_day), percent (cache limits array).
    for key in ("used_percentage", "utilization", "percent"):
        value = entry.get(key)
        if isinstance(value, (int, float)):
            return value
    return None


def weekly_segment(overall, scoped, stale_overall, stale_scoped):
    """Collapse the weekly caps into one "wk 46/100%" segment.

    weekly_all and weekly_scoped share a reset instant (they differ by
    microseconds), so printing it twice was pure noise. If they ever diverge by
    more than a minute, fall back to separate segments.
    """
    pct_all = percent_of(overall)
    pct_scoped = percent_of(scoped)
    if pct_all is None or pct_scoped is None:
        return None

    left_all = as_epoch((overall or {}).get("resets_at"))
    left_scoped = as_epoch((scoped or {}).get("resets_at"))
    if left_all and left_scoped and abs(left_all - left_scoped) > 60:
        return None

    segment = color("2", "wk ") + bar(pct_all) + " " + color(severity_code(pct_all), str(int(round(pct_all))))
    segment += color("2", "/") + bar(pct_scoped) + " "
    segment += color(severity_code(pct_scoped), str(int(round(pct_scoped))) + "%")
    if stale_overall or stale_scoped:
        segment += color("2", "~")
    left = time_left((scoped or {}).get("resets_at"))
    if left:
        segment += color("2", " " + left)
    return segment


def limit_segment(label, entry, stale=False):
    pct = percent_of(entry)
    if pct is None:
        return None
    segment = color("2", label + " ") + bar(pct) + " "
    segment += color(severity_code(pct), str(int(round(pct))) + "%")
    if stale:
        segment += color("2", "~")  # value is from the cache, may lag by hours
    left = time_left(entry.get("resets_at"))
    if left:
        segment += color("2", " " + left)
    return segment


def workspace_segment(payload):
    workspace = payload.get("workspace") or {}
    current = workspace.get("current_dir") or os.getcwd()
    project = workspace.get("project_dir")
    name = os.path.basename(project or current) or current
    if project and os.path.abspath(current) != os.path.abspath(project):
        suffix = os.path.relpath(current, project)
        if suffix and suffix != ".":
            name = name + "/" + suffix
    return color("36", name)


def main():
    payload = read_stdin()

    cache_block, cache_age = load_cache()
    cache_stale = cache_age is None or cache_age > 300

    # Fall back on a missing percentage, not just a missing key: the payload can
    # carry a five_hour object whose shape we fail to read, and treating that as
    # present silently drops the whole segment.
    limits = limits_from_payload(payload)
    live = percent_of(limits.get("five_hour")) is not None or percent_of(limits.get("seven_day")) is not None
    if not live:
        limits = cache_block

    parts = [workspace_segment(payload)]

    # Model and effort share a segment: "Fable 5 H" rather than a separator
    # between them. Effort is only sent for models exposing an effort control.
    model = (payload.get("model") or {}).get("display_name")
    effort = (payload.get("effort") or {}).get("level")
    if model or effort:
        segment = color("35", model) if model else ""
        if effort:
            short = EFFORT_SHORT.get(effort, effort[:3].upper())
            segment += (" " if segment else "") + color(EFFORT_COLORS.get(effort, "34"), short)
        parts.append(segment)

    limits_stale = not live and cache_stale
    segment = limit_segment("5h", limits.get("five_hour"), stale=limits_stale)
    if segment:
        parts.append(segment)

    scoped, scoped_model = scoped_limit(cache_block)
    merged = weekly_segment(limits.get("seven_day"), scoped, limits_stale, cache_stale)
    if merged:
        parts.append(merged)
    else:
        for label, entry, stale in (
            ("wk", limits.get("seven_day"), limits_stale),
            (scoped_model or "model", scoped, cache_stale),
        ):
            segment = limit_segment(label, entry, stale=stale)
            if segment:
                parts.append(segment)

    sys.stdout.write(color("2", " · ").join(parts))


if __name__ == "__main__":
    main()
