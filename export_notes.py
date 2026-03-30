#!/usr/bin/env python3
"""
Apple Notes Exporter
====================
Exports all Apple Notes to HTML and/or Markdown, preserving folder structure.

Requirements (install once):
    pip install markdownify

Usage:
    python export_notes.py                        # export both HTML + Markdown
    python export_notes.py --format html          # HTML only
    python export_notes.py --format md            # Markdown only
    python export_notes.py --out ~/Desktop/Notes  # custom output directory
    python export_notes.py --folder "Work"        # only one folder
    python export_notes.py --dry-run              # preview without writing files

On first run macOS will ask permission for Terminal to control Notes.app — click OK.
"""

import subprocess
import re
import argparse
import json
from pathlib import Path
from datetime import datetime

# ── Optional dependency ──────────────────────────────────────────────────────
try:
    from markdownify import markdownify as html_to_md
    HAS_MARKDOWNIFY = True
except ImportError:
    HAS_MARKDOWNIFY = False


# ── Delimiters used to parse AppleScript output ──────────────────────────────
NOTE_SEP  = "|||NOTE|||"
FIELD_SEP = "|||F|||"


# ── AppleScript: dumps every note as a delimited record ─────────────────────
def build_applescript(folder_filter):
    if folder_filter:
        folder_clause = f'set allFolders to (every folder whose name is "{folder_filter}")'
    else:
        folder_clause = "set allFolders to every folder"

    return f"""
use AppleScript version "2.4"
use scripting additions

set fieldSep to "{FIELD_SEP}"
set noteSep to "{NOTE_SEP}"
set output to ""

tell application "Notes"
    {folder_clause}
    repeat with aFolder in allFolders
        set folderName to name of aFolder
        set folderNotes to every note in aFolder
        repeat with aNote in folderNotes
            set noteTitle to name of aNote
            set noteBody to body of aNote
            set noteCreated to creation date of aNote as string
            set noteModified to modification date of aNote as string
            set output to output & folderName & fieldSep & noteTitle & fieldSep & noteCreated & fieldSep & noteModified & fieldSep & noteBody & noteSep
        end repeat
    end repeat
end tell
return output
"""


# ── Run AppleScript and return raw output ────────────────────────────────────
def fetch_notes_raw(folder_filter=None):
    script = build_applescript(folder_filter)
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=300,  # 5 min — large libraries can be slow
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"AppleScript error:\n{result.stderr.strip()}\n\n"
            "Fix: go to System Settings → Privacy & Security → Automation\n"
            "     and enable Terminal → Notes.app"
        )
    return result.stdout


# ── Parse the raw output into a list of note dicts ──────────────────────────
def parse_notes(raw):
    notes = []
    for record in raw.split(NOTE_SEP):
        record = record.strip()
        if not record:
            continue
        parts = record.split(FIELD_SEP, 4)  # max 5 parts
        if len(parts) < 5:
            continue  # malformed / empty note
        folder, title, created, modified, body = parts
        clean_title = " ".join(title.strip().split())  # collapse all whitespace/newlines
        print(clean_title)
        notes.append({
            "folder":   folder.strip(),
            "title":    clean_title,
            "created":  created.strip(),
            "modified": modified.strip(),
            "html":     clean_apple_html(body.strip(), clean_title),
        })
    return notes


# ── Sanitise a string so it's safe to use as a filename ─────────────────────
def safe_filename(name, max_len=80):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(". ")
    return name[:max_len] or "untitled"


