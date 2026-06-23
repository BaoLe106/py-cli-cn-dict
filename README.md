# py-cli-cn-dict

A terminal UI for searching a Pleco Chinese-Vietnamese `.pqb` dictionary.

The app uses Textual and searches `data/Pleco_OVD-Dict.pqb` by default. You can type Chinese traditional or simplified characters, pinyin, English, or Vietnamese, and matching entries appear in a table.

## Structure

```text
myclidict.py
data/
  Pleco_OVD-Dict.pqb
```

## Install

Install `uv` first if you do not already have it:

```sh
pip install uv
```

Then install project dependencies:

```sh
uv sync
```

## Run

Start the TUI:

```sh
uv run myclidict
```

Or run the file directly:

```sh
uv run python myclidict.py
```

Use another `.pqb` file:

```sh
uv run myclidict --db path/to/dictionary.pqb
```

Limit the number of visible results:

```sh
uv run myclidict --limit 100
```

## Use

Type a search term in the input at the top of the screen. Results update live in the table.

Useful keys:

```text
Esc      clear search
Ctrl+C   quit
```

## Data

The default dictionary file is:

```text
data/Pleco_OVD-Dict.pqb
```

The table displays:

```text
UID
Word
Alt Word
Pronunciation
Definition
```

Traditional and simplified Chinese input are both supported through OpenCC conversion.
