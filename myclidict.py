#!/usr/bin/env python3
"""Textual TUI for searching a Pleco .pqb Chinese dictionary."""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Input, Static

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - dependency is declared for app installs
    OpenCC = None


DEFAULT_DB = Path(__file__).with_name("data") / "Pleco_OVD-Dict.pqb"
DEFAULT_LIMIT = 200
PLECO_MARKERS = {
    "@": "",
    "\ueab1- ": "; ",
    "\ueab1": "; ",
    "\ueac7": "",
    "\ueac8": "",
}


def clean_pleco_text(value: object) -> str:
    """Remove common Pleco inline markers while preserving searchable text."""
    if value is None:
        return ""
    text = str(value)
    for marker, replacement in PLECO_MARKERS.items():
        text = text.replace(marker, replacement)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class DictionaryStore:
    """Read-only search adapter for a Pleco SQLite dictionary."""

    def __init__(self, db_path: Path, limit: int = DEFAULT_LIMIT) -> None:
        self.db_path = db_path
        self.limit = limit
        self.connection: sqlite3.Connection | None = None
        self.s2t = OpenCC("s2t") if OpenCC else None
        self.t2s = OpenCC("t2s") if OpenCC else None

    def open(self) -> None:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Dictionary not found: {self.db_path}")
        self.connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def count_entries(self) -> int:
        assert self.connection is not None
        return int(
            self.connection.execute("SELECT COUNT(*) FROM pleco_dict_entries").fetchone()[0]
        )

    def query_variants(self, query: str) -> list[str]:
        terms = {query.strip()}
        if self.s2t is not None and self.t2s is not None:
            terms.add(self.s2t.convert(query).strip())
            terms.add(self.t2s.convert(query).strip())
        return sorted(term for term in terms if term)

    def search(self, query: str) -> list[sqlite3.Row]:
        assert self.connection is not None
        variants = self.query_variants(query)
        if not variants:
            return []

        params: list[str | int] = []
        clauses: list[str] = []
        for variant in variants:
            exact = variant
            like = f"%{variant}%"
            clauses.append(
                """
                word = ? OR altword = ? OR pron = ?
                OR word LIKE ? COLLATE NOCASE
                OR altword LIKE ? COLLATE NOCASE
                OR pron LIKE ? COLLATE NOCASE
                OR defn LIKE ? COLLATE NOCASE
                """
            )
            params.extend([exact, exact, exact, like, like, like, like])
        params.append(self.limit)

        sql = f"""
            SELECT uid, word, altword, pron, defn
            FROM pleco_dict_entries
            WHERE {" OR ".join(f"({clause})" for clause in clauses)}
            ORDER BY
                CASE
                    WHEN word IN ({",".join("?" for _ in variants)}) THEN 0
                    WHEN altword IN ({",".join("?" for _ in variants)}) THEN 1
                    WHEN pron IN ({",".join("?" for _ in variants)}) THEN 2
                    ELSE 3
                END,
                length(word),
                uid
            LIMIT ?
        """
        params[-1:-1] = variants + variants + variants
        return list(self.connection.execute(sql, params))


class DictSearchApp(App[None]):
    """A compact dictionary lookup app."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #search {
        dock: top;
        margin: 0 1;
    }

    #status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("escape", "clear_search", "Clear"),
    ]

    query = reactive("")

    def __init__(self, store: DictionaryStore) -> None:
        super().__init__()
        self.store = store

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Input(
                placeholder="Search Chinese, pinyin, English, or Vietnamese...",
                id="search",
            )
            yield Static("Loading dictionary...", id="status")
            yield DataTable(id="results", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.store.open()
        table = self.query_one(DataTable)
        table.add_columns("UID", "Word", "Alt Word", "Pronunciation", "Definition")
        count = self.store.count_entries()
        self.query_one("#status", Static).update(
            f"Ready. {count:,} entries loaded from {self.store.db_path}."
        )
        self.query_one(Input).focus()

    def on_unmount(self) -> None:
        self.store.close()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.query = event.value.strip()
        self.refresh_results()

    def action_clear_search(self) -> None:
        self.query_one(Input).value = ""
        self.query = ""
        self.refresh_results()

    def refresh_results(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        status = self.query_one("#status", Static)

        if not self.query:
            status.update("Type Chinese, pinyin, English, or Vietnamese to search.")
            return

        rows = self.store.search(self.query)
        for row in rows:
            table.add_row(
                str(row["uid"]),
                clean_pleco_text(row["word"]),
                clean_pleco_text(row["altword"]),
                clean_pleco_text(row["pron"]),
                clean_pleco_text(row["defn"]),
                key=str(row["uid"]),
            )

        suffix = f" Showing first {self.store.limit:,}." if len(rows) == self.store.limit else ""
        status.update(f"{len(rows):,} result(s) for '{self.query}'.{suffix}")


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Value must be greater than zero.")
    return number


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search a Pleco .pqb dictionary in a TUI.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to a Pleco .pqb SQLite file. Default: {DEFAULT_DB}",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=DEFAULT_LIMIT,
        help=f"Maximum results to show. Default: {DEFAULT_LIMIT}",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    app = DictSearchApp(DictionaryStore(args.db, args.limit))
    app.run()


if __name__ == "__main__":
    main()
