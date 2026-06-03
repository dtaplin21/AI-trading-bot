"""Apply versioned SQL migrations from db/migrations/."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_TIMESCALE_MARKERS = (
    "timescaledb",
    "create_hypertable",
    "add_continuous_aggregate_policy",
    "remove_continuous_aggregate_policy",
)


def split_sql(script: str) -> list[str]:
    """Split a SQL file into executable statements (ignores full-line comments)."""
    statements: list[str] = []
    current: list[str] = []
    in_do_block = False

    for line in script.splitlines():
        if re.match(r"^\s*--", line):
            continue
        current.append(line)
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith("DO $$"):
            in_do_block = True
        if in_do_block:
            if upper.endswith("END $$;") or upper == "END $$;":
                stmt = "\n".join(current).strip()
                if stmt:
                    statements.append(stmt)
                current = []
                in_do_block = False
            continue

        if stripped.endswith(";"):
            stmt = "\n".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []

    if current:
        stmt = "\n".join(current).strip()
        if stmt:
            statements.append(stmt)
    return statements


def _is_timescale_statement(statement: str) -> bool:
    lower = statement.lower()
    return any(marker in lower for marker in _TIMESCALE_MARKERS)


def _timescale_available(cur) -> bool:
    cur.execute(
        "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'"
    )
    return cur.fetchone() is not None


def _ensure_migrations_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _execute_statement(cur, statement: str, *, allow_timescale: bool) -> None:
    if not allow_timescale and _is_timescale_statement(statement):
        logger.debug("Skipping TimescaleDB-only statement")
        return
    cur.execute(statement)


def run_migrations(database_url: str | None = None) -> int:
    """
    Apply pending db/migrations/*.sql files in sorted order.
    Returns count of newly applied migration files.
    """
    if database_url:
        url = database_url
    else:
        from config.settings import get_settings

        url = get_settings().database_url

    if not url:
        logger.info("run_migrations: DATABASE_URL unset — skipping")
        return 0

    from psycopg2 import Error as PsycopgError

    from data.storage.pg_connect import connect_psycopg2

    applied = 0
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        logger.warning("run_migrations: no files in %s", MIGRATIONS_DIR)
        return 0

    with connect_psycopg2(url) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            _ensure_migrations_table(cur)
            allow_timescale = _timescale_available(cur)

            for path in files:
                version = path.name
                cur.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s",
                    (version,),
                )
                if cur.fetchone():
                    continue

                logger.info("Applying migration %s", version)
                script = path.read_text(encoding="utf-8")
                try:
                    for statement in split_sql(script):
                        try:
                            _execute_statement(
                                cur, statement, allow_timescale=allow_timescale
                            )
                        except PsycopgError as exc:
                            if allow_timescale or not _is_timescale_statement(
                                statement
                            ):
                                raise
                            logger.warning(
                                "Skipping TimescaleDB statement in %s: %s",
                                version,
                                exc,
                            )
                except Exception:
                    logger.exception("Migration failed: %s", version)
                    raise

                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
                applied += 1
                logger.info("Applied migration %s", version)

    return applied
