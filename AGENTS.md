# DW2-Russian Translation Mod

## Structure

- `1.x.x.x/English/` and `1.x.x.x/Russian/` — paired source/translation dirs per game version
- `batch_translate.py` — main translation orchestrator
- `translate.py` — single-file translator (XML/TXT via LLM)
- `system_prompt.py` — glossary + translation rules (parsed for `"<en>"→"<ru>"` pairs)
- `build_release.ps1` — creates release ZIP (`param($Version, $Beta)`)
- `XSDs/` — XML schema files for reference
- `releases/` — output ZIP packages
- `mod.json` — Steam workshop manifest (version bumped by `build_release.ps1`)

## Commands

```powershell
# Translate one file (default: first eligible)
python batch_translate.py

# Translate all files
python batch_translate.py --all

# Check already-translated Russian files for untranslated English
python batch_translate.py --check

# Collect words for glossary expansion (no translation)
python batch_translate.py --words

# Fix untranslated elements in existing Russian XML files
python batch_translate.py --fix-untranslated

# Replace actual newlines with literal \n in Russian XML
python batch_translate.py --fix-newlines

# Build release ZIP
.\build_release.ps1 -Version 1.3.5.8
.\build_release.ps1 -Version 1.3.5.8 -Beta
```

Default target version: `1.3.5.7`. Override with `--target-version`. Cache sources default to `1.3.4.3`, override with `--cache-from`.

## Translation cache

- `.glossary_cache.pkl` — glossary from `system_prompt.py`
- `.translation_cache_*.pkl` — built from paired English/Russian files across versions
- `.previous_translation_cache_*.pkl` — cache from prior versions only
- `.translation_cache_file_*.pkl` — per-file local cache (passed via `DW2_FILE_TRANSLATION_CACHE`)

Cache files are `.pkl` — listed in `.gitignore`. They are regenerated from paired files if missing.

## Gotchas

- **Galactopedia filenames** get translated to Russian during output path resolution. The script resolves these dynamically.
- **Pause/resume**: `.progress.pkl` files are created during translation. Remove them to force re-translation.
- **Pool mode**: pass `--pool host:port[,host2:port2]` for distributed LLM workers with failover.
- **XML technical tags** (`Type`, `ImageFilename`, `RaceId`, `Amount`, etc.) are skipped during translation and checking.
- **TXT format**: lines with `;` translate only the portion after the semicolon. Lines without `;` translate the full line.
- **`_log.txt` files** are translation logs — in `.gitignore`.
- **`NEWLINE_REPLACEMENT.md`** documents the `\n` escaping issue and post-processing workflow.

## File types handled

XML: `GameText.txt`, `Hints.txt`, `ShipHulls*.xml`, `GameEvents*.xml`, `ResearchProjectDefinitions.xml`, `Races.xml`, `Governments*.xml`, `TroopDefinitions*.xml`, `TourItems.xml`, `SpaceItemDefinitions.xml`, `Resources.xml`, `OrbTypes.xml`, `PlanetaryFacilityDefinitions*.xml`, `CharacterDefinition*.xml`, etc.

TXT: `GameText.txt`, `Hints.txt`, `SystemNames.txt`, Galactopedia `.txt` files.
