---
name: pr-story
description: >-
  Turn a pull request (or a local branch) into a local, interactive "story" page for human
  review — an infinite canvas of the PR's real diff excerpts as boxes, arrows for how they
  connect, chapters that walk the change in reading order, before/after snapshot-test images,
  and a list of the PR's claims checked against the code. Use when someone asks to see, explain,
  visualise, map out or walk through a PR "as a story", "on a canvas", "visually", "like a
  board", or asks for a story page, visual review or guided tour of a PR or branch. Never posts
  on its own: the page only adds the reviewer's own comments to their pending GitHub review,
  which nobody else sees until they submit it. Not for finding bugs or giving a verdict — that's a review skill's
  job.
---

# PR story

You turn a PR into a page a reviewer reads instead of scrolling a diff: the change told in
chapters, with each chapter's code on a canvas as boxes and arrows.

You write **only the story** — a `story.json` that says which lines to show, in what order,
connected how, with a sentence or two of narration. Scripts pull the code from git, so excerpts
are always the real diff, and a validator fails the build if a changed line is missing from the
story. Don't type code into the story.

Everything stays local, in `~/.cache/pr-story/<PR>/`. You never post, comment, approve or push.
When the person writes a comment on the page, it goes straight into their **pending** review on
GitHub. The first comment starts that review, nobody else sees it until they submit it there,
and they can edit or delete their pending comments from the page. Never add, edit or delete a
comment yourself, not even to test. Test with `serve.py --dry-run`, which keeps a fake pending
review in memory instead.

## Files

- `scripts/collect.py`: fetches the PR's metadata, a line-numbered diff of every file, and the
  before/after versions of changed snapshot PNGs.
- `scripts/build.py`: checks `story.json` against the diff, pulls in the excerpts and writes
  `index.html`. It embeds every excerpted file in full, so a reader can expand any excerpt 20
  lines at a time, like GitHub's expand arrows.
- `scripts/serve.py`: serves `~/.cache/pr-story` with caching off. It also runs a small local
  API the page uses to show the PR's review comments and to add, edit and delete comments in
  the reader's pending GitHub review.
- `scripts/github.py`: the `gh` calls the other two scripts share.
- `assets/template.html`: the page itself. You don't edit it per PR.
- `references/story-schema.md`: every field of `story.json`. **Read it before writing one.**

`<skill>` below means this skill's directory.

## 1. Collect

From the repo checkout:

```bash
python3 -I <skill>/scripts/collect.py --pr <N> --out ~/.cache/pr-story/<N>
```

For a branch with no PR yet: `--base main --head HEAD --out ~/.cache/pr-story/<branch>`.

It prints the files with their `+/-` counts, the snapshot directories, the number of review
comments and the PR body. On a stacked PR the base is the branch of the PR below it, so the
diff is only this layer. The full
diff is in `collected.json`. Don't read that file into context. Read the diff with git, per
file: `git diff <base> <head> -- <path>`, using the `base` and `head` it printed. Run
`git diff -U0` first to see each hunk's new-side line numbers.

## 2. Understand the change

Read, in this order, and stop once you can tell the story:

1. The PR body that collect printed, and the ticket if it names one
   (with whatever ticket CLI or tracker access you have).
2. The diff of every non-glue file. Glue is the changelog, regenerated files, snapshot
   PNGs, lockfiles and project files.
3. Just enough unchanged code to answer two questions:
   - What does someone using the app do to reach this code? Find the screen, the tap or the
     trigger, and the caller that leads in.
   - What does the change depend on? For example, the default it now matches, the type it
     conforms to, or the one other place that uses it.

Read callers with `git show <head>:<path>`, or `git grep -n <symbol> <head>`.

