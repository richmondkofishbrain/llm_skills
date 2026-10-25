#!/usr/bin/env python3
"""Collect everything a PR story page needs: metadata, a line-numbered diff of every
changed file, and the before/after versions of every changed snapshot image.

Run from inside the repository checkout:

    collect.py --pr 15442 --out ~/.cache/pr-story/15442
    collect.py --base main --head HEAD --out ~/.cache/pr-story/my-branch

Writes <out>/collected.json and <out>/img/. Prints a compact summary for the model to read;
the full diff stays in collected.json and never needs to enter the model's context whole.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github  # noqa: E402

SNAPSHOT_RE = re.compile(r"/__Snapshots__/.+\.png$")


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True).stdout


def git_bytes(*args):
    result = subprocess.run(["git", *args], capture_output=True)
    return result.stdout if result.returncode == 0 else None


def has_commit(oid):
    return subprocess.run(["git", "cat-file", "-e", f"{oid}^{{commit}}"], capture_output=True).returncode == 0


def ensure_commit(oid, refspec=None):
    if not has_commit(oid):
        git("fetch", "-q", "origin", refspec or oid)
    if not has_commit(oid):
        sys.exit(f"Could not fetch commit {oid}")


def repo_slug():
    url = git("remote", "get-url", "origin").strip()
    match = re.search(r"[:/]([^/:]+/[^/]+?)(\.git)?$", url)
    return match[1] if match else None


def parse_diff(text):
    """Unified diff with full context -> [{t, o, n, text}]. `o`/`n` are old/new line numbers."""
    lines, old, new, in_hunk = [], 0, 0, False
    for raw in text.splitlines():
        if raw.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            old, new, in_hunk = int(match[1]), int(match[2]), True
            continue
        if not in_hunk or raw.startswith("\\"):
            continue
        tag, body = raw[:1], raw[1:]
        if tag == "+":
            lines.append({"t": "add", "n": new, "text": body})
            new += 1
        elif tag == "-":
            lines.append({"t": "del", "o": old, "text": body})
            old += 1
        else:
            lines.append({"t": "ctx", "o": old, "n": new, "text": body})
            old += 1
            new += 1
    return lines


def mark_commentable(lines, diff_u3):
    """GitHub only takes comments on lines inside the PR's own diff hunks (three lines of
    context). Flag those lines with `c`."""
    old_ranges, new_ranges = [], []
    for match in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff_u3, re.M):
        old_start, old_len, new_start, new_len = (int(g) if g is not None else 1 for g in match.groups())
        old_ranges.append((old_start, old_start + old_len - 1))
        new_ranges.append((new_start, new_start + new_len - 1))
    # `c` is the 1-based hunk number, so a multi-line comment can be kept inside one hunk.
    which = lambda ranges, number: next((i + 1 for i, (a, b) in enumerate(ranges) if a <= number <= b), None)
    for line in lines:
        hunk = which(old_ranges, line["o"]) if line["t"] == "del" else which(new_ranges, line["n"])
        if hunk:
            line["c"] = hunk


def snapshot_entry(path, base, head, status, img_dir):
    stem = hashlib.sha1(path.encode()).hexdigest()[:12]
    entry = {"path": path, "status": status, "before": None, "after": None}
    for side, rev in (("before", base), ("after", head)):
        if (side == "before" and status == "A") or (side == "after" and status == "D"):
            continue
        data = git_bytes("show", f"{rev}:{path}")
        if data:
            name = f"{stem}-{side}.png"
            (img_dir / name).write_bytes(data)
            entry[side] = f"img/{name}"
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out = Path(args.out).expanduser()
    img_dir = out / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    slug = repo_slug()

    if args.pr:
        fields = "number,title,body,author,url,baseRefName,baseRefOid,headRefName,headRefOid,state"
        meta = json.loads(subprocess.run(
            ["gh", "pr", "view", str(args.pr), "--json", fields], check=True, capture_output=True, text=True).stdout)
        ensure_commit(meta["headRefOid"], f"pull/{args.pr}/head")
        ensure_commit(meta["baseRefOid"])
        base_rev, head = meta["baseRefOid"], meta["headRefOid"]
        pr = {"number": meta["number"], "title": meta["title"], "body": meta["body"],
              "author": meta["author"]["login"], "url": meta["url"], "state": meta["state"],
              "baseRef": meta["baseRefName"], "headRef": meta["headRefName"]}
        # `--base` with `--pr` narrows the story to the PR's commits after that one, e.g. since a review.
        if args.base:
            base_rev = git("rev-parse", args.base).strip()
            pr["baseRef"] = args.base
    else:
        if not (args.base and args.head):
            sys.exit("Pass --pr, or both --base and --head")
        base_rev, head = git("rev-parse", args.base).strip(), git("rev-parse", args.head).strip()
        branch = git("rev-parse", "--abbrev-ref", args.head).strip()
        pr = {"number": None, "title": git("log", "-1", "--format=%s", head).strip(), "body": "",
              "author": git("log", "-1", "--format=%an", head).strip(), "url": None, "state": "LOCAL",
              "baseRef": args.base, "headRef": branch}

    base = git("merge-base", base_rev, head).strip()
    ticket = None
    for text in (pr["title"], pr["body"] or "", pr["headRef"] or ""):
        ticket = ticket or re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", text)

    numstat = {}
    for row in git("diff", "--numstat", "--no-renames", base, head).splitlines():
        added, deleted, path = row.split("\t", 2)
        numstat[path] = (added, deleted)
    statuses = [row.split("\t", 1) for row in git("diff", "--name-status", "--no-renames", base, head).splitlines()]

    files, snapshots = [], []
    for status, path in statuses:
        added, deleted = numstat.get(path, ("-", "-"))
        binary = added == "-"
        entry = {"path": path, "status": status[0], "binary": binary,
                 "additions": 0 if binary else int(added), "deletions": 0 if binary else int(deleted),
                 "hash": hashlib.sha256(path.encode()).hexdigest()}
        if SNAPSHOT_RE.search(path):
            snapshots.append(snapshot_entry(path, base, head, status[0], img_dir))
        elif not binary:
            entry["lines"] = parse_diff(git("diff", "-U100000", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
                                            base, head, "--", path))
            mark_commentable(entry["lines"], git("diff", "-U3", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
                                                 base, head, "--", path))
        files.append(entry)

    comments = []
    if pr["number"] and slug:
        try:
            comments = github.fetch_comments(slug, pr["number"])
        except RuntimeError as error:
            print(f"warning: could not fetch review comments: {error}")

    collected = {"comments": comments, "repo": slug, "root": git("rev-parse", "--show-toplevel").strip(), "pr": pr, "ticket": ticket[0] if ticket else None,
                 "base": base, "head": head, "files": files, "snapshots": snapshots}
    (out / "collected.json").write_text(json.dumps(collected, indent=1))

    text_files = [f for f in files if not f["binary"]]
    print(f"Collected into {out}")
    print(f"{pr['title']}  ({pr['author']}, {pr['state']})")
    print(f"base {base[:10]} ({pr['baseRef']})  head {head[:10]}  ticket {collected['ticket'] or '-'}")
    if pr["number"]:
        print(f"{len(comments)} review comments")
    print(f"{len(text_files)} text files, {len(files) - len(text_files)} binary, {len(snapshots)} snapshot images")
    for f in text_files:
        print(f"  {f['status']} +{f['additions']} -{f['deletions']}  {f['path']}")
    if snapshots:
        dirs = {}
        for s in snapshots:
            key = s["path"].split("/__Snapshots__/")[1].rsplit("/", 1)[0]
            dirs[key] = dirs.get(key, 0) + 1
        for key, count in sorted(dirs.items()):
            print(f"  snapshots {key}: {count}")
    if pr["body"]:
        print("\nPR body:\n" + pr["body"].strip())


if __name__ == "__main__":
    main()
