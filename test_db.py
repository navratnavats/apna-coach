"""
Simple Supabase connectivity + users-table existence check.

Usage:
  1) Put credentials in .env (or export env vars):
     - SUPABASE_URL
     - SUPABASE_KEY)
  2) Install SDK:
     pip install supabase
  3) Run:
     python test_db.py
"""

from __future__ import annotations

import os
import sys


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def create_supabase_client(url: str, key: str):
    try:
        from supabase import create_client  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Supabase Python SDK not found. Install with: pip install supabase"
        ) from exc

    return create_client(url, key)


def get_supabase_client():
    url = os.getenv("SUPABASE_URL")
    key = (
        os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )

    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or key. "
            "Set SUPABASE_KEY, SUPABASE_ANON_KEY, or SUPABASE_SERVICE_ROLE_KEY."
        )

    return create_supabase_client(url, key)


def users_table_exists(client) -> bool:
    try:
        # Lightweight probe. If table exists, this should return (or fail via RLS, which
        # still confirms the table relation exists).
        client.table("users").select("id").limit(1).execute()
        return True
    except Exception as exc:  # noqa: BLE001 - simple CLI script
        message = str(exc).lower()

        # PostgREST missing relation signal.
        if "does not exist" in message and "users" in message:
            return False

        # Common case: RLS/permission errors mean table exists but policy blocks reads.
        permission_markers = ["permission", "rls", "forbidden", "not authorized"]
        if any(marker in message for marker in permission_markers):
            return True

        raise


def main() -> int:
    try:
        load_env_file(".env")
        supabase = get_supabase_client()
        exists = users_table_exists(supabase)
    except Exception as exc:  # noqa: BLE001 - simple CLI script
        print(f"Database check failed: {exc}")
        return 1

    if exists:
        print("Success: Supabase connected and `public.users` exists.")
        return 0

    print("Not found: `public.users` does not exist yet.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