Check the PR's claims while you read. A claim is anything the description asserts, or anything
a reviewer would stop and wonder about ("only used here", "the sheet is primary", "no
behaviour change for X"). Mark a claim `verified` only after you've read the code or the pixels
that prove it, and say where in `why`. Everything else is `open` ("your call"). Don't
manufacture claims, and don't give a verdict or a list of findings.

## 3. Plan the story

The story is the order a thoughtful author would explain the change in, not the order of the
files.

1. **Where you meet it.** What someone using the app does, as a `journey` node, plus the
   unchanged code that leads into the change. Skip this for changes nobody using the app can
   reach, like build tooling or a pure refactor. Start with the core change instead.
2. **The core change**, the one edit everything else follows from.
3. **Consequences.** What had to change because of it: callers, a type that gains a case, a
   value that now flows somewhere new.
4. **Proof.** The tests and snapshots that pin it down.
5. **Glue.** Everything you only need to know exists.

Fit the number of chapters to the PR:
- A one-file fix has two or three.
- A feature has five to seven.
- Each chapter shows one to five boxes.

When several files repeat the same edit, show the clearest one as a code node. Put the rest
in its `same` list. They count toward coverage and appear folded under "The same edit in N
more places", and the node's notes name the pattern. That turns twenty files into one box.

Choose a **visual** (`html` node) only when a picture shows the mechanism better than code
does. Examples: which layer paints which color, a state machine, a before/after layout, a
timeline of async events, a data shape changing. Draw it from facts you've read, label it as
a schematic, and scope its CSS under `#node-<id>`. Most PRs need none or one.

Pick **snapshots** that show the change. Take one to three tests, usually one is enough. When a
test has several device sizes, pick the iPhone one (`375x812`, `393x852`) over iPad sizes. Light
and dark pairs are paired up automatically. Images show at their point size, pixels ÷ 3, so an
iPhone screenshot is as wide as on a phone. A pick is a substring,
so end it where the name ends (`testFoo_bothCounts.`) or it also matches
`testFoo_bothCounts_accessibility3`. Leave the rest to the glue node.

Use **groups** for clusters of code that belong together, like a type and its helpers, one
screen's view and view model, or the tests. They're drawn as colored regions, so a reviewer
can tell from the overview which boxes go together. A group can match a chapter, but it
doesn't have to.

Give each group a one- or two-sentence `summary`. It shows at the top of the region, so a
reviewer reads it without looking at the panel. A group of only tests or glue starts folded
into a stub and opens when the reviewer wants it. Set `collapsed` only to override that.

## 4. Write story.json

Write it to `~/.cache/pr-story/<N>/story.json`, following `references/story-schema.md`.

- **Code nodes** reference lines, never content: `"file": "...", "lines": "36-41"` or
  `"lines": "12-14,30-33"`. Use new-side line numbers. Deletions at the edges of a range or
  inside it come along automatically. Keep each excerpt under about 25 lines, and use several
  ranges to skip boilerplate.
- **Titles** say what the box shows ("Picker background"), not where it lives
  ("DisplayEntityPicker.swift").
- **Narration** is plain and short. `intent` is two or three sentences, starting with what
  someone using the app saw or will see. A chapter `summary` is one to three sentences. A
  node's `notes` hold one sentence each, and only what the code can't say. Backticks become
  code.
- **Edges** follow the story: calls, presents, wraps, renders, feeds, emits. Labels are two to
  four words. Don't connect everything to everything, and skip edges that jump back across
  chapters, from a test to the code it covers for example. They're very long and never light
  up, because their ends are in different chapters.
- **Layout**: `col` picks a column, and `row` orders boxes inside it. Columns stack
  independently, so a tall box never pushes another column down. Use `alignWith: "<id>"` to
  start a box level with one placed before it, so an edge between them runs straight. Use
  `after: "<id>"` to start it below one. Reading order runs left to right, and each chapter
  gets its own band of columns. Keep a chapter's boxes close together, and make sure each
  `group` frame holds only its own boxes.

## 5. Build, then look

```bash
python3 -I <skill>/scripts/build.py --dir ~/.cache/pr-story/<N>
```

Fix every error, then rebuild. "Changed lines no node shows" means a line isn't in the story yet.
Add a node, widen a range, or claim the file as glue. Deleted lines have no new-side number,
so the message names the new line a range should touch to include them. Use
`--allow-uncovered` only when the person asks for it. It renders the page with the missing
lines listed in a warning box on the canvas.

Then serve the page and look at it. If nothing is listening on port 8765 yet, start the server
in the background:

```bash
python3 -I <skill>/scripts/serve.py
```

Open `http://127.0.0.1:8765/<N>/index.html` in a new tab in the browser pane. The page has two
helpers for you in its JS console. Both wait until the page is ready, so `await` them.

- `await storyLint()` lists layout problems: boxes overlapping, a frame covering another
  group's box, a chapter spread thin across its view, an edge label sitting on a box, or a
  very long edge. Fix each one in `story.json`, rebuild, reload, and run it again until it
  says `["clean"]`.
- `await storyShow(3)` jumps to chapter 3 with no animation or fade, and `storyShow(0)` jumps
  to the overview. Chapters count from 1. Take a screenshot after each call, and read every chapter
  once as the reviewer would. Look for what lint can't catch: a chapter that doesn't make
  sense on its own, or a box whose title doesn't match its code.

`#3` at the end of the URL opens chapter 3, which works for sharing a chapter too.

Opened through `serve.py`, the page also shows the PR's review comments on their lines. Anyone
can click a line number in the PR's diff, or drag across several, to add a comment to their
pending review. Opened as a plain file, comments are saved as drafts in the browser instead. Comments on lines that
later commits changed are listed in the panel, because they no longer have a line to sit
on.

## 6. Hand over

Give the link and one line on what the story covers. Nothing else. The page stays on this
machine. Don't publish it as an Artifact or anywhere else unless the person asks, because it
contains the PR's code. To clean up afterwards, delete `~/.cache/pr-story/<N>/`.
