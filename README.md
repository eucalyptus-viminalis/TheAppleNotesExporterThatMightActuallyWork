# The Apple Notes Exporter That Might Actually Work

	And if you don't love me now  
	You will never love me again  
	I can still hear you saying  
	You would never break the chain (never break the chain)

<figure align="center" style="margin: 32px 0;">
  <div style="
    background: rgba(30, 30, 35, 0.7);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border-radius: 28px;
    padding: 24px;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
    border: 0.5px solid rgba(255, 255, 255, 0.2);
    display: inline-block;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
  " 
  onmouseover="this.style.transform='scale(1.01)'; this.style.boxShadow='0 28px 48px rgba(0,0,0,0.5)';"
  onmouseout="this.style.transform='scale(1)'; this.style.boxShadow='0 20px 40px rgba(0,0,0,0.4)';">
    <div style="
      background: #000;
      border-radius: 12px;
      padding: 12px;
      box-shadow: inset 0 0 20px rgba(0, 0, 0, 0.5), 0 0 0 2px rgba(255, 255, 255, 0.1);
    ">
      <img src="assets/successful-export.png" width="630" alt="terminal screenshot of a successful export" style="border-radius: 0px; display: block; opacity: 0.95;">
    </div>
    <figcaption style="margin-top: 12px; padding-bottom: 4px; color: rgba(255, 255, 255, 0.7); font-size: 13px; font-weight: 400; text-align: center; letter-spacing: 0.3px;">
      <em>⛓️‍💥</em>
    </figcaption>
  </div>
</figure>

## Foreword

**Script:** `export_notes.py`

## Rejected

- <https://github.com/threeplanetssoftware/apple_cloud_notes_parser>
- <https://github.com/kzaremski/apple-notes-exporter>

## Download

```sh
git clone https://github.com/eucalyptus-viminalis/TheAppleNotesExporterThatMightActuallyWork
cd TheAppleNotesExporterThatMightActuallyWork
pip install markdownify  # optional, for Markdown export
```

## Commands

```sh
# optional but recommended
pip install markdownify

# usage
python export_notes.py --help

# export everything (HTML + Markdown) to ~/Downloads/AppleNotesExport
python export_notes.py

# export one folder, Markdown only
python export_notes.py --folder "Work" --format md

# preview without writing files
python export_notes.py --dry-run

# Obsidian-friendly output
python export_notes.py --format md --obsidian-images --no-frontmatter
```

On first run, macOS will prompt Terminal to control Notes.app — click OK.

## Property

**Accuracy**
- Faithful HTML-to-Markdown conversion
- Handles Apple Notes HTML quirks (fragmented tags, NBSP, broken nested lists)

**Simplicity**
- Single file, nothing to install or configure
- stdlib only; `markdownify` is an optional dependency for Markdown export
- Requires Notes.app automation permission (macOS prompts on first run)

**Limitations**
- macOS only — uses AppleScript via Notes.app
- Slow on large libraries; AppleScript traversal has no bulk API

## Features

**Export**
- Exports HTML and Markdown, or either alone (`--format`)
- Custom output directory (`--out`)
- Filter to a single folder (`--folder`)
- Dry run: preview notes without writing files (`--dry-run`)

**Markdown**
- YAML front matter with title, folder, created, modified dates
- `--no-frontmatter` and `--no-title` flags for clean output
- 4-space list indent for Obsidian compatibility

**Images**
- Extract base64-encoded images to `_attachments/` (`--extract-images`)
- Obsidian `![[]]` link syntax (`--obsidian-images`)

## Last Good

| component | version | date       |
| --------- | ------- | ---------- |
| Notes     | 4.13    | 2026-03-31 |
| macOS     | v26.3   | 2026-03-31 |

## TODO

- [ ] tests for typical notes and edge cases
- [ ] try exporting notes with code blocks, and other funky elements
