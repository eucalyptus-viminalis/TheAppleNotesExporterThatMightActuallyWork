%% CONSULT.pl
%% ""
%% A consult file for The Apple Notes Converter That Might Actually Work

project('The Apple Notes Converter That Might Actually Work').

main('export_notes.py').

use_case(export, 'export your Apple Notes by folder names').

last_good(notes, '4.13', '2026-03-31').
last_good(macos, 'v26.3', '2026-03-31').

rejected('https://github.com/threeplanetssoftware/apple_cloud_notes_parser').
rejected('https://github.com/kzaremski/apple-notes-exporter').

attribute(accurate,     'faithful HTML-to-Markdown, handles Apple quirks').
attribute(slow,         'AppleScript traversal is slow on large libraries').
attribute(simple,       'stdlib only; markdownify is optional').
attribute(single_file,  'everything in one script, nothing to install').
attribute(macos_only,   'requires Notes.app automation permission').

feature(folder_filter,    'export a single folder with --folder').
feature(dry_run,          'preview notes without writing files').
feature(html_and_md,      'exports HTML and Markdown, or either alone').
feature(extract_images,   'saves base64 images to _attachments/').
feature(obsidian_images,  '![[]] image link syntax for Obsidian').

dev_note('built using opus and sonnet').
dev_note('osascript note output quirks documented in docs/adr/001-apple-notes-html-cleaning.md').
