#!/usr/bin/env python3
"""Textual TUI for searching a Pleco .pqb Chinese dictionary."""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass, field
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


DEFAULT_DATA_DIR = Path(__file__).with_name("data")
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


def row_merge_key(row: sqlite3.Row) -> tuple[str, str]:
    word = clean_pleco_text(row["word"]) or clean_pleco_text(row["altword"])
    pron = clean_pleco_text(row["pron"])
    return word, pron


def row_merge_keys(row: sqlite3.Row) -> list[tuple[str, str]]:
    pron = clean_pleco_text(row["pron"])
    keys = {
        (word, pron)
        for word in [clean_pleco_text(row["word"]), clean_pleco_text(row["altword"])]
        if word and pron
    }
    primary_key = row_merge_key(row)
    if primary_key[0] and primary_key[1]:
        keys.add(primary_key)
    return sorted(keys)


@dataclass
class SourceDatabase:
    path: Path
    connection: sqlite3.Connection
    name: str
    language: str
    key_index: dict[tuple[str, str], list[int]] = field(default_factory=dict)


@dataclass
class MergedEntry:
    word: str
    altword: str
    pron: str
    english_definitions: list[str] = field(default_factory=list)
    vietnamese_definitions: list[str] = field(default_factory=list)
    other_definitions: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    uids: list[str] = field(default_factory=list)
    rank: int = 3

    @property
    def english_text(self) -> str:
        return "\n\n".join(self.english_definitions)

    @property
    def vietnamese_text(self) -> str:
        return "\n\n".join(self.vietnamese_definitions)

    @property
    def other_text(self) -> str:
        return "\n\n".join(self.other_definitions)

    @property
    def source_text(self) -> str:
        return ", ".join(self.sources)

    @property
    def uid_text(self) -> str:
        return ", ".join(self.uids)


