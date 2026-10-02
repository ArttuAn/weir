# Contributing to weir

Contributions are welcome. This project has a few guardrails by design.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Running tests

```bash
python3 -m unittest discover -s tests
```

Or use the Makefile:

```bash
make test
```

## Code style

- Follow PEP 8. The project aims to be readable and explicit.
- Run ruff for linting if available: `ruff check .`
- Run mypy for type checking if available: `mypy weir`

## Principles

- The router's ledger is authoritative; the header is advisory.
- Refusals are explicit and carry retryable flags where appropriate.
- Every decision that should be accountable leaves a receipt.
- Coalescing must not leak across principals or unsafe requests.

All existing tests must pass. Add tests for new behaviour.
