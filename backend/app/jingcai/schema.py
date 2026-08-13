from __future__ import annotations

from typing import Any


def ensure_jingcai_runtime_schema(connection: Any) -> None:
    """Apply only the small, idempotent migration needed by standalone tasks."""

    column_exists = connection.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'raw'
            AND table_name = 'ingestion_batches'
            AND column_name = 'report'
        ) AS present
        """
    ).fetchone()["present"]
    if not column_exists:
        connection.execute(
            """
            ALTER TABLE raw.ingestion_batches
                ADD COLUMN IF NOT EXISTS report jsonb NOT NULL DEFAULT '{}'::jsonb
            """
        )

    indexes = (
        (
            "idx_ingestion_batches_source_resource_finished",
            "finished_at_utc DESC",
        ),
        (
            "idx_ingestion_batches_source_resource_started",
            "started_at_utc DESC, batch_id DESC",
        ),
    )
    for index_name, ordering in indexes:
        index_exists = connection.execute(
            "SELECT to_regclass(%s) IS NOT NULL AS present",
            (f"raw.{index_name}",),
        ).fetchone()["present"]
        if not index_exists:
            connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {index_name}
                    ON raw.ingestion_batches (source_name, resource_type, {ordering})
                """
            )
