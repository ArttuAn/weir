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

import dataclasses
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import Reason, Refused
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
            if self.path.startswith("/_weir/receipts/"):
                return self._receipts_for(self.path[len("/_weir/receipts/"):])
            if self.path == "/_weir/housekeep":
                return self._send(200, router.housekeep())
            if self.path == "/_weir/routes":
                # asdict, not __dict__: Route is a slots dataclass and has no
                # instance dict to read.
                return self._send(200, {"routes": [dataclasses.asdict(r)
                                                  for r in router.fib.routes()]})
            return self._send(404, {"error": "not found"})

        def _receipts_for(self, raw: str) -> None:
            """The audit trail for one delegation tree, and nothing else.

            Scoped on purpose.  The flat chain is the right thing to hand an
            operator with the whole box, and the wrong thing to hand a caller
            with one root: it is every other principal's activity on the network,
            which is a much larger disclosure than the question being asked and
            mostly not the asker's business.

            Note what is *not* here: no payload, and the query is only ever
            compared against entries in memory - it is never used to build a
            path, so there is nothing for a crafted root to traverse.
            """
            root = urllib.parse.unquote(raw)
            found = router.receipts.for_root(root)
            if not found:
                return self._send(404, {"error": "no receipts for that root",
                                        "root": root})
            ok, msg = router.receipts.verify()
            first, last = router.receipts.window()
            return self._send(200, {
                "root": root,
                "count": len(found),
                "entries": [dataclasses.asdict(r) for r in found],
                "verified": ok,
                "detail": msg,
                "window": {"from_seq": first, "to_seq": last, "trimmed": first > 0},
            })

        def do_POST(self):
            raw_len = self.headers.get("content-length") or "0"
            try:
                length = int(raw_len)
                if length < 0:
                    raise ValueError(raw_len)
            except ValueError:
                # The framing itself is what is broken. The body length is
                # unknown, so the bytes already sitting in the socket cannot be
                # located and a keep-alive client would parse them as the next
                # request. Answer, then hang up rather than guess.
                self.close_connection = True
                return self._send(400, {"reason": Reason.MALFORMED,
                                        "detail": f"bad content-length {raw_len!r}"},
                                  {"Connection": "close"})
            body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}

            try:
                intent = from_headers(headers)
            except MalformedIntent as exc:
                return self._send(400, {"reason": Reason.MALFORMED, "detail": str(exc)})

            ticket = headers.get("weir-appointment")

            def upstream(fwd, route):
                req = urllib.request.Request(
                    route.upstream, data=body, method="POST",
                    headers={**to_headers(fwd), "content-type":
                             headers.get("content-type", "application/json")})
                with urllib.request.urlopen(req, timeout=max(1.0, intent.time_left(
                        router.clock.now()))) as resp:
                    return {"status": resp.status, "body": resp.read().decode("utf-8", "replace")}

            try:
                decision = router.forward(intent, ticket, upstream=upstream)
            except Refused as exc:
                # The pipeline turns refusals into decisions; a Refused escaping
                # it is a bug, not a wire condition, so it is not answered as one.
                return self._send(500, {"reason": "internal-refusal",
                                        "detail": str(exc)})
            except Exception as exc:
                # The upstream died.  The router has already released the slot
                # and written the receipt, so all that is left is to tell the
                # caller - an unmodified client that sees the connection close
                # has learned nothing except that weir is flaky, and retries
                # blindly.  502 says the failure was downstream of the weir.
                return self._send(502, {"reason": "upstream-failed",
                                        "detail": f"{type(exc).__name__}: {exc}"},
                                  {"weir-now": f"{router.clock.now():.3f}"})

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
