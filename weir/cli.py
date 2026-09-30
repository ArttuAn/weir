"""``weir`` command line."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from . import __version__
from .fib import Route
from .intent import CREDIT
from .receipts import verify_file
from .router import Router, RouterConfig


def _router_from_args(args) -> Router:
    cfg = RouterConfig(aas=args.aas, name=args.name, limit=args.limit,
                       default_grant=args.grant,
                       receipt_path=args.receipts)
    r = Router(cfg)
    for spec in args.route or []:
        cap, _, up = spec.partition("=")
        if not up:
            raise SystemExit(f"bad --route {spec!r}, expected capability=url")
        r.route(Route(cap, up))
    if not args.route:
        r.route(Route("agent", "http://127.0.0.1:8711/"))
    return r


def cmd_run(args) -> int:
    from .server import serve
    r = _router_from_args(args)
    httpd = serve(r, args.host, args.port)
    print(f"weir {__version__}  AAS {r.config.aas}  {args.host}:{args.port}")
    for route in r.fib.routes():
        print(f"  route {route.capability:<24} -> {route.upstream}")
    if not r.keyring.required:
        print("  WARNING: attestation not required; any caller may claim any "
              "principal, budget or priority.")
    print(f"  stats    http://{args.host}:{args.port}/_weir/stats")
    print(f"  receipts http://{args.host}:{args.port}/_weir/receipts")
    print(f"           .../<root> for one delegation tree's audit trail")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n" + json.dumps(r.stats(), indent=2))
    return 0


def cmd_origin(args) -> int:
    from .server import EchoOrigin, serve_origin
    EchoOrigin.delay = args.delay
    httpd = serve_origin(args.host, args.port)
    print(f"echo origin on {args.host}:{args.port} (delay {args.delay}s)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_demo(args) -> int:
    from . import demos
    demos.run(args.scenario)
    return 0


def cmd_verify(args) -> int:
    ok, msg = verify_file(args.path)
    print(("OK  " if ok else "FAIL ") + msg)
    return 0 if ok else 1


def cmd_call(args) -> int:
    from .client import CallFailed, Caller
    c = Caller(router_url=args.router, principal=args.principal,
               agent=args.agent, budget=args.budget)
    try:
        out = c.call(args.capability, json.loads(args.payload),
                     coupling=args.coupling,
                     deadline=time.time() + args.deadline)
    except CallFailed as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    print(f"spent {c.spent} of {c.budget} millicredits", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="weir", description=__doc__)
    ap.add_argument("--version", action="version", version=f"weir {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a router")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8710)
    run.add_argument("--aas", type=int, default=64512)
    run.add_argument("--name", default="weir-0")
    run.add_argument("--limit", type=int, default=32)
    run.add_argument("--grant", type=int, default=100 * CREDIT)
    run.add_argument("--route", action="append", metavar="CAP=URL")
    run.add_argument("--receipts", metavar="PATH", help="append receipts to this file")
    run.set_defaults(fn=cmd_run)

    org = sub.add_parser("origin", help="run a throwaway echo origin")
    org.add_argument("--host", default="127.0.0.1")
    org.add_argument("--port", type=int, default=8711)
    org.add_argument("--delay", type=float, default=0.05)
    org.set_defaults(fn=cmd_origin)

    dm = sub.add_parser("demo", help="run a scenario")
    dm.add_argument("scenario", nargs="?", default="all",
                    choices=["all", "storm", "cycle", "swarm", "deadline"])
    dm.set_defaults(fn=cmd_demo)

    vf = sub.add_parser("verify", help="verify a receipt chain")
    vf.add_argument("path")
    vf.set_defaults(fn=cmd_verify)

    cl = sub.add_parser("call", help="make one call through a router")
    cl.add_argument("capability")
    cl.add_argument("--router", default="http://127.0.0.1:8710/")
    cl.add_argument("--principal", default="did:web:example#you")
    cl.add_argument("--agent", default="aas:64512/cli")
    cl.add_argument("--payload", default="{}")
    cl.add_argument("--coupling", default="human", choices=["human", "batch"])
    cl.add_argument("--budget", type=int, default=10 * CREDIT)
    cl.add_argument("--deadline", type=float, default=30.0)
    cl.set_defaults(fn=cmd_call)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
