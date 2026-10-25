"""Read account quotas without starting conversations or exporting credentials."""
import copy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

WEEK_MINUTES = 7 * 24 * 60


class UsageUnavailable(Exception):
    def __init__(self, reason="unavailable", retry_after=60):
        super().__init__(reason)
        self.reason, self.retry_after = reason, retry_after


def clean_limits(items):
    """Persist/export only validated display fields, never upstream metadata."""
    if not isinstance(items, list):
        return []
    result = []
    for item in items[:16]:
        if not isinstance(item, dict):
            continue
        used, reset = item.get("used_percent"), item.get("resets_at")
        if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 1000:
            continue
        if reset is not None and (type(reset) not in (int, float) or not math.isfinite(reset) or not 0 < reset < 10**11):
            reset = None
        minutes = item.get("window_minutes", WEEK_MINUTES)
        if type(minutes) is not int or minutes not in (300, WEEK_MINUTES):
            continue
        result.append({"id": str(item.get("id", ""))[:80], "label": str(item.get("label", ""))[:80],
                       "window_minutes": minutes, "used_percent": used, "resets_at": reset})
    return result


def claude_limits(result):
    if not isinstance(result, dict) or not any(key in result for key in ("five_hour", "seven_day")):
        raise UsageUnavailable()
    limits = []
    keys = ["five_hour", "seven_day", *sorted(key for key in result if key.startswith("seven_day_"))]
    for key in keys[:16]:
        window = result.get(key)
        if not isinstance(window, dict):
            continue
        reset = window.get("resets_at")
        if isinstance(reset, str):
            try:
                parsed = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                reset = parsed.timestamp() if parsed.tzinfo else None
            except (ValueError, OverflowError, OSError):
                reset = None
        suffix = key.removeprefix("seven_day_").replace("_", " ").title() if key.startswith("seven_day_") else ""
        limits.append({"id": key, "label": "Claude" + (" " + suffix if suffix else ""),
                       "window_minutes": 300 if key == "five_hour" else WEEK_MINUTES,
                       "used_percent": window.get("utilization"), "resets_at": reset})
    return clean_limits(limits)


class NoUsageRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, *args, **kwargs):
        # Never forward the local account's bearer token to another origin.
        if response is not None:
            response.close()
        raise UsageUnavailable()


def read_claude_limits(credentials, timeout=12, now=None):
    try:
        value = json.loads(Path(credentials).read_text())
    except FileNotFoundError:
        raise UsageUnavailable("not_signed_in") from None
    account = value.get("claudeAiOauth") if isinstance(value, dict) else None
    if not isinstance(account, dict) or not isinstance(account.get("accessToken"), str) or not account["accessToken"]:
        raise UsageUnavailable("not_signed_in")
    expires = account.get("expiresAt")
    if type(expires) in (int, float) and expires / 1000 <= (time.time() if now is None else now):
        raise UsageUnavailable("auth_required")
    request = Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": "Bearer " + account["accessToken"], "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json", "User-Agent": "agent-hub/0.1"})
    try:
        with build_opener(NoUsageRedirect()).open(request, timeout=timeout) as response:
            payload = response.read(1_000_001)
            if len(payload) > 1_000_000:
                raise UsageUnavailable()
            return claude_limits(json.loads(payload))
    except HTTPError as error:
        if error.code in (401, 403):
            reason, delay = "auth_required", 60
        elif error.code == 429:
            reason, delay = "rate_limited", 300
            try:
                delay = min(3600, max(delay, int(error.headers.get("Retry-After", "300"))))
            except (ValueError, TypeError, AttributeError):
                pass
        else:
            reason, delay = "unavailable", 60
        error.close()
        raise UsageUnavailable(reason, delay) from None


