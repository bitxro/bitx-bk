# NAS console redesign changelog

Frontend only. Base: `bitx-backup-console` @ `4a5795a`.

## Added
- Design system: tokens, components, light/dark/auto theme. See
  [NAS-DESIGN-SYSTEM.md](NAS-DESIGN-SYSTEM.md).
- `static/theme.js`: applies the saved theme before first paint.
- `static/console.js`: theme switch, mobile drawer, local time formatting
  for `<time>`, `data-confirm` on destructive buttons, Activity filter.
- Inline SVG icon sprite in `console.html`.
- `tests/console-preview.py`: offline renderer with **simulated** fixtures,
  a mock File Manager API, a preview server and a form-contract check.
  `tests/console-forms.json` is the contract captured from the original
  templates.

## Changed
- **Shell**: sidebar with grouped nav and icons, and an account block with
  logout. Below 900px it becomes a top bar with a drawer; a static list is
  the no-JS fallback.
- **Login**: segmented role choice (radio `role`, same values), HTTPS and
  session hints.
- **Dashboard**: stat tiles (free space with meter, services healthy count,
  users, shares, associations), a services card with pills, and a merged
  snapshot list.
- **Storage**: capacity card with percentage and 85/95% thresholds, a
  device card with a SMART pill, and an exFAT note.
- **Users**: file users as disclosure cards with avatar, protocol tags and
  state pill. The edit form is grouped (Access switches, Credentials) and
  deletion moved into a danger zone. Admin and legacy tables become stacked
  cards on mobile, and "Add web admin" moved into the admin card footer.
- **Shares**: unified shares first (size, protocols, grant count), then
  system shares, then legacy SMB. Permission rows sit in a two-column
  matrix, and delete is in a danger zone.
- **Repositories**: status (Restic / Not initialised / Error) and last
  snapshot are visible without expanding. Details are a key/value list, and
  remove has a confirm.
- **Activity**: result pills plus a client-side text and result filter.
- **Settings**: theme control, HTTPS pill, listeners in a card. Fixes the
  375px overflow.
- **File Manager admin**: explanation notice and cards for web access and
  Backup Explorer grants, with empty states.
- **File Manager / Backup Explorer**:
  - Live vs snapshot context banner (blue *Live* + access, purple
    *Snapshot · Read-only · date*). `data-mode` tints the selection and
    current items.
  - Native dialogs replace all `prompt()`/`confirm()`. Destination is a
    select of writable roots. Delete requires typing the exact name (the
    button stays disabled until it matches). Restore shows the source
    snapshot and the no-overwrite guarantee.
  - List view with Name/Size/Modified columns, type icons, a ⋮ menu (also
    on right-click), select-all and a selection bar. Grid view is a real
    tile grid, and the choice is remembered per browser.
  - Human-readable sizes (KiB…TiB) and localised dates. Snapshot picker
    shows date · host · short id plus a count.
  - Drag-and-drop upload and upload progress text.
  - Restore jobs list with status pills and a running indicator.
  - Loading skeleton, contextual empty states, pager hidden when unused,
    sort hidden in Backup Explorer.
  - On mobile, Locations collapses into a summary that shows the current
    location.
- `install.sh`: also installs `static/console.js` and `static/theme.js`.

## Unchanged (verified)
- Routes, form actions, field names, button values, CSRF fields and the
  header (`tests/console-preview.py check`: 0 contract differences).
- File API actions and payloads, download/upload URLs (checked by a
  Playwright run against the mock API: mkdir, delete+confirm, move,
  restore, archive download, diff).
- `app.py`, `broker.py`, `nas.py`, `file_gateway.py`: not modified.

## Validation (simulated, not a live VM)
- 40 template renders (8 admin pages + File Manager files role + login ×
  normal/empty/error/offline). No errors, CSRF present on every POST form.
- Screenshots at 375/768/1366/1920 in light and dark: no horizontal
  overflow, no console errors.
- 24 interaction checks (dialogs, payloads, navigation, read-only share,
  snapshot banner, selection, diff, theme persistence, Activity filter,
  mobile drawer, no-JS nav).
- Existing `tests/*.sh` and `tests/*.py` still pass.
- **Not tested**: real Restic/Samba/SFTP, real uploads over 8 GiB, Safari,
  and screen readers beyond semantic review.

## Notes for Codex (backend, optional)
1. `jobs` returns `restored` bytes but no total, so the UI can only show an
   indeterminate bar. A `total` field (or bytes estimated at queue time)
   would enable a real progress percentage.
2. `tree` returns no total count (only `more`), so Backup Explorer shows
   "N+ elements".
3. `snapshots` could expose `tags` and `paths` (the CLI already reads
   them) for richer snapshot labels.
4. The `size` filter is GiB-only. Templates now use a local `bytes()`
   macro for display. A shared filter in `app.py` would be cleaner.
5. `PAGES` names are English route labels in a Romanian UI. Translating the
   labels needs a display-name map separate from the route slug.
6. Activity is capped at 100 rows with no server-side filter or paging. The
   new filter is client-side only.
7. The `<select name="role">` on login is now radios with the same values.
   No backend change needed; noted for awareness.
