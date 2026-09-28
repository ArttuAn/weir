"""weir - a router for an internet whose traffic is agents.

See ``docs/DESIGN.md`` for why the classical forwarding model does not survive
contact with agent traffic, and what this replaces it with.
"""

from .clock import RealClock, VirtualClock
from .errors import Reason, Refused
from .fib import Route
from .intent import CREDIT, Hop, Intent, digest_of, new_root
from .router import Decision, Router, RouterConfig

__version__ = "0.1.0"
__all__ = ["Router", "RouterConfig", "Decision", "Route", "Intent", "Hop",
           "Reason", "Refused", "RealClock", "VirtualClock",
           "digest_of", "new_root", "CREDIT", "__version__"]
