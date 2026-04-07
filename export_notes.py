#!/usr/bin/env python3
"""
The Apple Notes Exporter That Might Actually Work
=================================================
macOS only — requires Notes.app (macOS 10.15 Catalina or later).

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
    python export_notes.py --no-frontmatter       # omit YAML front matter
    python export_notes.py --no-title             # omit # Title heading
    python export_notes.py --extract-images       # extract images, use ![](path) links
    python export_notes.py --obsidian-images      # extract images, use ![[]] links

On first run macOS will ask permission for Terminal to control Notes.app — click OK.
"""

import subprocess
import re
import argparse
import json
import base64
import time
from pathlib import Path
from datetime import datetime

# ── Optional dependency ──────────────────────────────────────────────────────
try:
    from markdownify import MarkdownConverter, re_line_with_content
    HAS_MARKDOWNIFY = True
except ImportError:
    HAS_MARKDOWNIFY = False


# ── Custom Markdown converter with configurable list indent ────────────────
if HAS_MARKDOWNIFY:
    class AppleNotesConverter(MarkdownConverter):
        """MarkdownConverter subclass that uses 4-space list indent (Obsidian-friendly)."""

        class DefaultOptions(MarkdownConverter.DefaultOptions):
            list_indent = 4

        def convert_li(self, el, text, parent_tags):
            text = (text or '').strip()

            parent = el.parent
            if parent is not None and parent.name == 'ol':
                if parent.get("start") and str(parent.get("start")).isnumeric():
                    start = int(parent.get("start"))
                else:
                    start = 1
                bullet = '%s.' % (start + len(el.find_previous_siblings('li')))
                if not text:
                    return '%s\n' % bullet
            else:
                if not text:
                    return "\n"
                depth = -1
                while el:
                    if el.name == 'ul':
                        depth += 1
                    el = el.parent
                bullets = self.options['bullets']
                bullet = bullets[depth % len(bullets)]
            bullet = bullet + ' '

            indent = self.options['list_indent']
            indent_str = ' ' * indent

            def _indent_for_li(match):
                line_content = match.group(1)
                return indent_str + line_content if line_content else ''
            text = re_line_with_content.sub(_indent_for_li, text)

            text = bullet + text[indent:]
            return '%s\n' % text

        def convert_img(self, el, text, parent_tags):
            src = el.attrs.get('src', '') or ''
            if not self.options.get('extract_images') or not src.startswith('data:image/'):
                return super().convert_img(el, text, parent_tags)

            match = re.match(r'data:image/(\w+);base64,(.+)', src, re.DOTALL)
            if not match:
                return super().convert_img(el, text, parent_tags)

            ext = match.group(1)
            if ext == 'jpeg':
                ext = 'jpg'
            b64_data = match.group(2)

            note_title = self.options.get('note_title', 'image')
            image_list = self.options.get('image_list')
            idx = len(image_list) + 1
            filename = f"{safe_filename(note_title)}_{idx}.{ext}"

            image_names = self.options.get('image_names')
            if image_names is not None:
                filename = unique_name(image_names, filename)

            image_list.append({'filename': filename, 'data': b64_data, 'ext': ext})

            if self.options.get('obsidian_images'):
                return f'![[{filename}]]'
            return f'![](_attachments/{filename})'

    def html_to_md(html, **options):
        return AppleNotesConverter(**options).convert(html)


# ── Delimiters used to parse AppleScript output ──────────────────────────────
NOTE_SEP  = "|||NOTE|||"
FIELD_SEP = "|||F|||"


# ── AppleScript: dumps every note as a delimited record ─────────────────────
def _as_escape(value):
    """Escape a string for safe interpolation inside an AppleScript double-quoted literal."""
    return value.replace('\\', '\\\\').replace('"', '\\"')


