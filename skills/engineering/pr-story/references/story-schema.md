# story.json

This is the one file you write. `build.py` checks it against `collected.json` and fills in
excerpts, links, roles and coverage.

Prose fields (`intent`, `summary`, `notes`, `text`, `steps`, check `text` and `why`) accept a
little markup: `` `code` ``, `**bold**`, and `[label](https://…)`. Everything else is escaped.

```jsonc
{
  "intent": "Two or three sentences: what someone using the app saw or will see, and the mechanism.",
  "chapters": [ /* Chapter, in reading order */ ],
  "nodes":    [ /* Node */ ],
  "edges":    [ /* Edge */ ],
  "groups":   [ /* Group, optional */ ],
  "checks":   [ /* Check, optional */ ],
  "stats":    "optional override of the auto-generated '3 text files · +5 −4 · 42 snapshot images'",
  "ticketURL":"optional override; defaults to $PR_STORY_TICKET_URL with {key} replaced by the key found in the title, body or branch; no link when unset"
}
```

## Chapter

```jsonc
{ "id": "fix", "title": "One color from the rounded top down",
  "summary": "One to three sentences shown under the title when the chapter is open.",
  "nodes": ["dep-view", "dep-collection", "layers"] }   // boxes lit and fitted to screen in this chapter
```

A node can appear in several chapters. That's how a visual or a key piece of code carries
across steps. Each box shows small pills with the numbers of the chapters it belongs to.

## Node

Every node has `id`, `kind`, `title`, `col` and `row`, all required. These are optional:

- `alignWith`, the id of a box placed before this one, meaning in an earlier column or
  earlier in this column. This box starts no higher than that box's top.
- `after`, the same, but this box starts below that box.
- `w`, the width in px. The default depends on the kind: code 540, journey 340, snaps 560,
  html 620, note 380, glue 420. A code box always widens to fit its longest line, up to 150
  characters. Longer lines wrap under their own indentation, so for code, `w` only sets a
  minimum.
- `role`, which colors the left edge: `changed` orange, `context` grey, `test` purple, `ui`
  blue. The build works it out for code nodes.
- `notes`, a list of one-sentence strings shown above the body.

Placement: `col` picks the column, counting from 0. `row` orders boxes within that column, top
to bottom. Each column stacks on its own, so rows don't line up across columns unless you
use `alignWith`. A column is as wide as its widest box, and the gaps leave room for arrows.

### `journey`: what someone using the app does

```jsonc
{ "id": "journey", "kind": "journey", "title": "Someone posting as a page", "col": 0, "row": 0,
  "steps": ["Feed", "Tap +", "Add post", "Tap “Public Feed” under your name", "The audience sheet opens"] }
```

### `code`: an excerpt of the real diff, or of unchanged code at the head commit

```jsonc
{ "id": "dep-view", "kind": "code", "title": "Picker background", "col": 1, "row": 0,
  "file": "Classes/General/Controllers/Pickers/DisplayEntityPicker.swift",
  "lines": "36-41",                 // new-side numbers; "12-14,30-33" for several ranges, shown with ⋯ between
  "notes": ["Optional one-liner the code can't say."],
  "same": [                         // optional: the same edit repeated elsewhere, folded under the excerpt
    { "file": "Other.swift", "lines": "20-22" }
  ] }
```

- For a file the PR changes, the range covers new-side lines. Any deletions inside the range,
  or right at its edges, come along with it.
- For a file the PR deletes, the range covers old-side lines.
- For a file the PR doesn't touch, the lines come from the head commit. The box is grey and
  labelled "Context · unchanged".
- The build sets `role` (`test` for files under a `*Tests/` directory), `link` and `line`. A
  changed excerpt links to the line in the PR's Files tab, so the reviewer can comment there.
  Context links to the file at the head commit.

### `snaps`: before and after reference images

```jsonc
{ "id": "snaps", "kind": "snaps", "title": "Cell with subtitle, avatar and selection · 375×812", "col": 3, "row": 1, "alignWith": "test-sut",
  "pick": ["RadioButtonCellTests/testSnapshot_withSubtitleAndAvatarSelected.375x812"] }
```

Images show at their point size, pixels ÷ `scale` (default 3, capped at 430pt wide), two to
a row, and the box is sized to fit them. Set `"scale": 2` for @2x references.

