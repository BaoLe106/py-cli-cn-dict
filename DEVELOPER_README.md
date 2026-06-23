# Developer Guide

This project is a small Python Textual TUI that searches a Pleco `.pqb` SQLite dictionary.

## Requirements

- Python 3.11 or newer
- `uv`
- Git LFS, if you are committing `.pqb` dictionary files

## Install Dependencies

From the project root:

```sh
uv sync
```

Run the TUI during development:

```sh
uv run python myclidict.py
```

Or use the script entry:

```sh
uv run myclidict
```

Run lint checks:

```sh
uv run ruff check .
```

## Project Structure

```text
myclidict.py
data/
  Pleco_OVD-Dict.pqb
README.md
DEVELOPER_README.md
pyproject.toml
uv.lock
```

`myclidict.py` contains the Textual app and the read-only SQLite search adapter.

`data/Pleco_OVD-Dict.pqb` is the default dictionary file used by the TUI. The app can search Chinese, pinyin, English, and Vietnamese text from `pleco_dict_entries`.

`pqb_to_supabase.py` is a separate utility for importing Pleco dictionaries into Supabase. It is not required to run the TUI.

## Git Notes

The repo ignores local agent folders, interpreter folders, virtual environments, and environment files.

`.pqb` files are tracked with Git LFS because the OVD dictionary is larger than GitHub's regular file size limit.