def build_applescript(folder_filter):
    if folder_filter:
        folder_clause = f'set allFolders to (every folder whose name is "{_as_escape(folder_filter)}")'
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

    # Matches full URLs and bare domains commonly auto-linked by Apple Notes.
    # Purposefully excludes '@' so email addresses are not linkified here.
    url_text_re = re.compile(
        r'(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<]*)?',
        flags=re.IGNORECASE,
    )

    def _href_from_text(url_text):
        text = url_text.strip()
        if re.match(r'^https?://', text, flags=re.IGNORECASE):
            return text
        return f"https://{text}"

    inline_url_re = re.compile(
        rf'(?<![\w@/])({url_text_re.pattern})(?![\w/])',
        flags=re.IGNORECASE,
    )
    email_text_re = re.compile(
        r'(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])'
    )

    def _heading_joiner(left, right):
        """Choose spacing when stitching fragmented heading text."""
        if not left or not right:
            return ''
        if left[-1].isspace() or right[0].isspace():
            return ''
        if right[0] in '.,;:!?)]}':
            return ''

        l = left[-1]
        r = right[0]
        if l.isdigit() and r.isalpha():
            return ' '
        if l.isalpha() and r.isdigit():
            return ' '
        if l.islower() and r.isupper():
            return ' '
        if l.isupper() and r.isupper():
            # Acronym followed by title-cased word, e.g. "AWS" + "Pricing".
            if len(right) > 1 and right[1].islower():
                return ' '
            return ''
        return ''

    def _merge_fragmented_headings(html, allow_br=False):
        """Merge same-level heading fragments emitted by Apple Notes."""
        sep = r'(?:\s|<br\s*/?>)*' if allow_br else r'\s*'
        for tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            pattern = re.compile(
                rf'<{tag}(\s+[^>]*)?>([^<]*)</{tag}>{sep}<{tag}(?:\s+[^>]*)?>([^<]*)</{tag}>',
                flags=re.IGNORECASE,
            )
            while True:
                def _merge_parts(match):
                    attrs = match.group(1) or ''
                    left = match.group(2) or ''
                    right = match.group(3) or ''
                    return f'<{tag}{attrs}>{left}{_heading_joiner(left, right)}{right}</{tag}>'

                html, count = pattern.subn(_merge_parts, html)
                if count == 0:
                    break
        return html

    common_tlds = {
        "com", "net", "org", "edu", "gov", "mil", "int",
        "io", "co", "ai", "app", "dev", "me", "biz", "info",
        "us", "uk", "au", "ca", "de", "fr", "jp", "cn", "in", "nz",
        "sg", "kr", "nl", "se", "no", "fi", "es", "it", "ch",
    }

    def _is_likely_web_url_or_domain(url_text):
        text = url_text.strip().strip("()[]{}<>,;:'\"")
        if not text or "@" in text or " " in text:
            return False
        if re.match(r'^https?://', text, flags=re.IGNORECASE):
            return True
        if "/" in text:
            host = text.split("/", 1)[0]
        else:
            host = text
        if "." not in host:
            return False
        labels = [p for p in host.split(".") if p]
        if len(labels) < 2:
            return False
        tld = labels[-1].lower()
        if tld not in common_tlds:
            return False
        return bool(re.search(r"[a-z]", labels[-2], flags=re.IGNORECASE))

    def _linkify_plain_text_fragment(text):
        email_tokens = {}

        def _protect_email(match):
            email = match.group(1)
            token = f"__APPLE_NOTES_EMAIL_{len(email_tokens)}__"
            email_tokens[token] = f'<a href="mailto:{email}">{email}</a>'
            return token

        text = email_text_re.sub(_protect_email, text)

        def _replace(match):
            candidate = match.group(1)
            if not _is_likely_web_url_or_domain(candidate):
                return candidate
            href = _href_from_text(candidate)
            return f'<a href="{href}">{candidate}</a>'

        text = inline_url_re.sub(_replace, text)
        for token, replacement in email_tokens.items():
            text = text.replace(token, replacement)
        return text

    def _linkify_inline_plaintext(html):
        parts = re.split(r'(<[^>]+>)', html)
        out = []
        in_anchor = False

        for part in parts:
            if not part:
                continue
            if part.startswith('<'):
                if re.match(r'<a\b', part, flags=re.IGNORECASE):
                    in_anchor = True
                elif re.match(r'</a\b', part, flags=re.IGNORECASE):
                    in_anchor = False
                out.append(part)
                continue

            if in_anchor:
                out.append(part)
            else:
                out.append(_linkify_plain_text_fragment(part))

        return ''.join(out)

    # Handle Apple Notes Unicode line separators (U+2028) and associated NBSP
    # Apple uses U+2028 for within-paragraph line breaks; the HTML body sometimes
    # preserves it and always leaves U+00A0 (NBSP) as a remnant at tag boundaries.
    body = body.replace('\u2028', '<br>')
    body = body.replace('\x0c', ' ')  # form feed occasionally appears in Notes text runs
    # Apple can emit malformed HTML entity fragments for quotes: "&quot" (no ';').
    # Normalise these early so downstream HTML->Markdown conversion yields '"'.
    body = re.sub(r'&quot(?!;)', '"', body, flags=re.IGNORECASE)
    body = re.sub(r'&lt(?!;)', '&lt;', body, flags=re.IGNORECASE)
    body = re.sub(r'&gt(?!;)', '&gt;', body, flags=re.IGNORECASE)
    body = re.sub(
        r'(</(?:b|i|u|em|strong)>)\s*\u00a0\s*(<(?:b|i|u|em|strong)[\s>])',
        r'\1<br>\n\2', body,
    )
    body = re.sub(r'\u00a0\s*(?=<br)', '', body)
    body = re.sub(r'(?<=<br>)\s*\u00a0', '', body)
    body = re.sub(r'(<div>)\s*\u00a0\s*', r'\1', body)
    body = re.sub(r'\s*\u00a0\s*(</div>)', r'\1', body)
    # Replace any remaining NBSP with regular space
    body = body.replace('\u00a0', ' ')

    # Collapse empty inline wrappers: <b><br></b> → <br>,  <b></b> → ""
    body = re.sub(
        r'<(b|i|u|em|strong)>\s*(?:<br\s*/?>)?\s*</\1>',
        lambda m: '<br>' if '<br' in m.group() else '',
        body,
    )

    # Strip trailing <br> before closing block tags (must precede div merge)
    body = re.sub(r'<br\s*/?>\s*(</(?:div|li|h[1-6]|p|td|th)>)', r'\1', body)

    # Merge adjacent identical tags that Apple fragments across characters
    # e.g. <h1>CO</h1><h1>SC</h1> → <h1>COSC</h1>
    # e.g. <b>part1</b><b>part2</b> → <b>part1part2</b>
    for tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'b', 'i', 'u', 'em', 'strong'):
        # Allow attributes on adjacent opening tags, e.g.
        # </h2><h2 class="Apple-..."> or </b><b style="...">
        body = re.sub(rf'</{tag}>\s*<{tag}(?:\s+[^>]*)?>', '', body)
    body = _merge_fragmented_headings(body)

    # Apple can wrap block headings in inline styling tags
    # (e.g. <b><h2>..</h2></b> or <font><h2>..</h2></font>), which creates
    # invalid nesting and prevents heading-fragment merging.
    # Repeat until stable because wrappers can be nested.
    while True:
        body, count = re.subn(
            r'<(b|i|u|em|strong|font|span)(?:\s+[^>]*)?>\s*(<(h[1-6])[^>]*>.*?</\3>)\s*</\1>',
            r'\2',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if count == 0:
            break

    # Convert URL/domain-only inline wrappers to links.
    # Apple Notes often drops <a> in body HTML while preserving visual styling tags.
    def _linkify_wrapped_url(match):
        text = match.group(2).strip()
        href = _href_from_text(text)
        return f'<a href="{href}">{text}</a>'

    body = re.sub(
        rf'<(b|i|u|em|strong)>\s*({url_text_re.pattern})\s*</\1>',
        _linkify_wrapped_url,
        body,
        flags=re.IGNORECASE,
    )

    # Linkify plain text URL/domain list items (Apple's own auto-linking case),
    # but only when it's likely an actual web host (conservative TLD check).
    def _linkify_container_url(match):
        open_tag = match.group(1)
        text = match.group(2).strip()
        maybe_br = match.group(3) or ''
        close_tag = match.group(4)
        if not _is_likely_web_url_or_domain(text):
            return match.group(0)
        href = _href_from_text(text)
        return f'{open_tag}<a href="{href}">{text}</a>{maybe_br}{close_tag}'

    body = re.sub(
        rf'(<(?:li|p|div|span)\b[^>]*>)\s*({url_text_re.pattern})\s*(<br\s*/?>)?\s*(</(?:li|p|div|span)>)',
        _linkify_container_url,
        body,
        flags=re.IGNORECASE,
    )

    def _linkify_container_inline_urls(match):
        open_tag = match.group(1)
        inner_html = match.group(2)
        close_tag = match.group(3)
        return f'{open_tag}{_linkify_inline_plaintext(inner_html)}{close_tag}'

    body = re.sub(
        r'(<(?:li|p|div|span)\b[^>]*>)(.*?)(</(?:li|p|div|span)>)',
        _linkify_container_inline_urls,
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove duplicate leading title (Apple Notes repeats it in the body)
    if title:
        title_pat = r'\s+'.join(re.escape(w) for w in title.split())
        body = re.sub(
            rf'^\s*(?:<div>\s*)?<h1>\s*{title_pat}\s*</h1>(?:\s*</div>)?\s*',
            '', body, count=1,
        )

    protected_blocks = {}

    def _protect_table_blocks(match):
        token = f"__APPLE_NOTES_TABLE_BLOCK_{len(protected_blocks)}__"
        table_html = match.group(1) if match.lastindex else match.group(0)
        protected_blocks[token] = table_html
        return token

    body = re.sub(
        r'<object>\s*(<table.*?</table>)\s*</object>',
        _protect_table_blocks,
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body = re.sub(
        r'<table.*?</table>',
        _protect_table_blocks,
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body = re.sub(
        r'<div>\s*(__APPLE_NOTES_TABLE_BLOCK_\d+__)\s*(?:<br\s*/?>)?\s*</div>',
        r'\1',
        body,
    )

    # Merge consecutive divs into single blocks with <br> line breaks.
    # In Apple Notes, each Enter creates a new <div>; only an empty <div><br></div>
    # (or now <div></div> after br-stripping) signals a true paragraph break.
    # Phase 1: mark empty paragraph-separator divs so merge won't bridge across them
    _pm = '\x00PARA\x00'
    body = re.sub(r'<div>\s*(?:<br\s*/?>)?\s*</div>', _pm, body)
    # Phase 2: merge all consecutive content divs
    body = re.sub(r'</div>\s*<div>', '<br>\n', body)
    # Phase 3: remove markers
    body = body.replace(_pm, '')

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

    # Unwrap divs that directly precede a list (keeps label tight with list)
    body = re.sub(r'<div>(.*?)</div>\n(<(?:ul|ol)[\s>])', r'\1\n\2', body, flags=re.DOTALL)
    body = re.sub(r'<div>\s*(__APPLE_NOTES_TABLE_BLOCK_\d+__)\s*</div>', r'\1', body)

    # Convert remaining content <div>s to semantic <p>s
    body = re.sub(r'<div>(.*?)</div>', r'<p>\1</p>', body, flags=re.DOTALL)

    # Remove empty paragraphs
    body = re.sub(r'<p>\s*</p>', '', body)

    # Post-div-merge fix: Apple can also split headings across <br> separators.
    body = _merge_fragmented_headings(body, allow_br=True)

    # Heading wrappers that were merged through <div> blocks can leave an
    # artificial line-break directly after the heading. That becomes an empty
    # hard-break line in Markdown, so drop it.
    body = re.sub(r'(</h[1-6]>)\s*<br\s*/?>', r'\1', body, flags=re.IGNORECASE)

    # Apple occasionally inserts an inline line separator in the middle of a
    # list-item sentence (for example before a lower-case continuation word).
    # Preserve structural breaks like nested lists, but collapse these mid-line
    # breaks back to spaces.
    def _collapse_midline_li_breaks(match):
        content = match.group(1)
        content = re.sub(
            r'(?<=\S)\s*<br\s*/?>\s*(?=[a-z\u03b1-\u03c9])',
            ' ',
            content,
            flags=re.IGNORECASE,
        )
        return f'<li>{content}</li>'

    body = re.sub(
        r'<li>(.*?)</li>',
        _collapse_midline_li_breaks,
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Collapse excessive blank lines
    body = re.sub(r'\n{3,}', '\n\n', body)

    for token, block_html in protected_blocks.items():
        body = body.replace(token, block_html)

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
def to_markdown(title, created, modified, folder, html_body, *,
                include_frontmatter=True, include_title=True,
                extract_images=False, obsidian_images=False, image_names=None):
    def _preserve_line_indentation(html_text):
        def _indent_to_nbsp(indent):
            pieces = []
            for ch in indent:
                if ch == '\t':
                    pieces.append('&nbsp;' * 4)
                elif ch == ' ':
                    pieces.append('&nbsp;')
            return ''.join(pieces)

        return re.sub(
            r'(<br\s*/?>\n?)([ \t]+)',
            lambda m: m.group(1) + _indent_to_nbsp(m.group(2)),
            html_text,
        )

    def _tokenize_inline_data_images(html_text):
        tokens = {}

        def _replace(match):
            token = f"__APPLE_NOTES_INLINE_IMAGE_{len(tokens)}__"
            tokens[token] = match.group(1)
            return f'src="{token}"'

        tokenized = re.sub(
            r'src="(data:image/[^"]+)"',
            _replace,
            html_text,
            flags=re.IGNORECASE,
        )
        return tokenized, tokens

    def _restore_inline_data_images(markdown_text, tokens):
        for token, data_uri in tokens.items():
            markdown_text = markdown_text.replace(f"]({token})", f"]({data_uri})")
        return markdown_text

    def _tokenize_tables(html_text):
        table_tokens = {}

        def _replace(match):
            token = f"__APPLE_NOTES_MD_TABLE_{len(table_tokens)}__"
            table_tokens[token] = match.group(0)
            return token

        tokenized = re.sub(
            r'<table.*?</table>',
            _replace,
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return tokenized, table_tokens

    def _restore_tables(markdown_text, table_tokens):
        for token, table_html in table_tokens.items():
            markdown_text = markdown_text.replace(token, f'\n\n{table_html}\n\n')
            markdown_text = markdown_text.replace(
                token.replace('_', r'\_'),
                f'\n\n{table_html}\n\n',
            )
        return markdown_text

    def _escape_non_html_angle_placeholders(markdown_text):
        html_tags = {
            "a", "abbr", "acronym", "address", "article", "aside", "audio",
            "b", "blockquote", "br", "button", "caption", "code", "col",
            "colgroup", "dd", "del", "details", "div", "dl", "dt", "em",
            "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
            "head", "header", "hr", "html", "i", "iframe", "img", "input",
            "label", "li", "main", "nav", "object", "ol", "p", "picture", "pre",
            "q", "section", "small", "source", "span", "strong", "sub", "sup",
            "summary", "table", "tbody", "td", "textarea", "tfoot", "th", "thead",
            "time", "title", "tr", "u", "ul", "video",
        }

        def _replace(match):
            inner = match.group(1)
            lower = inner.replace('\\', '').lower()
            if lower in html_tags:
                return match.group(0)
            return f'&lt;{inner}&gt;'

        return re.sub(
            r'<([A-Za-z][A-Za-z0-9_\\-]*)>',
            _replace,
            markdown_text,
        )

    image_list = []
    if HAS_MARKDOWNIFY:
        md_opts = dict(
            heading_style="ATX",
            bullets="-",
            strip=["script", "style"],
        )
        prepared_html = _preserve_line_indentation(html_body)
        inline_image_tokens = {}
        table_tokens = {}
        prepared_html, table_tokens = _tokenize_tables(prepared_html)
        if extract_images:
            md_opts.update(
                extract_images=True,
                obsidian_images=obsidian_images,
                note_title=title,
                image_list=image_list,
                image_names=image_names,
            )
        else:
            prepared_html, inline_image_tokens = _tokenize_inline_data_images(prepared_html)

        body_md = html_to_md(prepared_html, **md_opts)
        # markdownify inserts blank lines before lists; tighten when preceded by
        # a standalone label line (bold text, "Word:", "1)", etc.)
        # Only matches lines preceded by a blank line or start-of-string,
        # ensuring we don't tighten the last line of a multi-line paragraph.
        body_md = re.sub(r'(^|\n\n)([^\n]+)\n\n((?:\d+\.|[-*+]) )',
                         r'\1\2\n\3', body_md)
        # Tighten label->list when the label line ends with ":" (including bold labels).
        body_md = re.sub(r'([^\n]*:\*?\*?)\n\n((?:\d+\.|[-*+]) )',
                         r'\1\n\2', body_md)
        # markdownify also inserts blank lines after headings. Apple Notes renders
        # section headings tightly with the following content, so keep the source
        # markdown compact here as well.
        body_md = re.sub(r'(^|\n)(#{2,6} [^\n]+)\n\n(?=\S)', r'\1\2\n', body_md)
        body_md = re.sub(r'<(\[[^]]+\]\(mailto:[^)]+\))>', r'\1', body_md)
        body_md = _escape_non_html_angle_placeholders(body_md)
        if table_tokens:
            body_md = _restore_tables(body_md, table_tokens)
        if inline_image_tokens:
            body_md = _restore_inline_data_images(body_md, inline_image_tokens)
    else:
        # Simple fallback: strip all HTML tags
        body_md = re.sub(r"<[^>]+>", "", html_body)
        body_md = re.sub(r"\n{3,}", "\n\n", body_md).strip()

    parts = []
    if include_frontmatter:
        parts.append(
            f"---\n"
            f"title: \"{title}\"\n"
            f"folder: \"{folder}\"\n"
            f"created: \"{created}\"\n"
            f"modified: \"{modified}\"\n"
            f"---\n"
        )
    if include_title:
        parts.append(f"# {title}\n")
    parts.append(body_md.strip() + "\n")
    return "\n".join(parts), image_list


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
def write_note(note, out_dir, fmt, used_names, *,
               include_frontmatter=True, include_title=True,
               extract_images=False, obsidian_images=False, image_names=None):
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
        filepath.write_text(content, encoding="utf-8")
    else:
        content, images = to_markdown(
            note["title"], note["created"], note["modified"],
            note["folder"], note["html"],
            include_frontmatter=include_frontmatter,
            include_title=include_title,
            extract_images=extract_images,
            obsidian_images=obsidian_images,
            image_names=image_names,
        )
        filepath = folder_dir / f"{base}.md"
        filepath.write_text(content, encoding="utf-8")

        if images:
            attach_dir = folder_dir / "_attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                img_path = attach_dir / img['filename']
                img_path.write_bytes(base64.b64decode(img['data']))

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
    parser.add_argument(
        "--no-frontmatter", action="store_true",
        help="Omit YAML front matter from Markdown files",
    )
    parser.add_argument(
        "--no-title", action="store_true",
        help="Omit the '# Title' heading from Markdown files",
    )
    img_group = parser.add_mutually_exclusive_group()
    img_group.add_argument(
        "--extract-images", action="store_true",
        help="Extract images to _attachments/, use standard ![](path) links",
    )
    img_group.add_argument(
        "--obsidian-images", action="store_true",
        help="Extract images to _attachments/, use Obsidian ![[]] links",
    )
    parser.add_argument(
        "--timings", action="store_true",
        help="Print timing information for fetch, parse, and export stages",
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

    fetch_started = time.perf_counter()
    raw = fetch_notes_raw(args.folder)
    fetch_elapsed = time.perf_counter() - fetch_started

    parse_started = time.perf_counter()
    notes = parse_notes(raw)
    parse_elapsed = time.perf_counter() - parse_started

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
    image_names = set()
    do_extract = args.extract_images or args.obsidian_images
    counts = {fmt: 0 for fmt in formats}
    export_started = time.perf_counter()
    per_note_timings = []

    for i, note in enumerate(notes, 1):
        note_started = time.perf_counter()
        for fmt in formats:
            write_note(note, out_dir, fmt, used_names,
                       include_frontmatter=not args.no_frontmatter,
                       include_title=not args.no_title,
                       extract_images=do_extract,
                       obsidian_images=args.obsidian_images,
                       image_names=image_names)
            counts[fmt] += 1
        note_elapsed = time.perf_counter() - note_started
        if args.timings:
            per_note_timings.append((note_elapsed, note["folder"], note["title"]))
        # Progress every 25 notes
        if i % 25 == 0 or i == len(notes):
            print(f"   {i}/{len(notes)} notes processed…", end="\r")

    export_elapsed = time.perf_counter() - export_started

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
        "include_frontmatter": not args.no_frontmatter,
        "include_title": not args.no_title,
        "extract_images": args.extract_images,
        "obsidian_images": args.obsidian_images,
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

    if args.timings:
        total_elapsed = fetch_elapsed + parse_elapsed + export_elapsed
        print("\n⏱️  Timings")
        print(f"   Fetch Notes.app data: {fetch_elapsed:.2f}s")
        print(f"   Parse + clean note HTML: {parse_elapsed:.2f}s")
        print(f"   Render + write exported files: {export_elapsed:.2f}s")
        print(f"   Total measured time: {total_elapsed:.2f}s")

        if per_note_timings:
            print("   Slowest notes:")
            for elapsed, folder, title in sorted(per_note_timings, reverse=True)[:5]:
                print(f"   - {elapsed:.2f}s  [{folder}] {title}")


if __name__ == "__main__":
    main()