def read_claude_cached_limits(path, credentials, now):
    """Reuse the local status line's report, with its real observation time.

    Its cache is shared only by the default Claude account on this host. Never
    reuse a report from before the current credential file was written.
    """
    if path is None:
        return None
    try:
        with Path(path).open('rb') as source:
            raw = source.read(1_000_001)
        if len(raw) > 1_000_000:
            return None
        value = json.loads(raw)
        stamp = value.get('fetchedAtMs') if isinstance(value, dict) else None
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            return None
        observed = stamp / 1000
        if not 0 < observed <= now + 60 or now - observed > 180 or observed < Path(credentials).stat().st_mtime:
            return None
        limits = claude_limits(value.get('utilization'))
        return (limits, observed) if limits else None
    except (OSError, ValueError, UsageUnavailable):
        return None


def weekly_limits(result):
    """The weekly window may be primary OR secondary, depending on the plan."""
    if not isinstance(result, dict):
        raise UsageUnavailable()
    buckets = result.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        fallback = result.get("rateLimits")
        if not isinstance(fallback, dict):
            raise UsageUnavailable()
        buckets = {fallback.get("limitId") or "codex": fallback}
    weekly = []
    for bucket_id, bucket in list(buckets.items())[:16]:
        if not isinstance(bucket, dict):
            continue
        name = str(bucket.get("limitName") or ("Codex" if bucket_id == "codex" else bucket_id))[:80]
        for slot in ("primary", "secondary"):
            window = bucket.get(slot)
            if not isinstance(window, dict) or window.get("windowDurationMins") != WEEK_MINUTES:
                continue
            used, reset = window.get("usedPercent"), window.get("resetsAt")
            if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 1000:
                continue
            if reset is not None and (type(reset) not in (int, float) or not math.isfinite(reset) or not 0 < reset < 10**11):
                reset = None
            weekly.append({"id": str(bucket_id)[:80] + ":" + slot, "label": name,
                           "used_percent": used, "resets_at": reset})
    return weekly