# ── Clean up Apple Notes HTML quirks ─────────────────────────────────────────
def clean_apple_html(body, title=""):
    """Transform raw Apple Notes HTML into clean, semantic HTML."""

    # Merge adjacent identical tags that Apple fragments across characters
    # e.g. <h1>CO</h1><h1>SC</h1> → <h1>COSC</h1>
    # e.g. <b>part1</b><b>part2</b> → <b>part1part2</b>
    for tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'b', 'i', 'u', 'em', 'strong'):
        body = re.sub(rf'</{tag}>\s*<{tag}>', '', body)

    # Strip trailing <br> before closing block tags
    body = re.sub(r'<br\s*/?>\s*(</(?:div|li|h[1-6]|p|td|th)>)', r'\1', body)

    # Collapse empty inline wrappers: <b><br></b> → <br>,  <b></b> → ""
    body = re.sub(
        r'<(b|i|u|em|strong)>\s*(?:<br\s*/?>)?\s*</\1>',
        lambda m: '<br>' if '<br' in m.group() else '',
        body,
    )

    # Remove duplicate leading title (Apple Notes repeats it in the body)
    if title:
        title_pat = r'\s+'.join(re.escape(w) for w in title.split())
        body = re.sub(
            rf'^\s*(?:<div>\s*)?<h1>\s*{title_pat}\s*</h1>(?:\s*</div>)?\s*',
            '', body, count=1,
        )

    # Remove Apple-specific class attributes
    body = re.sub(r'\s+class="Apple-[^"]*"', '', body)

    # Fix Apple's broken nested lists: <li>text</li><ul> → <li>text\n<ul>
    # (move the nested list inside the preceding <li> instead of after it)
    body = re.sub(r'</li>\s*(<(?:ul|ol)[\s>])', r'\n\1', body)
    # Close the <li> after the nested list ends
    body = re.sub(r'(</(?:ul|ol)>)\s*(?=</(?:ul|ol)>|<li[\s>])', r'\1\n</li>', body)

    # Unwrap <div> around block-level elements
    body = re.sub(
        r'<div>\s*(<(?:h[1-6]|ul|ol|table|blockquote|pre)[\s>])', r'\1', body,
    )
    body = re.sub(
        r'(</(?:h[1-6]|ul|ol|table|blockquote|pre)>)\s*</div>', r'\1', body,
    )

    # Remove blank-line divs: <div><br></div>, <div></div>
    body = re.sub(r'<div>\s*(?:<br\s*/?>)?\s*</div>', '', body)

    # Convert remaining content <div>s to semantic <p>s
    body = re.sub(r'<div>(.*?)</div>', r'<p>\1</p>', body)

    # Remove empty paragraphs
    body = re.sub(r'<p>\s*</p>', '', body)

    # Collapse excessive blank lines
    body = re.sub(r'\n{3,}', '\n\n', body)

    return body.strip()


