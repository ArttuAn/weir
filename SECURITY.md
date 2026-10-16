# Security Policy

## Supported Versions

Currently, only the latest version is supported.

## Reporting a Vulnerability

Please use GitHub's private vulnerability reporting form at
https://github.com/ArttuAn/weir/security/advisories/new. Do not publish sensitive
vulnerability details, credentials, or user data in a public issue. If private
reporting is unavailable, contact the maintainer privately before disclosure.

We take security seriously, especially given the design goals around
accountability and receipts. See [THREAT-MODEL.md](docs/THREAT-MODEL.md) for
the project's threat model.

The HTTP data plane limits request bodies to 1 MiB by default and closes requests
with oversized, ambiguous, incomplete, or timed-out bodies before admission.
Embedders can set `serve(..., max_body_bytes=...)` to a positive byte limit.
The demo echo origin uses the same default cap. Use a hardened HTTP ingress for
public deployments; this stdlib server is intended for the documented lab setup.
