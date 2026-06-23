# py-cli-cn-dict

A terminal UI for searching Pleco `.pqb` dictionaries.

The app uses Textual and searches every `.pqb` file in `data/` by default. You can type Chinese traditional or simplified characters, pinyin, English, or Vietnamese, and matching entries appear in one merged table with separate English and Vietnamese definition columns.

## Structure

```text
myclidict.py
data/
  Pleco_OVD-Dict.pqb
  other-dictionaries.pqb
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

Use a different folder of `.pqb` files:

```sh
uv run myclidict --data-dir path/to/dictionaries
```

Use one or more specific `.pqb` files:

```sh
uv run myclidict --db path/to/dictionary.pqb
uv run myclidict --db dict-a.pqb --db dict-b.pqb
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

The default dictionary folder is:

```text
data/
```

Every `.pqb` in that folder is queried. Matching rows are converted into a Chinese headword + pronunciation key, then the app fetches the matching records from the other dictionaries. In practice, the Chinese-English dictionary acts like the base table and the Chinese-Vietnamese dictionary is joined onto it when the key matches.

The table displays:

```text
Word
Alt Word
Pronunciation
English
Vietnamese
Other
Sources
UIDs
```

Traditional and simplified Chinese input are both supported through OpenCC conversion.