Each `pick` entry is a substring of the snapshot paths. Every changed PNG that matches becomes
a before/after pair, labelled by what's left of the filename (`light`, `dark`, …), with light
first. A new snapshot has no before, so its light and dark versions sit side by side
instead; a removed one does the same with its before images. Clicking an image opens a compare slider.

### `html`: a custom visual

```jsonc
{ "id": "layers", "kind": "html", "title": "What paints what, inside the sheet", "col": 2, "row": 0,
  "notes": ["Schematic drawn from the token values, not a screenshot."],
  "html": "<style>#node-layers .x { … }</style><div class=\"x\">…</div>",
  "script": "/* runs once with `el` (the node element) and `story` in scope */" }
```

- Scope all CSS under `#node-<id>`.
- Put `data-interactive` on any controls, so dragging them doesn't pan the canvas.
- Use the page's CSS variables (`--ink`, `--muted`, `--line`, `--panel`, `--code-bg`,
  `--changed`, `--ui`) so the visual follows light and dark.
- Say in `notes` that it is a schematic. Draw only what you've verified.

### `note`: free text

```jsonc
{ "id": "pattern", "kind": "note", "title": "Twelve screens, one edit", "col": 2, "row": 1,
  "text": "Each screen gains an `appearance` parameter and forwards it." }
```

### `glue`: files you only need to know exist

```jsonc
{ "id": "glue", "kind": "glue", "title": "Supporting files", "col": 3, "row": 0,
  "files": [".changelog/*", "*/__Snapshots__/*", "*.pbxproj", "*/Package.resolved"] }
```

`files` takes `fnmatch` globs against repo paths, and `*` matches across `/`. Matched files
skip the coverage check. The node lists them with their `+/-` counts and folds snapshot PNGs
into one count per test directory.

## Edge

```jsonc
{ "from": "presenter", "to": "dep-view", "label": "presents" }
```

Each edge is drawn from the nearest sides of the two boxes. When several edges meet the same
side, they spread out so they don't stack. An edge lights up in a chapter only when both of
its ends are in that chapter.

## Group

```jsonc
{ "title": "Tests", "nodes": ["vm-tests", "view-tests"], "color": "rose",
  "summary": "One or two sentences on what these boxes do together.",
  "collapsed": true }
```

A group is a tinted region drawn around its nodes' bounding box. Its header holds:
- The title.
- A Review tick that marks every box inside reviewed.
- A Collapse/Expand toggle.
- The `summary` underneath.

`summary` is optional. Without one, the group borrows the summary of the chapter that holds
exactly its boxes, or else the smallest chapter that holds them all.

`collapsed: true` starts the group folded into one stub box listing its boxes' titles. Arrows
into the group point at the stub. A group whose boxes are all test code or glue starts collapsed
without being told; set `collapsed: false` to keep it open. Opening a chapter that includes a
collapsed group's boxes expands it for that visit. A Collapse or Expand click is remembered in
the reviewer's browser.

`color` is optional: blue, violet, teal, amber, rose, green or slate. Without it, groups take
the colors in that order. Lay the boxes out so each frame holds only its own nodes;
`storyLint()` reports any frame that doesn't.

## Check

```jsonc
{ "status": "verified", "text": "`RadioButtonCell` is only used by this picker",
  "why": "Only `RadioButtonItemSectionController` dequeues it, and only `DisplayEntityPicker` creates that." }
```

`status` is `verified` or `open`; `open` shows as "your call". A check is `verified` only if
you read the evidence, and `why` says what it is.

## What the build checks

The build exits non-zero when any of these fails:

- Ids are unique, and every node has a valid kind and a `col`/`row`.
- `alignWith` and `after` point at a box placed earlier.
- A `html` node with a `<style>` scopes it under its own `#node-<id>` and targets no other
  node.
- Every chapter, edge and group points at real nodes.
- Every code range resolves to real lines.
- Every snapshot `pick` matches at least one changed image.
- Every added or deleted line in every text file is in some code node or its `same` list,
  unless its file is claimed by glue. Blank lines don't count. `--allow-uncovered` renders anyway and lists the missing
  spans in a warning box.

It only warns about:

- A node that's in no chapter.
- An excerpt over 40 lines.
- A binary file that's in no snaps or glue node.
