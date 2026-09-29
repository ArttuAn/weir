"""HTTP/1.1 data plane.

A weir router is a forwarding element, not a framework, so this module is
deliberately thin: it turns an HTTP request into an :class:`~weir.intent.Intent`,
runs it through the pipeline, and turns the outcome back into an HTTP response.
All of the interesting behaviour is in :mod:`weir.router` and is reachable
without any of this.

Refusals are rendered so that an *unmodified* HTTP client does something
sensible: a standard ``Retry-After`` accompanies every appointment, so a caller
that knows nothing about weir still backs off roughly correctly, and a caller
that does know about weir reads ``weir-appointment`` and gets the exact slot.
Incremental deployability is not a nicety here.  A router that only works once
every agent on the internet has been rewritten is a router that never ships.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import Refused
from .intent import MalformedIntent, from_headers, to_headers
from .router import Router


def make_handler(router: Router):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "weir/0"

        def log_message(self, fmt, *args):  # quieter than the default
            pass

        def _send(self, status: int, payload: dict, extra: dict | None = None) -> None:
            body = json.dumps(payload, indent=2).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/_weir/stats":
                return self._send(200, router.stats())
            if self.path == "/_weir/receipts":
                ok, msg = router.receipts.verify()
                return self._send(200, {"verified": ok, "detail": msg,
                                        "tip": router.receipts.tip})
            if self.path == "/_weir/housekeep":
                return self._send(200, router.housekeep())
            if self.path == "/_weir/routes":
                return self._send(200, {"routes": [r.__dict__ for r in router.fib.routes()]})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}

            try:
                intent = from_headers(headers)
            except MalformedIntent as exc:
                return self._send(400, {"reason": "malformed-intent", "detail": str(exc)})

            ticket = headers.get("weir-appointment")

            def upstream(fwd, route):
                req = urllib.request.Request(
                    route.upstream, data=body, method="POST",
                    headers={**to_headers(fwd), "content-type":
                             headers.get("content-type", "application/json")})
                with urllib.request.urlopen(req, timeout=max(1.0, intent.time_left(
                        router.clock.now()))) as resp:
                    return {"status": resp.status, "body": resp.read().decode("utf-8", "replace")}

            decision = router.forward(intent, ticket, upstream=upstream)

            # The router's own clock, on every response. A caller that subtracts
            # its own wall clock from a router timestamp folds clock skew
            # straight into the wait - and then gets charged a penalty for
            # being early when the only thing wrong was NTP. Echoing `now`
            # lets the caller work in deltas, where the skew cancels.
            now_hdr = {"weir-now": f"{router.clock.now():.3f}"}

            if decision.ok:
                return self._send(200, {
                    "disposition": decision.disposition,
                    "credits": decision.credits,
                    "upstream": decision.route.upstream if decision.route else None,
                    "result": decision.result,
                }, {**now_hdr,
                    "weir-disposition": decision.disposition,
                    "weir-credits": str(decision.credits),
                    "weir-receipt": router.receipts.tip})

            extra = {**now_hdr, "weir-reason": decision.reason,
                     "weir-credits": str(decision.credits)}
            if decision.retry_not_before is not None:
                wait = max(0.0, decision.retry_not_before - router.clock.now())
                # Retry-After for clients that have never heard of weir; the
                # exact slot for those that have.
                extra["retry-after"] = str(max(1, int(round(wait))))
                extra["weir-retry-not-before"] = f"{decision.retry_not_before:.3f}"
            if decision.ticket:
                extra["weir-appointment"] = decision.ticket
            return self._send(decision.status, {
                "reason": decision.reason,
                "detail": decision.detail,
                "credits_charged": decision.credits,
                "retry_not_before": decision.retry_not_before,
            }, extra)

    return Handler


def _housekeeper(router: Router, every: float) -> threading.Thread:
    """Sweep finished state on a timer, off the request path."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every):
            try:
                router.housekeep()
            except Exception:  # never let maintenance kill the data plane
                pass

    t = threading.Thread(target=loop, daemon=True, name="weir-housekeeper")
    t.stop = stop  # type: ignore[attr-defined]
    t.start()
    return t


def serve(router: Router, host: str = "127.0.0.1", port: int = 8710,
          *, housekeep_every: float = 30.0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(router))
    httpd.daemon_threads = True
    # Without this the router leaks root accounts until it is killed.
    httpd.housekeeper = _housekeeper(router, housekeep_every)  # type: ignore[attr-defined]
    return httpd


# ---------------------------------------------------------------------------

class EchoOrigin(BaseHTTPRequestHandler):
    """A trivial origin, so the demo needs nothing installed."""

    protocol_version = "HTTP/1.1"
    delay = 0.05

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        import time
        time.sleep(self.delay)
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b"{}"
        out = json.dumps({
            "origin": "echo",
            "saw_principal": self.headers.get("weir-principal"),
            "saw_path": self.headers.get("weir-path"),
            "saw_depth": self.headers.get("weir-depth"),
            "echo": body.decode("utf-8", "replace")[:200],
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def serve_origin(host: str = "127.0.0.1", port: int = 8711) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), EchoOrigin)
    httpd.daemon_threads = True
    return httpd
