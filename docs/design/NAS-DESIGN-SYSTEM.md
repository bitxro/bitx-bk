# NAS console design system

One stylesheet (`backup-server/static/console.css`) and two small scripts.
No framework, no build step, no external assets. The CSP is
`style-src 'self'; script-src 'self'`, so there are **no inline styles or
scripts**. Icons are an inline SVG sprite in `console.html` referenced with
`<use href="#i-name">`.

## Principles

1. Calm, dense, legible: the information level of a NAS dashboard without
   decoration.
2. One accent (blue) for actions and live data. Purple is reserved for
   **snapshot / backup history**, so read-only historical data is never
   mistaken for live files.
3. Status always has shape plus text, never colour alone (pills with a dot
   and a label).
4. Progressive enhancement: every form posts without JS. JS adds the theme
   switch, the drawer, local time formatting, confirmations, filtering and
   the File Manager.
5. 375–1920 px with no horizontal page scroll. Touch targets are ≥40px
   below 600px.

## Tokens (`:root`)

| Group | Tokens |
|-------|--------|
| Surfaces | `--bg` `--surface` `--surface-2` `--surface-sunken` `--overlay` |
| Text | `--text` `--text-2` (secondary) `--text-3` (tertiary/hints) |
| Lines | `--border` `--border-strong` |
| Accent | `--accent` `--accent-hover` `--accent-soft` `--accent-text` |
| States | `--ok` `--warn` `--danger` `--info`, each with a `-soft` background |
| Live vs history | `--live` (= accent), `--snapshot`, `--snapshot-soft` |
| Shape | `--radius-s` 6, `--radius` 10, `--radius-l` 14; `--shadow-1..3` |
| Space | `--s1`…`--s10` on a 4px base (4, 8, 12, 16, 20, 24, 32, 40) |
| Type | `--font`, `--mono`, `--fs-xs` 12 … `--fs-2xl` 26; base 15px |
| Layout | `--sidebar` 248, `--topbar` 56, `--content-max` 1320 |

Dark values are defined twice: under
`@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }`
and under `:root[data-theme="dark"]`. With no saved preference the console
follows the OS. `theme.js` (loaded blocking in `<head>`, so there is no
flash of the wrong theme) applies a saved `light`/`dark` from
`localStorage["bitx-theme"]`. The preference is per browser and never sent
to the server.

## Components

| Component | Classes | Notes |
|-----------|---------|-------|
| Shell | `.sidebar` `.nav` `.nav-label` `.account` `.topbar` `.main` `.content` `.scrim` | Fixed sidebar above 900px. Below that, a drawer opened by `#nav-toggle`; Escape or the scrim closes it. Without JS (`html.no-js`) the nav is a static wrapped list. |
| Page header | `.page-head` | Title, one-line lead and actions (theme switch). |
| Card | `.card` `.card-head` `.card-body` `.card-foot` · `.panel` | `.panel` is a padded surface. Inside `.item-body` it loses its chrome. |
| Stat tile | `.stat` `.stat-label` `.stat-value` `.stat-foot` `.stat-wide` | Inside `.grid-stats`. |
| Meter | `.meter` (`.high` ≥85%, `.full` ≥95%) `.meter-legend` | A styled `<progress>`: CSP-safe, no width styles. |
| Pill | `.pill` + `ok` `warn` `danger` `info` `snapshot` `plain` | State. |
| Tag | `.tag` `.tag.on` | Protocol or attribute (SMB, SFTP, Restic). |
| Notice | `.notice` + `ok` `error` `warn` `info` | Flash messages keep their `ok`/`error` category class. |
| Buttons | `button` (primary), `.secondary`, `.danger`, `.ghost`, `.icon-btn`, `.small` | `data-confirm="…"` adds a confirm before submitting. |
| Forms | `label` (stacked), `.form-cols`, `fieldset`/`legend`, `.hint`, `.check` | |
| Switches | `.toggles .check input[type=checkbox]` | Still a real checkbox, so the posted value is unchanged. |
| Permissions | `.perm-list` (label + select rows), `.perm-row`, `.perm-group` + `.radio-row` | |
| Segmented | `.segmented` | Radio-based (login role) or `aria-pressed` buttons (File Manager mode). |
| Disclosure | `details.item` > `summary` (`.thumb` `.title` `.meta` `.chev`) + `.item-body` | Used for users, shares and repositories. `.meta.keep` stays visible on mobile. |
| Danger zone | `.danger-zone` | Every destructive form lives in one, separate from save. |
| Key/value | `dl.kv` | Collapses to one column below 480px. |
| Tables | `.table-scroll` + `table.stack-table` with `td[data-label]` | Become label/value cards below 720px. |
| Empty state | `.empty` | Icon, bold title, hint. |
| Dialog | `dialog` + `.dialog-head` `.dialog-body` `.dialog-foot` | Native `<dialog>`. |

## File Manager anatomy

```
.fm[data-mode=files|backup]
├─ .fm-modes: segmented #fm-files / #fm-backups · #fm-actions (Upload, New folder, Compare)
└─ .fm-layout
   ├─ details.fm-sidebar > #fm-roots (locations or repositories, aria-current)
   └─ .fm-main
      ├─ #fm-context   .live (Live · access) | .snapshot (Snapshot · Read-only · date)
      ├─ .fm-bar       back/forward · #fm-breadcrumb · #fm-view
      ├─ #fm-snapbar   snapshot picker + count (backup only)
      ├─ .fm-bar       #fm-filter (search, sort only in files mode)
      ├─ #fm-selection count + bulk actions
      ├─ #fm-message   notice (error/info/ok)
      ├─ #fm-entries   .fm-head + .fm-entry rows | .fm-grid tiles · drag-and-drop target
      ├─ .fm-foot      count · pager
      └─ #fm-jobs      restore queue with status pills
```

File-type icons (`.ficon.dir/img/doc/arc/file`) come from the extension.
`ask()` in `files.js` builds every modal form (mkdir, rename, move, copy,
delete with exact-name match, restore, compare). It replaces all
`prompt()`/`confirm()` calls.

## Accessibility

- Skip link, labelled `nav`, `aria-current="page"`, and `aria-pressed` on
  toggles.
- `:focus-visible` uses a 3px accent ring on all interactive elements.
- Native `<dialog>` handles focus trapping and Escape.
- Live regions: `#fm-message` and `#fm-jobs` (`aria-live="polite"`) and the
  Activity filter count.
- Contrast: body and secondary text meet WCAG AA on both themes. Pills use
  the darker state colour on a soft tint.
- `prefers-reduced-motion` disables transitions and the loading shimmer.

## Adding a page

Wrap content in `.card`/`.section`, use pills for state and `.item` for
editable records. Put destructive actions in `.danger-zone` with
`data-confirm`. Add `data-label` to table cells. Then run
`python3 tests/console-preview.py check`, and if forms changed on purpose,
regenerate the contract with
`python3 tests/console-preview.py contract > tests/console-forms.json`.
