"""Compatibility shim for scripts/test_db.py."""

from scripts.test_db import main


if __name__ == "__main__":
    raise SystemExit(main())
