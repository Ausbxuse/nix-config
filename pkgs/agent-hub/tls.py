"""Direct TLS keeps the browser's real tailnet address for access checks.

Only public certificates are downloaded. Each host's private key stays on that
host. A renewed certificate is checked before atomic installation, and new
connections pick it up without restarting the hub or its supervisor.
"""

from http.server import ThreadingHTTPServer
import logging
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse
from urllib.request import build_opener, HTTPSHandler, ProxyHandler

from federation import NoRedirect


def validate_config(config):
    settings = config.get('tls')
    if settings is None:
        return
    if not isinstance(settings, dict) or not config.get('tailnet_bind'):
        raise ValueError('TLS requires a tailnet listener')
    if type(settings.get('port', 8443)) is not int or not 1024 <= settings.get('port', 8443) <= 65535:
        raise ValueError('TLS port must be an unprivileged TCP port')
    for key in ('cert_file', 'key_file'):
        if not isinstance(settings.get(key), str) or not Path(settings[key]).is_absolute():
            raise ValueError(f'TLS {key} must be an absolute path outside the Nix store')
        if settings[key].startswith('/nix/store/'):
            raise ValueError('Keep TLS files outside the Nix store')
    hostname = settings.get('server_name', '')
    if not isinstance(hostname, str) or not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', hostname):
        raise ValueError('TLS requires a DNS server_name')
    if settings.get('renewal_url'):
        url = urlparse(settings['renewal_url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('TLS renewal_url must use trusted HTTPS')


def context_for(settings, cert_file=None):
    key = Path(settings['key_file'])
    if key.stat().st_mode & 0o077:
        raise ValueError('TLS private key must have mode 600')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_file or settings['cert_file'], key)
    return context


def install_certificate(settings, content):
    """Reject a wrong hostname, untrusted chain, expired cert, or wrong key."""
    if len(content) > 65536 or b'PRIVATE KEY' in content:
        raise ValueError('Expected a public certificate chain')
    target = Path(settings['cert_file'])
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.renew-', delete=False) as file:
        candidate = Path(file.name)
        file.write(content)
    try:
        context_for(settings, candidate)  # Includes certificate/private key match.
        result = subprocess.run(['openssl', 'verify', '-purpose', 'sslserver',
                                 '-verify_hostname', settings['server_name'],
                                 '-untrusted', str(candidate), str(candidate)],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ValueError('Certificate verification failed: ' + result.stderr.strip()[:300])
        decoded = ssl._ssl._test_decode_cert(str(candidate))
        expires = ssl.cert_time_to_seconds(decoded['notAfter'])
        if expires < time.time() + 86400:
            raise ValueError('Renewed certificate expires within one day')
        if target.exists():
            if target.read_bytes() == content:
                return False
            previous = ssl._ssl._test_decode_cert(str(target))
            if expires <= ssl.cert_time_to_seconds(previous['notAfter']):
                raise ValueError('Refusing a certificate older than the current one')
        os.replace(candidate, target)
        return True
    finally:
        candidate.unlink(missing_ok=True)


def renew_certificate(settings):
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
    with opener.open(settings['renewal_url'], timeout=20) as response:
        content = response.read(65537)
    return install_certificate(settings, content)


def maintain_certificate(settings, stop):
    while not stop.is_set():
        try:
            if renew_certificate(settings):
                logging.info('Installed renewed TLS certificate for %s', settings['server_name'])
        except Exception as exc:
            logging.warning('TLS certificate refresh failed; keeping current certificate: %s', exc)
        stop.wait(12 * 3600)


class TLSHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler, settings):
        self.tls_settings = settings
        self.tls_lock = threading.Lock()
        self.tls_context = context_for(settings)
        self.tls_stamp = self.certificate_stamp()
        super().__init__(address, handler)

    def certificate_stamp(self):
        return tuple((p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_size)
                     for p in map(Path, (self.tls_settings['cert_file'], self.tls_settings['key_file'])))

    def current_context(self):
        with self.tls_lock:
            try:
                stamp = self.certificate_stamp()
                if stamp != self.tls_stamp:
                    self.tls_context = context_for(self.tls_settings)
                    self.tls_stamp = stamp
            except (OSError, ValueError) as exc:
                logging.warning('Cannot reload TLS certificate; keeping last valid context: %s', exc)
            return self.tls_context

    def process_request_thread(self, request, client_address):
        # Handshake inside the worker: one stalled TLS client cannot stop accepts.
        # Do not proxy through loopback: access control needs client_address.
        try:
            request.settimeout(10)
            secured = self.current_context().wrap_socket(request, server_side=True)
        except (OSError, ValueError):
            self.shutdown_request(request)
            return
        try:
            self.finish_request(secured, client_address)
        except (ConnectionError, ssl.SSLError):
            pass  # Browser navigated away or closed the connection.
        except Exception:
            self.handle_error(secured, client_address)
        finally:
            self.shutdown_request(secured)
