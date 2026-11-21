# NAS console UI audit

Scope: the HTTPS console in `backup-server/` (`templates/console.html`,
`templates/nas.html`, `templates/files.html`, `static/console.css`,
`static/files.js`) as of commit `4a5795a` on `bitx-backup-console`.
Backend files (`app.py`, `broker.py`, `nas.py`, `file_gateway.py`) were read
only to learn data shapes. They were not changed.

Method: every page was rendered from the real templates with simulated
fixture data (`tests/console-preview.py`) at 375, 768, 1366 and 1920 px, in
light and dark mode. The File Manager ran against a simulated JSON API.
**None of this is a live VM test.** Restic, Samba, SFTP, the broker and the
file gateway were not running.

Severity: **H** blocks or endangers a task, **M** slows it down or confuses
users, **L** is polish.

## Global

| # | Finding | Sev |
|---|---------|-----|
| G1 | No design tokens beyond 10 colours. Spacing, radii, type sizes and shadows were hard-coded per rule, so components drifted (7px, 8px, 12px, 18px radii). | M |
| G2 | Dark mode only followed the OS. There was no manual override, and Settings showed a static "Urmează sistemul" with no control. | M |
| G3 | Mobile nav (≤760px) became a wrapped row of 8 links above the content, about 200px tall before any content. There was no drawer and no current-page title. | M |
| G4 | Settings overflowed horizontally at 375px: the `ss` output `<pre>` was 385px wide. | M |
| G5 | Headings were oversized (`h1` up to 3.2rem) with little hierarchy below them. Sections were plain `h2` lines with no grouping surfaces. | L |
| G6 | Status used plain coloured text (`active`/`inactive`, `Activ`) with no pill or shape cue. Colour was the only signal. | M |
| G7 | Raw ISO timestamps (`2026-11-20T02:41:40.123456789Z`) and byte counts (`100,931,731,456 B`) were shown to users. | M |
| G8 | Destructive submit buttons (delete account, delete share, remove association) submitted with no client-side confirmation. The server still validates, but there was no "are you sure". | M |
| G9 | No skip link and no visible landmarks besides `aside`/`main`. Focus ring existed. | L |
| G10 | Empty states were bare sentences or missing (no shares, no users, no repositories). | L |
| G11 | Page names (Dashboard, Storage…) are English while all content is Romanian. Kept: they are the route names in `app.py`'s `PAGES` (see backend notes). | L |

## Pages

**Login.** A centred card. The role was a `<select>` below the username, so
the role choice was easy to miss. The error notice styling was weak.

**Dashboard.** Three disconnected blocks (volume, services, NAS counters)
and a bulleted list of snapshots. There was no at-a-glance health verdict,
and the counters had no context (how many active? how many with a known
snapshot?).

**Storage.** Three numbers plus a thin `progress`. There was no percentage,
no warning threshold and no SMART emphasis. The device details were a plain
`dl`.

**Users.** File users were collapsed rows whose summary text was all link
coloured. The edit form was long and single-column, and the protocol toggles
were plain checkboxes. Account deletion sat directly under the save button
with no visual separation (H-adjacent: easy to mis-click into a data-deleting
form). The admin and legacy tables used `<details>` links that wrapped
awkwardly. At 375px the tables needed horizontal scrolling.

**Shares.** System shares, unified shares and legacy SMB shares were three
undifferentiated lists. Permission selects were stacked label-above-select,
which made long user lists hard to scan. Delete sat inline with save.

**Repositories.** Each association was a collapsed row. Its status
(initialised, error, last snapshot) was only visible after expanding.
"Remove association" looked like the other buttons.

**Activity.** A table only, with no filtering. `denied` results were not
highlighted.

**Settings.** Static text, no theme control, and the `<pre>` overflow (G4).

**File Manager (admin).** Two plain forms. No explanation that admins do
not get file access, or that a share grant does not confer decryption.

**File Manager (files user), priority.**
- F1 (H): every mutation used `window.prompt()`: mkdir, rename, move, copy,
  delete and restore. Copy/move asked for the destination root by typing an
  internal id (`share:proiecte`). Restore used three chained prompts and a
  `confirm()`. Snapshot compare asked for a typed index from a numbered list.
- F2 (H): live files and snapshot contents looked identical. The only cue
  was which top button was blue. Nothing said "read-only" or "this is a copy
  from date X".
- F3 (M): roots showed raw ids, and repositories showed raw byte counts and
  ISO dates in their button labels.
- F4 (M): entries had no type icons, no size or date columns, and
  directories were prefixed with "▸". Grid view was the same card with
  different wrapping.
- F5 (M): per-entry actions were in a `<details>` "Acțiuni" link with
  right-aligned buttons. The context menu opened it in place.
- F6 (M): the restore job list was plain text (`target · message · bytes B`)
  with no status colour.
- F7 (L): no select-all, no selection count, no drag-and-drop upload, no
  upload progress, and the pager buttons were always visible.
- F8 (L): the sort select was shown in Backup Explorer, where the API
  ignores it.

## What was preserved

The routes, form `action`s, field names, button `name=value` pairs, CSRF
fields and `X-CSRF-Token` header are unchanged. So are the file API actions
and payloads, the download/upload URLs, auth/session behaviour, the CSP
compatibility (no inline script or style) and the progressive enhancement
(forms work without JS). Verified by `tests/console-preview.py check`
against `tests/console-forms.json`, a contract captured from the original
templates before any change.