def read_codex_limits(executable, cwd, stop=None, timeout=15):
    binary = shutil.which(executable)
    if not binary:
        raise UsageUnavailable()
    env = dict(os.environ)
    for key in ("TMUX", "TMUX_PANE"):
        env.pop(key, None)
    # A separate stdio app-server connection reads the existing local account.
    # No thread/turn/login/control methods are sent, and credentials stay local.
    process = subprocess.Popen([binary, "app-server", "--stdio"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               cwd=cwd, env=env, start_new_session=True)
    selector = selectors.DefaultSelector()
    buffer = b""
    deadline = time.monotonic() + timeout

    def send(value):
        process.stdin.write(json.dumps(value).encode() + b"\n")
        process.stdin.flush()

    def receive(wanted):
        nonlocal buffer
        while time.monotonic() < deadline and not (stop and stop.is_set()):
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get("id") == wanted:
                    if "error" in value or not isinstance(value.get("result"), dict):
                        raise UsageUnavailable()
                    return value["result"]
            if not selector.select(min(.5, max(0, deadline - time.monotonic()))):
                continue
            part = os.read(process.stdout.fileno(), 65536)
            if not part:
                raise UsageUnavailable()
            buffer += part
            if len(buffer) > 1_000_000:
                raise UsageUnavailable()
        raise UsageUnavailable()

    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "agent_hub_usage", "version": "0.1"}}})
        receive(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read"})
        # Return only quota fields. Account IDs, credits, auth and reset-credit
        # capabilities are deliberately not sent to browsers or other hosts.
        return weekly_limits(receive(2))
    finally:
        selector.close()
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        process.stdout.close()


class UsageMonitor:
    provider = "codex"
    cache_name = "usage.json"
    items_key = "weekly"

    def __init__(self, state_dir, executable="codex", stop=None, clock=time.time):
        self.path = Path(state_dir) / self.cache_name
        self.executable, self.stop, self.clock = executable, stop or threading.Event(), clock
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.generation = 0
        self.thread = None
        self.delay = 60
        self.value = {"provider": self.provider, "status": "loading", self.items_key: [], "observed_at": 0,
                      "checked_at": 0, "stale": True}
        try:
            saved = json.loads(self.path.read_text())
            observed = float(saved["observed_at"])
            if not math.isfinite(observed) or not 0 < observed <= self.clock() + 60:
                raise ValueError("Invalid observation time")
            self.value.update({self.items_key: clean_limits(saved[self.items_key]),
                               "observed_at": observed, "status": "cached"})
        except (OSError, ValueError, TypeError, KeyError, UsageUnavailable):
            pass

    def snapshot(self):
        with self.lock:
            value = copy.deepcopy(self.value)
        value["stale"] = value["stale"] or self.clock() - value["observed_at"] > 180
        return value

    def refresh(self):
        with self.refresh_lock:
            self._refresh()

    def invalidate(self):
        with self.lock:
            self.generation += 1
            try:
                self.publish([], 0, 0)
            finally:
                self.value.update(status='loading', stale=True)

    def _refresh(self):
        with self.lock:
            generation = self.generation
        checked = self.clock()
        try:
            limits = clean_limits(self.read())
            with self.lock:
                if generation != self.generation:
                    return
                self.publish(limits, checked, self.clock())
            self.delay = 60
        except (OSError, ValueError, UsageUnavailable, subprocess.SubprocessError) as error:
            self.delay = error.retry_after if isinstance(error, UsageUnavailable) else 60
            with self.lock:
                if generation != self.generation:
                    return
                self.value.update(checked_at=checked, stale=True,
                                  reason=error.reason if isinstance(error, UsageUnavailable) else "unavailable",
                                  status="cached" if self.value["observed_at"] else "unavailable")

    def publish(self, limits, checked, observed):
        value = {"provider": self.provider, "status": "available" if limits else "not_reported",
                 self.items_key: clean_limits(limits), "observed_at": observed, "checked_at": checked, "stale": False}
        with self.lock:
            self.value = value
        temp = self.path.with_suffix(".json.tmp")
        with temp.open("w") as file:
            os.chmod(temp, 0o600)
            json.dump(value, file)
        os.replace(temp, self.path)

    def read(self):
        return read_codex_limits(self.executable, self.path.parent, self.stop)

    def start(self):
        def poll():
            while not self.stop.is_set():
                self.refresh()
                self.stop.wait(self.delay)
        self.thread = threading.Thread(target=poll, name=self.provider + "-usage", daemon=True)
        self.thread.start()


class ClaudeUsageMonitor(UsageMonitor):
    provider = "claude"
    cache_name = "claude-usage.json"
    items_key = "limits"

    def __init__(self, state_dir, stop=None, config_dir=None, clock=time.time, cache_path=None):
        super().__init__(state_dir, stop=stop, clock=clock)
        self.credentials = Path(config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / ".credentials.json"
        default_account = self.credentials.resolve() == (Path.home() / '.claude/.credentials.json').resolve()
        self.shared_cache = Path(cache_path) if cache_path else (
            Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'claude-statusline-usage.json' if default_account else None)
        self.next_api_read = 0

    def refresh(self):
        now = self.clock()
        try:
            credentials_changed = self.credentials.stat().st_mtime > self.value['observed_at'] > 0
        except OSError:
            credentials_changed = self.value['observed_at'] > 0
        if credentials_changed:
            with self.lock:
                self.value = {"provider": self.provider, "status": "loading", self.items_key: [],
                              "observed_at": 0, "checked_at": 0, "stale": True}
        cached = read_claude_cached_limits(self.shared_cache, self.credentials, now)
        if cached:
            limits, observed = cached
            if observed >= self.value['observed_at']:
                try:
                    self.publish(limits, now, observed)
                except OSError:
                    pass  # Keep a valid in-memory report when cache storage is full.
        elif now >= self.next_api_read:
            super().refresh()
            self.next_api_read = now + self.delay
        # Check local reports during upstream backoff, without making API calls.
        self.delay = 15

    def read(self):
        return read_claude_limits(self.credentials, now=self.clock())
