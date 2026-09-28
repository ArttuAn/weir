"""Attestation of intent headers.

An intent header is only worth forwarding on if the fields that constrain it -
principal, budget, depth, deadline - cannot be rewritten by the agent they
constrain.  Without attestation ``weir-budget: 999999999`` is a self-service
credit line and ``weir-coupling: human`` is a self-declared fast lane.

WIH-0 uses a keyed BLAKE2b MAC per Agent AS: symmetric, stdlib-only, and
adequate when the verifying router shares administrative trust with the issuing
AAS (the common case: your own fleet, your own router).  Cross-domain
attestation needs public-key signatures so a router can verify without holding
a secret that would let it forge; that is WIH-1 and is specified, not
implemented, in ``docs/SPEC-WIH-0.md``.  The interface below is the same shape
either way, so swapping the primitive does not move the trust boundary.
"""

from __future__ import annotations

import hmac
from hashlib import blake2b

from .intent import Intent


class Keyring:
    """Per-AAS attestation keys."""

    def __init__(self, keys: dict[int, bytes] | None = None, *, required: bool = True) -> None:
        self._keys = dict(keys or {})
        #: When False, unattested headers are accepted.  Useful for a lab, and
        #: a loaded gun in production - the router logs it on every startup.
        self.required = required

    def add(self, aas: int, key: bytes) -> None:
        self._keys[aas] = key

    def known(self, aas: int) -> bool:
        return aas in self._keys

    def sign(self, intent: Intent) -> str:
        key = self._keys.get(intent.aas)
        if key is None:
            raise KeyError(f"no attestation key for AAS {intent.aas}")
        mac = blake2b(intent.canonical(), key=key, digest_size=16).hexdigest()
        return f"b2k:{intent.aas}:{mac}"

    def attested(self, intent: Intent) -> Intent:
        from dataclasses import replace
        return replace(intent, attest=self.sign(intent))

    def verify(self, intent: Intent) -> bool:
        if not self.required and not intent.attest:
            return True
        parts = intent.attest.split(":")
        if len(parts) != 3 or parts[0] != "b2k":
            return False
        try:
            aas = int(parts[1])
        except ValueError:
            return False
        # The MAC must be from the AAS that claims to be executing this hop.
        # Otherwise AAS 2 could mint headers claiming to be AAS 1.
        if aas != intent.aas:
            return False
        key = self._keys.get(aas)
        if key is None:
            return False
        expect = blake2b(intent.canonical(), key=key, digest_size=16).hexdigest()
        return hmac.compare_digest(expect, parts[2])
