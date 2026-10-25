#!/usr/bin/env python3
"""Private-publisher Web Push relay. It has no agent or tailnet access."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlparse, urlencode

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pywebpush import webpush, WebPushException
import requests
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError


def push_label(value, limit):
    if not isinstance(value, str):
        raise ValueError('Invalid agent label')
    text = ' '.join(''.join(char if char.isprintable() else ' ' if char.isspace() else '' for char in value).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def notification_text(value, count):
    sources = value.get('agents', [])
    if not isinstance(sources, list) or len(sources) > 100:
        raise ValueError('Invalid agent labels')
    labels = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('Invalid agent label')
        name, host = push_label(source.get('name', ''), 96), push_label(source.get('host', ''), 48)
        label = name + (' · ' + host if host else '')
        if name and label not in labels:
            labels.append(label)
    total = value.get('agent_count', len(labels))
    if type(total) is not int or not len(labels) <= total <= 100000:
        raise ValueError('Invalid agent count')
    body = f'{count} new agent {"update" if count == 1 else "updates"}. Open your hub to review.'
    if not labels:
        return 'Agent Hub', body
    if total == 1 and count == 1:
        return labels[0], 'New update. Tap to open.'
    body = '\n'.join(labels[:3])
    if total > min(3, len(labels)):
        body += f'\n+{total - min(3, len(labels))} more agents'
    return f'Agent Hub · {count} {"update" if count == 1 else "updates"}', body

ROOT = Path(__file__).resolve().parent
PREFIX = "/agent-alerts"


class PushHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        if self.source_address is not None:
            return super()._new_conn()
        # This relay has an unreliable IPv6 route to Mozilla. An IPv4 source
        # bind skips IPv6 candidates without waiting for their connect timeout.
        # Keep DNS, TLS/SNI and certificate verification in urllib3, and fall
        # back to its normal dual-stack connection if IPv4 is unavailable.
        self.source_address = ('0.0.0.0', 0)
        try:
            return super()._new_conn()
        except (ConnectTimeoutError, NewConnectionError):
            pass
        finally:
            self.source_address = None
        return super()._new_conn()


class PushHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = PushHTTPSConnection


class PushAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        # urllib3's default registry is shared; change only this adapter.
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme, 'https': PushHTTPSPool}


class NoRedirectSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.mount('https://', PushAdapter())

    def request(self, *args, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(*args, **kwargs)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate_subscription(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid subscription")
    endpoint = value.get("endpoint", "")
    if not isinstance(endpoint, str):
        raise ValueError("Invalid subscription endpoint")
    url = urlparse(endpoint)
    host = url.hostname or ""
    allowed = host in ("fcm.googleapis.com", "web.push.apple.com") or host.endswith(".push.services.mozilla.com") or host.endswith(".notify.windows.com")
    if not allowed or url.scheme != "https" or url.port not in (None, 443) or url.username or url.password or url.fragment or len(endpoint) > 4096:
        raise ValueError("Unsupported push provider")
    keys = value.get("keys", {})
    if not isinstance(keys, dict):
        raise ValueError("Invalid subscription keys")
    for name, size in (("auth", 16), ("p256dh", 65)):
        key = keys.get(name, "")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", key):
            raise ValueError("Invalid subscription key")
        decoded = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        if len(decoded) != size:
            raise ValueError("Invalid subscription key")
        if name == "p256dh":
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded)
    return {"endpoint": endpoint, "keys": {key: keys[key] for key in ("auth", "p256dh")}}


class Relay:
    def __init__(self, directory, config):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config = config
        self.publish_lock = threading.Lock()
        self.pem = self.directory / "vapid.pem"
        if not self.pem.exists():
            private = ec.generate_private_key(ec.SECP256R1())
            with self.pem.open("xb") as file:
                file.write(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            self.pem.chmod(0o600)
        private = serialization.load_pem_private_key(self.pem.read_bytes(), password=None)
        self.public_key = base64.urlsafe_b64encode(private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode().rstrip("=")
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tickets(hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, subscription TEXT NOT NULL, management TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries(batch TEXT NOT NULL, device TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(batch,device));
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.directory / "push.sqlite", timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def pair(self):
        ticket = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("DELETE FROM tickets WHERE expires<?", (time.time(),))
            if db.execute("SELECT count(*) FROM tickets").fetchone()[0] >= 20:
                raise ValueError("Too many enrollment links; wait for one to expire")
            db.execute("INSERT INTO tickets VALUES(?,?)", (digest(ticket), time.time() + 600))
        return {"ticket": ticket}

    def enroll(self, value):
        subscription = validate_subscription(value.get("subscription"))
        ticket = value.get("ticket", "")
        if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,60}", ticket):
            raise ValueError("Invalid enrollment ticket")
        management = secrets.token_urlsafe(32)
        device = digest(subscription["endpoint"])
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("DELETE FROM tickets WHERE hash=? AND expires>?", (digest(ticket), time.time())).rowcount
            if not used:
                raise ValueError("Enrollment link expired or already used. Open Enable push in your hub again.")
            if db.execute("SELECT count(*) FROM devices").fetchone()[0] >= 24:
                raise ValueError("Device limit reached; remove an old subscription first")
            db.execute("INSERT OR REPLACE INTO devices VALUES(?,?,?,?)", (device, json.dumps(subscription), digest(management), time.time()))
        return {"device": device, "management": management, "hub_url": self.config["hub_url"]}

    def device(self, value):
        if not isinstance(value.get("device"), str) or not isinstance(value.get("management"), str):
            raise ValueError("Invalid device")
        with self.db() as db:
            row = db.execute("SELECT * FROM devices WHERE id=?", (value.get("device", ""),)).fetchone()
        if not row or not secrets.compare_digest(row["management"], digest(value.get("management", ""))):
            raise ValueError("Device is not enrolled; open Enable push in your hub")
        return dict(row)

    def remove(self, value):
        row = self.device(value)
        with self.db() as db:
            db.execute("DELETE FROM devices WHERE id=?", (row["id"],))
        return {"ok": True}

    def status(self):
        # Publisher-only health metadata; never expose subscriptions or capabilities.
        with self.db() as db:
            devices = db.execute('SELECT count(*) FROM devices').fetchone()[0]
            accepted = db.execute('SELECT max(created) FROM deliveries').fetchone()[0]
        return {"subscribers": devices, "last_accepted_at": accepted or 0}

    def send(self, row, batch, payload):
        with self.db() as db:
            if db.execute("SELECT 1 FROM deliveries WHERE batch=? AND device=?", (batch, row["id"])).fetchone():
                return True
        try:
            with NoRedirectSession() as session:
                response = webpush(subscription_info=json.loads(row["subscription"]), data=json.dumps(payload, ensure_ascii=False),
                                   vapid_private_key=str(self.pem), vapid_claims={"sub": self.config["contact"]},
                                   ttl=3600, timeout=8, requests_session=session,
                                   headers={"Urgency": "high", "Topic": "agent-updates"})
            if not 200 <= response.status_code < 300:
                return False
        except WebPushException as exc:
            response = exc.response
            if response is not None and response.status_code in (404, 410):
                with self.db() as db:
                    db.execute("DELETE FROM devices WHERE id=?", (row["id"],))
                return True
            return False
        except (requests.RequestException, ValueError):
            return False
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?)", (batch, row["id"], time.time()))
        return True

    def publish(self, value):
        batch = value.get("id", "")
        if not isinstance(batch, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", batch):
            raise ValueError("Invalid update ID")
        count = value.get("count", 1)
        if not isinstance(count, int) or not 1 <= count <= 100:
            raise ValueError("Invalid update count")
        # Only agent/host labels are accepted; message bodies and destinations
        # remain relay-controlled. Conversation content stays on the private hub.
        agent = str(value.get("agent_id", ""))[:240]
        notice = value.get('notice_id', '')
        if not isinstance(notice, str) or (notice and not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', notice)):
            raise ValueError('Invalid notice ID')
        title, body = notification_text(value, count)
        payload = {"title": title, "body": body,
                   "url": self.config["hub_url"] + "/?" + urlencode({"updates": "1", "agent": agent, **({'notice': notice} if notice else {})}), "id": batch}
        with self.publish_lock:
            with self.db() as db:
                rows = [dict(row) for row in db.execute("SELECT * FROM devices")]
                db.execute("DELETE FROM deliveries WHERE created<?", (time.time() - 7 * 86400,))
            with ThreadPoolExecutor(max_workers=8) as pool:
                success = list(pool.map(lambda row: self.send(row, batch, payload), rows))
        if not all(success):
            raise ConnectionError("Push provider unavailable; retry delivery")
        return {"ok": True, **self.status()}

    def test(self, value):
        row = self.device(value)
        # A device can request only its own generic test message.
        batch = "test-" + row["id"] + "-" + str(int(time.time() // 30))
        payload = {"title": "Agent Hub", "body": "Push is connected. Future agent updates will appear here.",
                   "url": self.config["hub_url"] + "/#updates=1", "id": batch}
        if not self.send(row, batch, payload):
            raise ConnectionError("Push provider unavailable; try again shortly")
        return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentAlerts/1"

    def log_message(self, *args):
        pass  # Enrollment capabilities and subscriptions never enter logs.

    @property
    def relay(self):
        return self.server.relay

    def respond(self, value, status=200, mime="application/json"):
        body = json.dumps(value).encode() if mime == "application/json" else value
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == PREFIX + "/info":
            return self.respond({"public_key": self.relay.public_key})
        files = {PREFIX + "/": "index.html", PREFIX + "/app.js": "app.js", PREFIX + "/style.css": "style.css",
                 PREFIX + "/sw.js": "sw.js", PREFIX + "/manifest.webmanifest": "manifest.webmanifest", PREFIX + "/icon.svg": "icon.svg"}
        name = files.get(path)
        if not name:
            return self.respond({"error": "Not found"}, 404)
        return self.respond((ROOT / "static" / name).read_bytes(), mime=mimetypes.guess_type(name)[0] or "application/octet-stream")

    def do_POST(self):
        path = urlparse(self.path).path.removeprefix(PREFIX)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 10000:
                raise ValueError("Invalid request size")
            if path in ("/pair", "/publish", "/status"):
                if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.relay.config["publisher_token"]):
                    return self.respond({"error": "Unauthorized"}, 403)
            else:
                if self.headers.get("Origin") != self.relay.config["origin"] or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.respond({"error": "Origin mismatch"}, 403)
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("Expected an object")
            if path == "/pair":
                result = self.relay.pair()
            elif path == "/publish":
                result = self.relay.publish(value)
            elif path == "/status":
                result = self.relay.status()
            elif path == "/subscribe":
                result = self.relay.enroll(value)
            elif path == "/unsubscribe":
                result = self.relay.remove(value)
            elif path == "/test":
                result = self.relay.test(value)
            else:
                return self.respond({"error": "Not found"}, 404)
            return self.respond(result)
        except (ValueError, TypeError, KeyError):
            return self.respond({"error": "Request not accepted. Enrollment links last 10 minutes; open Enable push in your hub again if needed."}, 400)
        except ConnectionError:
            return self.respond({"error": "Push provider unavailable. Updates remain in your hub; delivery will retry."}, 503)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", default="/var/lib/agent-hub-push")
    parser.add_argument("--port", type=int, default=14575)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(Path(args.config).read_text())
    assert len(config["publisher_token"]) >= 32
    assert urlparse(config["origin"]).scheme == "https"
    assert urlparse(config["hub_url"]).scheme in ("http", "https")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.relay = Relay(args.state_dir, config)
    server.serve_forever()


if __name__ == "__main__":
    main()
