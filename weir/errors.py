"""Refusal reasons.

A weir router never fails silently and never returns a bare "try again".  Every
refusal names a machine-readable reason, and every reason maps to an action the
caller can actually take.  ``RETRYABLE`` reasons come with an appointment (see
:mod:`weir.damper`); the rest are terminal and retrying them only burns budget.
"""

from __future__ import annotations


class Reason:
    # Terminal: the request is malformed or forbidden.  Retrying is pointless.
    MALFORMED = "malformed-intent"
    BAD_ATTESTATION = "bad-attestation"
    DELEGATION_LOOP = "delegation-loop"
    DEPTH_EXCEEDED = "depth-exceeded"
    FANOUT_EXCEEDED = "fanout-exceeded"
    BUDGET_EXHAUSTED = "budget-exhausted"
    DEADLINE_PASSED = "deadline-passed"
    INFEASIBLE = "deadline-infeasible"
    TERMS_DENIED = "terms-denied"
    NO_ROUTE = "no-route"

    # Retryable: the request was fine, the network was not.  Carries an
    # appointment in ``retry_not_before``.
    CONGESTED = "congested"
    EARLY_RETRY = "early-retry"

    RETRYABLE = frozenset({CONGESTED, EARLY_RETRY})

    #: HTTP status used on the wire for each reason.  429 is reserved for
    #: reasons that carry an appointment; terminal refusals use 4xx codes that
    #: no sane retry library treats as retryable.
    HTTP = {
        MALFORMED: 400,
        BAD_ATTESTATION: 401,
        DELEGATION_LOOP: 508,      # RFC 5842 "Loop Detected"
        DEPTH_EXCEEDED: 508,
        FANOUT_EXCEEDED: 429,
        BUDGET_EXHAUSTED: 402,     # Payment Required, finally load-bearing
        DEADLINE_PASSED: 408,
        INFEASIBLE: 408,
        TERMS_DENIED: 451,
        NO_ROUTE: 404,
        CONGESTED: 429,
        EARLY_RETRY: 429,
    }


class Refused(Exception):
    """Raised inside the pipeline; converted to a wire response at the edge."""

    def __init__(self, reason: str, detail: str = "", retry_not_before: float | None = None,
                 charged: int = 0) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.retry_not_before = retry_not_before
        self.charged = charged

    @property
    def retryable(self) -> bool:
        return self.reason in Reason.RETRYABLE

    @property
    def status(self) -> int:
        return Reason.HTTP.get(self.reason, 400)
