#!/usr/bin/env python3
"""
Convert Pleco .pqb dictionary SQLite files in the current folder into one
Supabase Postgres table, merging Vietnamese definitions into extra columns.

Requirements:
  pip install psycopg[binary] python-dotenv

Environment variables:
  SUPABASE_DB_URL="postgresql://postgres.<project-ref>:<password>@aws-0-xxx.pooler.supabase.com:6543/postgres"

Example:
  python pqb_to_supabase.py --table pleco_entries

Notes:
  - Use Supabase's Postgres connection string, not the REST API URL.
  - This script reads .pqb files in read-only mode.
  - It scans the current folder for .pqb files.
  - It merges rows by word + pronunciation, not Pleco uid.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from dotenv import load_dotenv


DEFAULT_TABLE = "pleco_merged_entries"
DEFAULT_BATCH_SIZE = 1000
MERGE_SEPARATOR = "\x1f"


def clean_pleco_text(value: Any) -> str | None:
    """Clean common Pleco formatting markers for easier display/search."""
    if value is None:
        return None
    text = str(value)
    return (
        text.replace("@", "")
        .replace("\ueab1- ", "; ")
        .replace("\ueab1", "; ")
        .strip()
    )


def validate_table_name(name: str) -> str:
    """Allow only simple SQL identifiers to avoid injection through table name."""
    if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
        raise ValueError(
            "Invalid table name. Use only letters, numbers, and underscores, "
            "and do not start with a number."
        )
    return name


def qualified_table(table: str) -> sql.Composed:
    """Return a safely quoted table identifier, with optional schema support."""
    parts = table.split(".")
    if len(parts) > 2 or any(not part for part in parts):
        raise ValueError("Invalid table name. Use table or schema.table.")
    return sql.SQL(".").join(sql.Identifier(part) for part in parts)


def index_name(table: str, suffix: str) -> str:
    """Build a valid, stable index name for the table."""
    return validate_table_name(f"idx_{table.replace('.', '_')}_{suffix}")


def merge_key(word: str | None, pron: str | None) -> str:
    """Build the stable row key used to merge dictionaries."""
    return f"{word or ''}{MERGE_SEPARATOR}{pron or ''}"


def batched(iterable: Iterable[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def get_pqb_properties(pqb_path: Path) -> dict[str, str | None]:
    """Read Pleco dictionary metadata."""
    conn = sqlite3.connect(f"file:{pqb_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT propid, propvalue FROM pleco_dict_properties"
        ).fetchall()
        return {propid: propvalue for propid, propvalue in rows}
    finally:
        conn.close()


def is_vietnamese_pqb(pqb_path: Path) -> bool:
    """Detect the Vietnamese dictionary file from filename and Pleco metadata."""
    props = get_pqb_properties(pqb_path)
    searchable = " ".join(
        value or ""
        for value in [
            pqb_path.name,
            props.get("DictName"),
            props.get("DictShortName"),
            props.get("DictMenuName"),
        ]
    ).lower()
    return "ovd" in searchable or "vietnamese dictionary" in searchable


def scan_pqb_files(pqb_dir: Path) -> tuple[list[Path], list[Path]]:
    """Return base dictionaries and Vietnamese dictionaries from a folder."""
    pqb_files = sorted(pqb_dir.glob("*.pqb"))
    if not pqb_files:
        raise FileNotFoundError(f"No .pqb files found in {pqb_dir}")

    vietnamese_files = [path for path in pqb_files if is_vietnamese_pqb(path)]
    base_files = [path for path in pqb_files if path not in vietnamese_files]

    if not vietnamese_files:
        raise RuntimeError(
            "No Vietnamese/OVD .pqb file found. Expected a filename or Pleco "
            "metadata containing 'OVD' or 'Vietnamese'."
        )
    if not base_files:
        raise RuntimeError("No base/non-Vietnamese .pqb file found to merge into.")

    return base_files, vietnamese_files


def iter_pqb_entries(pqb_path: Path) -> Iterable[dict[str, Any]]:
    """Stream entries from the Pleco SQLite DB."""
    conn = sqlite3.connect(f"file:{pqb_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        query = """
            SELECT
                uid,
                word,
                altword,
                pron,
                defn,
                length,
                sortkey
            FROM pleco_dict_entries
            ORDER BY uid
        """
        for row in conn.execute(query):
            raw = dict(row)
            yield {
                "merge_key": merge_key(raw["word"], raw["pron"]),
                "source_file": pqb_path.name,
                "base_uid": raw["uid"],
                "word": raw["word"],
                "altword": raw["altword"],
                "pron": raw["pron"],
                "defn": raw["defn"],
                "length": raw["length"],
                "sortkey": raw["sortkey"],
                "word_clean": clean_pleco_text(raw["word"]),
                "pron_clean": clean_pleco_text(raw["pron"]),
                "defn_clean": clean_pleco_text(raw["defn"]),
                "raw_data": Jsonb(raw),
                "raw_dict": raw,
            }
    finally:
        conn.close()


def create_table(pg_conn: psycopg.Connection, table: str) -> None:
    """Create the target table and useful indexes."""
    table_sql = qualified_table(table)

    with pg_conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
            CREATE TABLE IF NOT EXISTS {} (
                merge_key TEXT PRIMARY KEY,
                base_uid BIGINT,
                source_file TEXT,
                word TEXT,
                altword TEXT,
                pron TEXT,
                defn TEXT,
                length INTEGER,
                sortkey TEXT,
                word_clean TEXT,
                pron_clean TEXT,
                defn_clean TEXT,
                raw_data JSONB,
                ovd_uids BIGINT[],
                ovd_source_files TEXT[],
                ovd_defn TEXT,
                ovd_defn_clean TEXT,
                ovd_raw_data JSONB,
                has_ovd BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
            ).format(table_sql)
        )
        for column_sql in [
            "ADD COLUMN IF NOT EXISTS merge_key TEXT",
            "ADD COLUMN IF NOT EXISTS base_uid BIGINT",
            "ADD COLUMN IF NOT EXISTS source_file TEXT",
            "ADD COLUMN IF NOT EXISTS ovd_uids BIGINT[]",
            "ADD COLUMN IF NOT EXISTS ovd_source_files TEXT[]",
            "ADD COLUMN IF NOT EXISTS ovd_defn TEXT",
            "ADD COLUMN IF NOT EXISTS ovd_defn_clean TEXT",
            "ADD COLUMN IF NOT EXISTS ovd_raw_data JSONB",
            "ADD COLUMN IF NOT EXISTS has_ovd BOOLEAN NOT NULL DEFAULT FALSE",
        ]:
            cur.execute(
                sql.SQL("ALTER TABLE {} {};").format(
                    table_sql,
                    sql.SQL(column_sql),
                )
            )
        cur.execute(
            sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (merge_key);").format(
                sql.Identifier(index_name(table, "merge_key")),
                table_sql,
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (word_clean);").format(
                sql.Identifier(index_name(table, "word_clean")),
                table_sql,
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (pron_clean);").format(
                sql.Identifier(index_name(table, "pron_clean")),
                table_sql,
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (length);").format(
                sql.Identifier(index_name(table, "length")),
                table_sql,
            )
        )
        cur.execute(
            sql.SQL(
                """
            CREATE INDEX IF NOT EXISTS {}
            ON {}
            USING GIN (defn_clean gin_trgm_ops);
            """
            ).format(
                sql.Identifier(index_name(table, "defn_clean_trgm")),
                table_sql,
            )
        )
        cur.execute(
            sql.SQL(
                """
            CREATE INDEX IF NOT EXISTS {}
            ON {}
            USING GIN (ovd_defn_clean gin_trgm_ops);
            """
            ).format(
                sql.Identifier(index_name(table, "ovd_defn_clean_trgm")),
                table_sql,
            )
        )
    pg_conn.commit()


def enable_trgm(pg_conn: psycopg.Connection) -> None:
    """Enable pg_trgm for faster fuzzy/text search on definitions."""
    with pg_conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    pg_conn.commit()


def upsert_base_batch(pg_conn: psycopg.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    columns = [
        "merge_key",
        "base_uid",
        "source_file",
        "word",
        "altword",
        "pron",
        "defn",
        "length",
        "sortkey",
        "word_clean",
        "pron_clean",
        "defn_clean",
        "raw_data",
    ]
    sql_stmt = sql.SQL(
        """
        INSERT INTO {} ({})
        VALUES ({})
        ON CONFLICT (merge_key) DO UPDATE SET
        {};
    """
    ).format(
        qualified_table(table),
        sql.SQL(", ").join(sql.Identifier(col) for col in columns),
        sql.SQL(", ").join(sql.Placeholder(col) for col in columns),
        sql.SQL(", ").join(
            [
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(col), sql.Identifier(col))
                for col in columns
                if col != "merge_key"
            ]
            + [sql.SQL("updated_at = NOW()")]
        ),
    )

    with pg_conn.cursor() as cur:
        cur.executemany(sql_stmt, rows)
    pg_conn.commit()


def aggregate_vietnamese_entries(pqb_paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    """Merge duplicate Vietnamese entries by word + pronunciation."""
    grouped: dict[str, dict[str, Any]] = {}
    for pqb_path in pqb_paths:
        for row in iter_pqb_entries(pqb_path):
            key = row["merge_key"]
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = {
                    "merge_key": key,
                    "word": row["word"],
                    "altword": row["altword"],
                    "pron": row["pron"],
                    "length": row["length"],
                    "sortkey": row["sortkey"],
                    "word_clean": row["word_clean"],
                    "pron_clean": row["pron_clean"],
                    "ovd_uids": [row["base_uid"]],
                    "ovd_source_files": [pqb_path.name],
                    "ovd_defns": [row["defn"]] if row["defn"] else [],
                    "ovd_raw_rows": [row["raw_dict"]],
                }
                continue

            existing["ovd_uids"].append(row["base_uid"])
            if pqb_path.name not in existing["ovd_source_files"]:
                existing["ovd_source_files"].append(pqb_path.name)
            if row["defn"]:
                existing["ovd_defns"].append(row["defn"])
            existing["ovd_raw_rows"].append(row["raw_dict"])

    for row in grouped.values():
        ovd_defn = "\n\n---\n\n".join(row.pop("ovd_defns"))
        row["ovd_defn"] = ovd_defn
        row["ovd_defn_clean"] = clean_pleco_text(ovd_defn)
        row["ovd_raw_data"] = Jsonb(row.pop("ovd_raw_rows"))
        yield row


def upsert_vietnamese_batch(
    pg_conn: psycopg.Connection,
    table: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return

    columns = [
        "merge_key",
        "word",
        "altword",
        "pron",
        "length",
        "sortkey",
        "word_clean",
        "pron_clean",
        "ovd_uids",
        "ovd_source_files",
        "ovd_defn",
        "ovd_defn_clean",
        "ovd_raw_data",
    ]
    sql_stmt = sql.SQL(
        """
        INSERT INTO {} ({}, has_ovd)
        VALUES ({}, TRUE)
        ON CONFLICT (merge_key) DO UPDATE SET
            ovd_uids = EXCLUDED.ovd_uids,
            ovd_source_files = EXCLUDED.ovd_source_files,
            ovd_defn = EXCLUDED.ovd_defn,
            ovd_defn_clean = EXCLUDED.ovd_defn_clean,
            ovd_raw_data = EXCLUDED.ovd_raw_data,
            has_ovd = TRUE,
            word = COALESCE({}.word, EXCLUDED.word),
            altword = COALESCE({}.altword, EXCLUDED.altword),
            pron = COALESCE({}.pron, EXCLUDED.pron),
            length = COALESCE({}.length, EXCLUDED.length),
            sortkey = COALESCE({}.sortkey, EXCLUDED.sortkey),
            word_clean = COALESCE({}.word_clean, EXCLUDED.word_clean),
            pron_clean = COALESCE({}.pron_clean, EXCLUDED.pron_clean),
            updated_at = NOW();
    """
    ).format(
        qualified_table(table),
        sql.SQL(", ").join(sql.Identifier(col) for col in columns),
        sql.SQL(", ").join(sql.Placeholder(col) for col in columns),
        *[qualified_table(table) for _ in range(7)],
    )

    with pg_conn.cursor() as cur:
        cur.executemany(sql_stmt, rows)
    pg_conn.commit()


def import_pqbs_to_supabase(
    pqb_dir: Path,
    db_url: str,
    table: str,
    batch_size: int,
) -> None:
    if not pqb_dir.exists():
        raise FileNotFoundError(f"Folder not found: {pqb_dir}")

    for table_part in table.split("."):
        validate_table_name(table_part)

    base_files, vietnamese_files = scan_pqb_files(pqb_dir)
    print("Base PQB files:", ", ".join(path.name for path in base_files))
    print("Vietnamese PQB files:", ", ".join(path.name for path in vietnamese_files))

    with psycopg.connect(db_url, row_factory=dict_row) as pg_conn:
        enable_trgm(pg_conn)
        create_table(pg_conn, table)

        base_total = 0
        for pqb_path in base_files:
            file_total = 0
            for batch in batched(iter_pqb_entries(pqb_path), batch_size):
                upsert_base_batch(pg_conn, table, batch)
                file_total += len(batch)
                base_total += len(batch)
                print(
                    f"Imported {file_total:,} base rows from {pqb_path.name} "
                    f"({base_total:,} total)...",
                    flush=True,
                )

        ovd_total = 0
        for batch in batched(aggregate_vietnamese_entries(vietnamese_files), batch_size):
            upsert_vietnamese_batch(pg_conn, table, batch)
            ovd_total += len(batch)
            print(f"Merged {ovd_total:,} Vietnamese rows...", flush=True)

    print(
        f"Done. Imported/upserted {base_total:,} base rows and merged "
        f"{ovd_total:,} Vietnamese rows into table '{table}'."
    )


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Scan a folder for Pleco .pqb files, import base dictionaries, "
            "and merge Vietnamese/OVD definitions into the same Supabase table."
        )
    )
    parser.add_argument(
        "--pqb-dir",
        default=".",
        help="Folder containing .pqb files. Default: current folder.",
    )
    parser.add_argument("--table", default=DEFAULT_TABLE, help=f"Target table name. Default: {DEFAULT_TABLE}")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Rows per batch")
    parser.add_argument(
        "--db-url",
        default=os.getenv("SUPABASE_DB_URL"),
        help="Supabase Postgres connection URL. Defaults to SUPABASE_DB_URL env var.",
    )

    args = parser.parse_args()

    if not args.db_url:
        raise RuntimeError(
            "Missing Supabase DB URL. Set SUPABASE_DB_URL in .env or pass --db-url."
        )

    import_pqbs_to_supabase(
        pqb_dir=Path(args.pqb_dir),
        db_url=args.db_url,
        table=args.table,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