# ── Wrap note HTML in a complete, styled page ────────────────────────────────
def wrap_html(title, created, modified, folder, body):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 780px; margin: 40px auto; padding: 0 24px;
      color: #1d1d1f; line-height: 1.65; background: #fff;
    }}
    h1 {{ font-size: 1.9em; margin-bottom: 0.25em; }}
    h2 {{ font-size: 1.5em; margin-top: 1.4em; margin-bottom: 0.3em; }}
    h3 {{ font-size: 1.25em; margin-top: 1.2em; margin-bottom: 0.3em; }}
    p  {{ margin: 0.6em 0; }}
    a  {{ color: #0066cc; }}
    a:hover {{ text-decoration: underline; }}
    .meta {{
      color: #6e6e73; font-size: 0.85em;
      border-bottom: 1px solid #e5e5ea; padding-bottom: 0.8em; margin-bottom: 1.6em;
    }}
    ul, ol {{ padding-left: 1.8em; margin: 0.6em 0; }}
    li {{ margin: 0.2em 0; }}
    img  {{ max-width: 100%; height: auto; border-radius: 6px; }}
    pre  {{ background: #f5f5f7; padding: 14px; border-radius: 8px; overflow-x: auto; }}
    code {{ font-family: "SF Mono", Menlo, monospace; font-size: 0.88em; }}
    blockquote {{
      border-left: 3px solid #d1d1d6; margin: 0; padding-left: 16px; color: #555;
    }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d1d1d6; padding: 8px 12px; text-align: left; }}
    th {{ background: #f5f5f7; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="meta">
    📁 {folder} &nbsp;&middot;&nbsp;
    Created: {created} &nbsp;&middot;&nbsp;
    Modified: {modified}
  </div>
  {body}
</body>
</html>"""


# ── Convert HTML body → Markdown with YAML front matter ─────────────────────
def to_markdown(title, created, modified, folder, html_body):
    if HAS_MARKDOWNIFY:
        body_md = html_to_md(
            html_body,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style"],
        )
    else:
        # Simple fallback: strip all HTML tags
        body_md = re.sub(r"<[^>]+>", "", html_body)
        body_md = re.sub(r"\n{3,}", "\n\n", body_md).strip()

    frontmatter = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"folder: \"{folder}\"\n"
        f"created: \"{created}\"\n"
        f"modified: \"{modified}\"\n"
        f"---\n\n"
    )
    return frontmatter + f"# {title}\n\n" + body_md.strip() + "\n"


# ── Deduplicate filenames within a directory ─────────────────────────────────
def unique_name(used_set, name):
    if name not in used_set:
        used_set.add(name)
        return name
    i = 2
    while f"{name} ({i})" in used_set:
        i += 1
    result = f"{name} ({i})"
    used_set.add(result)
    return result


# ── Write one note to disk ───────────────────────────────────────────────────
def write_note(note, out_dir, fmt, used_names):
    folder_dir = out_dir / fmt / safe_filename(note["folder"])
    folder_dir.mkdir(parents=True, exist_ok=True)

    base = safe_filename(note["title"])
    key  = str(folder_dir)
    used_names.setdefault(key, set())
    base = unique_name(used_names[key], base)

    if fmt == "html":
        content  = wrap_html(note["title"], note["created"], note["modified"],
                             note["folder"], note["html"])
        filepath = folder_dir / f"{base}.html"
    else:
        content  = to_markdown(note["title"], note["created"], note["modified"],
                               note["folder"], note["html"])
        filepath = folder_dir / f"{base}.md"

    filepath.write_text(content, encoding="utf-8")
    return filepath


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Export Apple Notes to HTML / Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--format", choices=["html", "md", "both"], default="both",
        help="Output format  (default: both)",
    )
    parser.add_argument(
        "--out",
        default=str(Path.home() / "Downloads" / "AppleNotesExport"),
        help="Root output directory  (default: ~/Downloads/AppleNotesExport)",
    )
    parser.add_argument(
        "--folder", default=None,
        help="Only export notes from this folder name",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch notes but don't write files — just print a summary",
    )
    args = parser.parse_args()

    out_dir = Path(args.out).expanduser().resolve()
    formats = ["html", "md"] if args.format == "both" else [args.format]

    # ── Warn if markdownify is missing and Markdown is requested ────────────
    if "md" in formats and not HAS_MARKDOWNIFY:
        print("⚠️  markdownify not found — Markdown export will use plain-text fallback.")
        print("   For better results: pip install markdownify\n")

    print("📓 Fetching notes from Notes.app…")
    if args.folder:
        print(f"   Folder filter: {args.folder!r}")

    raw   = fetch_notes_raw(args.folder)
    notes = parse_notes(raw)

    if not notes:
        print("⚠️  No notes found.")
        print("   • Check the folder name (case-sensitive)")
        print("   • Check Terminal permissions: System Settings → Privacy & Security → Automation")
        return

    folders = sorted({n["folder"] for n in notes})
    print(f"   Found {len(notes)} note(s) in {len(folders)} folder(s): {', '.join(folders)}")

    if args.dry_run:
        print("\nDry-run — notes that would be exported:")
        for n in notes:
            print(f"  [{n['folder']}]  {n['title']}")
        return

    # ── Export ───────────────────────────────────────────────────────────────
    used_names = {}
    counts = {fmt: 0 for fmt in formats}

    for i, note in enumerate(notes, 1):
        for fmt in formats:
            write_note(note, out_dir, fmt, used_names)
            counts[fmt] += 1
        # Progress every 25 notes
        if i % 25 == 0 or i == len(notes):
            print(f"   {i}/{len(notes)} notes processed…", end="\r")

    print()
    print(f"\n✅ Export complete → {out_dir}")
    for fmt, count in counts.items():
        label = "HTML" if fmt == "html" else "Markdown"
        folder_count = len(folders)
        print(f"   {label}: {count} file(s) across {folder_count} folder(s)")

    # ── Write a manifest JSON ────────────────────────────────────────────────
    manifest = {
        "exported_at":  datetime.now().isoformat(),
        "total_notes":  len(notes),
        "formats":      formats,
        "folder_filter": args.folder,
        "folders":      folders,
        "notes": [
            {"folder": n["folder"], "title": n["title"],
             "created": n["created"], "modified": n["modified"]}
            for n in notes
        ],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"   Manifest written: manifest.json")


if __name__ == "__main__":
    main()