class DictionaryStore:
    """Read-only search adapter that merges results from many Pleco dictionaries."""

    def __init__(self, db_paths: list[Path], limit: int = DEFAULT_LIMIT) -> None:
        self.db_paths = db_paths
        self.limit = limit
        self.sources: list[SourceDatabase] = []
        self.s2t = OpenCC("s2t") if OpenCC else None
        self.t2s = OpenCC("t2s") if OpenCC else None

    def open(self) -> None:
        if not self.db_paths:
            raise FileNotFoundError("No .pqb dictionaries found.")
        for db_path in self.db_paths:
            if not db_path.exists():
                raise FileNotFoundError(f"Dictionary not found: {db_path}")
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            name = self.source_name(connection, db_path)
            source = SourceDatabase(db_path, connection, name, self.source_language(name, db_path))
            source.key_index = self.build_key_index(source)
            self.sources.append(
                source
            )

    def close(self) -> None:
        for source in self.sources:
            source.connection.close()
        self.sources.clear()

    def source_name(self, connection: sqlite3.Connection, db_path: Path) -> str:
        row = connection.execute(
            """
            SELECT propvalue
            FROM pleco_dict_properties
            WHERE propid IN ('DictShortName', 'DictMenuName', 'DictName')
            ORDER BY
                CASE propid
                    WHEN 'DictShortName' THEN 0
                    WHEN 'DictMenuName' THEN 1
                    ELSE 2
                END
            LIMIT 1
            """
        ).fetchone()
        return clean_pleco_text(row["propvalue"]) if row else db_path.stem

    def source_language(self, source_name: str, db_path: Path) -> str:
        searchable = f"{source_name} {db_path.name}".lower()
        if "ovd" in searchable or "vietnamese" in searchable:
            return "vietnamese"
        if "cd-dict" in searchable or "english" in searchable:
            return "english"
        return "other"

    def count_entries(self) -> int:
        return sum(
            int(
                source.connection.execute(
                    "SELECT COUNT(*) FROM pleco_dict_entries"
                ).fetchone()[0]
            )
            for source in self.sources
        )

    def build_key_index(self, source: SourceDatabase) -> dict[tuple[str, str], list[int]]:
        index: dict[tuple[str, str], list[int]] = {}
        rows = source.connection.execute(
            "SELECT uid, word, altword, pron FROM pleco_dict_entries"
        )
        for row in rows:
            for key in row_merge_keys(row):
                index.setdefault(key, []).append(int(row["uid"]))
        return index

    def query_variants(self, query: str) -> list[str]:
        terms = {query.strip()}
        if self.s2t is not None and self.t2s is not None:
            terms.add(self.s2t.convert(query).strip())
            terms.add(self.t2s.convert(query).strip())
        return sorted(term for term in terms if term)

    def search_source(
        self, source: SourceDatabase, variants: list[str], source_limit: int
    ) -> list[sqlite3.Row]:
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
        params.append(source_limit)

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
        return list(source.connection.execute(sql, params))

    def search(self, query: str) -> list[MergedEntry]:
        variants = self.query_variants(query)
        if not variants:
            return []

        ranked_keys: dict[tuple[str, str], int] = {}
        source_limit = max(self.limit * 2, self.limit + 50)

        for source in self.sources:
            for row in self.search_source(source, variants, source_limit):
                rank = self.rank_row(row, variants)
                for key in row_merge_keys(row):
                    ranked_keys[key] = min(rank, ranked_keys.get(key, rank))

        entries = [
            self.join_entry(key, rank)
            for key, rank in ranked_keys.items()
            if key[0] and key[1]
        ]

        return sorted(
            entries,
            key=lambda entry: (entry.rank, len(entry.word), entry.word, entry.pron),
        )[: self.limit]

    def join_entry(self, key: tuple[str, str], rank: int) -> MergedEntry:
        key_word, key_pron = key
        entry = MergedEntry(word=key_word, altword="", pron=key_pron, rank=rank)
        preferred_word_set = False

        for source in sorted(self.sources, key=lambda item: item.language != "english"):
            for row in self.rows_for_key(source, key):
                word = clean_pleco_text(row["word"])
                altword = clean_pleco_text(row["altword"])
                pron = clean_pleco_text(row["pron"])

                if not preferred_word_set and source.language == "english":
                    entry.word = word or entry.word
                    entry.altword = altword
                    entry.pron = pron or entry.pron
                    preferred_word_set = True
                elif not entry.altword and altword:
                    entry.altword = altword

                self.add_definition(entry, source, clean_pleco_text(row["defn"]))
                if source.name not in entry.sources:
                    entry.sources.append(source.name)
                uid = f"{source.name}:{row['uid']}"
                if uid not in entry.uids:
                    entry.uids.append(uid)

        return entry

    def rows_for_key(self, source: SourceDatabase, key: tuple[str, str]) -> list[sqlite3.Row]:
        uids = source.key_index.get(key, [])
        if not uids:
            return []
        placeholders = ",".join("?" for _ in uids)
        rows = source.connection.execute(
            f"""
            SELECT uid, word, altword, pron, defn
            FROM pleco_dict_entries
            WHERE uid IN ({placeholders})
            ORDER BY uid
            """,
            uids,
        ).fetchall()
        return [row for row in rows if key in row_merge_keys(row)]

    def add_definition(
        self, entry: MergedEntry, source: SourceDatabase, definition: str
    ) -> None:
        if not definition:
            return
        target = {
            "english": entry.english_definitions,
            "vietnamese": entry.vietnamese_definitions,
        }.get(source.language, entry.other_definitions)
        if definition not in target:
            target.append(definition)

    def rank_row(self, row: sqlite3.Row, variants: list[str]) -> int:
        word = row["word"] or ""
        altword = row["altword"] or ""
        pron = row["pron"] or ""
        if word in variants:
            return 0
        if altword in variants:
            return 1
        if pron in variants:
            return 2
        return 3


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
        table.add_columns(
            "Word",
            "Alt Word",
            "Pronunciation",
            "English",
            "Vietnamese",
            "Other",
            "Sources",
            "UIDs",
        )
        count = self.store.count_entries()
        self.query_one("#status", Static).update(
            f"Ready. {count:,} entries loaded from {len(self.store.sources)} dictionaries."
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
                row.word,
                row.altword,
                row.pron,
                row.english_text,
                row.vietnamese_text,
                row.other_text,
                row.source_text,
                row.uid_text,
                key=f"{row.word}:{row.pron}",
            )

        suffix = f" Showing first {self.store.limit:,}." if len(rows) == self.store.limit else ""
        status.update(f"{len(rows):,} result(s) for '{self.query}'.{suffix}")


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Value must be greater than zero.")
    return number


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search merged Pleco .pqb dictionaries in a TUI.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Folder containing .pqb files. Default: {DEFAULT_DATA_DIR}",
    )
    parser.add_argument(
        "--db",
        action="append",
        type=Path,
        default=None,
        help="Specific .pqb file to search. Can be passed more than once.",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=DEFAULT_LIMIT,
        help=f"Maximum results to show. Default: {DEFAULT_LIMIT}",
    )
    return parser.parse_args(argv)


def discover_db_paths(args: argparse.Namespace) -> list[Path]:
    if args.db:
        return sorted(dict.fromkeys(path.resolve() for path in args.db))
    return sorted(args.data_dir.glob("*.pqb"))


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    app = DictSearchApp(DictionaryStore(discover_db_paths(args), args.limit))
    app.run()


if __name__ == "__main__":
    main()
