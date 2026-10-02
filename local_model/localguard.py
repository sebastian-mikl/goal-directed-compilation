"""No-Anthropic guard for the O2 local-model runs. Import this FIRST, before anything
that imports anthropic, dotenv, gdc or evidence.

What it enforces, in this process:
  1. ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are removed from the environment, and
     ANTHROPIC_BASE_URL is forced to the local Ollama server, so any Anthropic()
     built anywhere (gdc.config.CLIENT included) defaults to localhost.
  2. dotenv is neutralised: load_dotenv / dotenv_values / find_dotenv become no-ops
     that record the attempt, so no .env file is ever read.
  3. Every socket connect and DNS lookup is checked: only loopback hosts are allowed.
     Anything else raises NonLocalNetworkBlocked and is logged to VIOLATIONS_LOG.
  4. guarded_client() builds the Anthropic client pointed at http://localhost:11434
     with a dummy key, asserts its base_url, and installs an httpx request hook that
     refuses any request whose host is not localhost:11434 (logged as well).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

LOCAL_HOST, LOCAL_PORT = "localhost", 11434
LOCAL_BASE_URL = f"http://{LOCAL_HOST}:{LOCAL_PORT}"
DUMMY_API_KEY = "ollama-local-dummy-key"
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"}
VIOLATIONS_LOG = Path(os.environ.get("GDC_LOCAL_VIOLATIONS_LOG",
                                     Path(__file__).resolve().parent / "guard_violations.log"))
DOTENV_ATTEMPTS: list[str] = []
REQUESTS_SEEN = {"localhost_11434": 0}


class NonLocalNetworkBlocked(RuntimeError):
    pass


def _violation(what: str):
    try:
        with open(VIOLATIONS_LOG, "a") as fh:
            fh.write(json.dumps({"t": time.time(), "pid": os.getpid(), "blocked": what}) + "\n")
    finally:
        raise NonLocalNetworkBlocked(f"[localguard] blocked non-local network access: {what}")


# 1. environment
for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(_k, None)
os.environ["ANTHROPIC_BASE_URL"] = LOCAL_BASE_URL
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

# 2. dotenv
try:
    import dotenv as _dotenv
    import dotenv.main as _dotenv_main

    def _no_load(*a, **k):
        DOTENV_ATTEMPTS.append("load_dotenv")
        return False

    def _no_values(*a, **k):
        DOTENV_ATTEMPTS.append("dotenv_values")
        return {}

    def _no_find(*a, **k):
        DOTENV_ATTEMPTS.append("find_dotenv")
        return ""

    for _mod in (_dotenv, _dotenv_main):
        _mod.load_dotenv = _no_load
        _mod.dotenv_values = _no_values
        _mod.find_dotenv = _no_find
except ImportError:
    pass


# 3. sockets
def _host_ok(host) -> bool:
    if host is None:
        return False
    h = host.decode() if isinstance(host, bytes) else str(host)
    return h.strip("[]").lower() in _LOOPBACK


_orig_getaddrinfo = socket.getaddrinfo
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_create_connection = socket.create_connection


def _getaddrinfo(host, *a, **k):
    if not _host_ok(host):
        _violation(f"getaddrinfo({host!r})")
    return _orig_getaddrinfo(host, *a, **k)


def _addr_ok(sock, address) -> bool:
    if sock.family == socket.AF_UNIX:
        return True
    host = address[0] if isinstance(address, tuple) else address
    return _host_ok(host)


def _connect(self, address):
    if not _addr_ok(self, address):
        _violation(f"socket.connect({address!r})")
    return _orig_connect(self, address)


def _connect_ex(self, address):
    if not _addr_ok(self, address):
        _violation(f"socket.connect_ex({address!r})")
    return _orig_connect_ex(self, address)


def _create_connection(address, *a, **k):
    if not _host_ok(address[0]):
        _violation(f"create_connection({address!r})")
    return _orig_create_connection(address, *a, **k)


socket.getaddrinfo = _getaddrinfo
socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.create_connection = _create_connection


# 4. the client
def _request_hook(request):
    url = request.url
    if not (url.host in ("localhost", "127.0.0.1") and url.port == LOCAL_PORT):
        _violation(f"httpx request to {url!s}")
    REQUESTS_SEEN["localhost_11434"] += 1


def guarded_client(**kw):
    """The Anthropic-shaped client every system uses: local Ollama, dummy key."""
    import anthropic
    assert "ANTHROPIC_API_KEY" not in os.environ, "ANTHROPIC_API_KEY must be unset"
    http = anthropic.DefaultHttpxClient(event_hooks={"request": [_request_hook]})
    client = anthropic.Anthropic(base_url=LOCAL_BASE_URL, api_key=DUMMY_API_KEY,
                                 http_client=http, **kw)
    bu = str(client.base_url).rstrip("/")
    if bu != LOCAL_BASE_URL:
        raise RuntimeError(f"[localguard] client base_url is {bu!r}, expected {LOCAL_BASE_URL!r}")
    return client


def self_test() -> dict:
    """Prove the guard blocks a non-local host, without sending anything: the DNS
    lookup itself is refused before any packet leaves."""
    out = {}
    try:
        socket.getaddrinfo("api.anthropic.com", 443)
        out["dns_api.anthropic.com"] = "NOT BLOCKED"
    except NonLocalNetworkBlocked:
        out["dns_api.anthropic.com"] = "blocked"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("160.79.104.10", 443))       # an arbitrary non-loopback address
            out["connect_nonlocal_ip"] = "NOT BLOCKED"
        finally:
            s.close()
    except NonLocalNetworkBlocked:
        out["connect_nonlocal_ip"] = "blocked"
    import httpx
    try:
        _request_hook(httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
        out["httpx_hook_api.anthropic.com"] = "NOT BLOCKED"
    except NonLocalNetworkBlocked:
        out["httpx_hook_api.anthropic.com"] = "blocked"
    c = guarded_client()
    out["client_base_url"] = str(c.base_url)
    out["ANTHROPIC_API_KEY_present"] = "ANTHROPIC_API_KEY" in os.environ
    # the self-test's own violations are expected; mark them so the log is unambiguous
    with open(VIOLATIONS_LOG, "a") as fh:
        fh.write(json.dumps({"t": time.time(), "pid": os.getpid(),
                             "note": "the 3 lines above are the guard self-test (expected)"}) + "\n")
    return out
