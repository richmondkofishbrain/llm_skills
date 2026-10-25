#!/usr/bin/env python3
"""Turn a story.json (written by the model) plus collected.json (written by collect.py)
into a self-contained index.html.

    build.py --dir ~/.cache/pr-story/15442

Code excerpts are never typed by the model: a code node names a file and a line range on the
new side of the diff, and this script copies those lines (with any deletions inside or at the
edges of the range) out of the real diff. For files the PR does not touch, it reads the file at
the head commit.

Exits non-zero on any validation error, including changed lines that no node shows and that
are not claimed as glue. Pass --allow-uncovered to render anyway; the page then lists the
uncovered lines in a node of their own.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "template.html"
KINDS = {"journey", "code", "snaps", "html", "note", "glue"}
TEST_PATH = re.compile(r"(^|/)(\w*Tests?|\w*UITests|SnapshotTests)/")
MAX_EXCERPT = 40

errors, warnings = [], []
# Every line of each file a code node shows, so the page can expand an excerpt like GitHub does.
sources = {}


def parse_ranges(spec):
    ranges = []
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            ranges.append((int(start), int(end)))
        else:
            ranges.append((int(part), int(part)))
    return ranges


def slice_diff(lines, start, end):
    """Lines whose new-side number is in [start, end], plus deletions inside or touching it."""
    indices = [i for i, l in enumerate(lines) if "n" in l and start <= l["n"] <= end]
    if not indices:
        return None
    first, last = indices[0], indices[-1]
    while first > 0 and lines[first - 1]["t"] == "del":
        first -= 1
    while last + 1 < len(lines) and lines[last + 1]["t"] == "del":
        last += 1
    return lines[first:last + 1]


def slice_deleted(lines, start, end):
    """For deleted files: a range on the old side."""
    picked = [l for l in lines if l["t"] == "del" and start <= l["o"] <= end]
    return picked or None


def head_file(collected, path):
    result = subprocess.run(["git", "-C", collected["root"], "show", f"{collected['head']}:{path}"],
                            capture_output=True, text=True)
    return result.stdout.splitlines() if result.returncode == 0 else None


def resolve_code(node, files, collected, covered):
    path, spec = node.get("file"), node.get("lines")
    if not path or not spec:
        errors.append(f"{node['id']}: code nodes need `file` and `lines`")
        return
    changed = files.get(path)
    excerpt, spans, first_change = [], [], None
    for i, (start, end) in enumerate(parse_ranges(spec)):
        if i:
            excerpt.append({"t": "gap"})
        if changed and changed.get("lines") is not None:
            picked = (slice_deleted if changed["status"] == "D" else slice_diff)(changed["lines"], start, end)
            sources[path] = changed["lines"]
        else:
            source = head_file(collected, path)
            if source is None:
                errors.append(f"{node['id']}: {path} is not in the diff or at the head commit")
                return
            if end > len(source):
                errors.append(f"{node['id']}: {path} has {len(source)} lines, range ends at {end}")
                return
            sources.setdefault(path, [{"t": "ctx", "n": n, "text": text} for n, text in enumerate(source, 1)])
            picked = sources[path][start - 1:end]
        if not picked:
            errors.append(f"{node['id']}: no lines of {path} in {start}-{end}")
            return
        for line in picked:
            if line["t"] == "add":
                covered.add((path, "n", line["n"]))
                first_change = first_change or ("R", line["n"])
            elif line["t"] == "del":
                covered.add((path, "o", line["o"]))
                first_change = first_change or ("L", line["o"])
        excerpt.extend(picked)
        position = {id(line): i for i, line in enumerate(sources[path])}
        spans.append([position[id(picked[0])], position[id(picked[-1])]])
    if len(excerpt) > MAX_EXCERPT:
        warnings.append(f"{node['id']}: {len(excerpt)}-line excerpt; split it or narrow the range")

    has_change = first_change is not None
    node["excerpt"] = excerpt
    node["spans"] = spans
    node["changed"] = has_change
    node["role"] = node.get("role") or ("test" if TEST_PATH.search(path) else "changed" if has_change else "context")
    first_line = next((l.get("n") or l.get("o") for l in excerpt if l["t"] != "gap"), 1)
    node["line"] = first_line
    repo, pr = collected["repo"], collected["pr"]
    if has_change and pr.get("url"):
        side, number = first_change
        node["link"] = f"{pr['url']}/files#diff-{changed['hash']}{side}{number}"
    elif repo:
        last_line = max((l.get("n") or 0) for l in excerpt if l["t"] != "gap")
        node["link"] = f"https://github.com/{repo}/blob/{collected['head']}/{path}#L{first_line}-L{last_line}"


def png_size(path):
    """Pixel width and height from a PNG's header."""
    with open(path, "rb") as f:
        header = f.read(24)
    return (int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")) if header[:8] == b"\x89PNG\r\n\x1a\n" else (None, None)


def variant_label(rest):
    rest = rest.removesuffix(".png").lstrip("-._")
    return rest or "light"


def resolve_snaps(node, collected, claimed_binaries, work):
    pairs = []
    for pick in node.get("pick", []):
        matches = [s for s in collected["snapshots"] if pick in s["path"]]
        # Light before dark, and otherwise by path.
        matches.sort(key=lambda s: ("dark" in s["path"].split(pick, 1)[1], s["path"]))
        if not matches:
            errors.append(f"{node['id']}: no changed snapshot matches {pick!r}")
            continue
        for snap in matches:
            claimed_binaries.add(snap["path"])
            rest = snap["path"].split(pick, 1)[1]
            name = pick.rsplit("/", 1)[-1].removeprefix("testSnapshot_")
            width, height = png_size(work / (snap["after"] or snap["before"]))
            pairs.append({"label": variant_label(rest), "name": name, "status": snap["status"],
                          "before": snap["before"], "after": snap["after"], "path": snap["path"],
                          "px": width, "py": height})
    node["pairs"] = pairs


def resolve_glue(node, files, claimed_binaries):
    patterns = node.get("files", [])
    listed, snapshot_dirs = [], {}
    for f in files.values():
        if not any(fnmatch.fnmatch(f["path"], p) for p in patterns):
            continue
        f["glue"] = True
        if "/__Snapshots__/" in f["path"]:
            key = f["path"].split("/__Snapshots__/")[1].rsplit("/", 1)[0]
            snapshot_dirs[key] = snapshot_dirs.get(key, 0) + 1
            claimed_binaries.add(f["path"])
        else:
            listed.append({"path": f["path"], "status": f["status"], "additions": f["additions"],
                           "deletions": f["deletions"], "binary": f["binary"]})
            if f["binary"]:
                claimed_binaries.add(f["path"])
    node["listed"] = listed
    node["snapshotDirs"] = [{"dir": k, "count": v} for k, v in sorted(snapshot_dirs.items())]


def uncovered_lines(files, covered):
    report = []
    for f in files.values():
        if f["binary"] or f.get("glue") or f.get("lines") is None:
            continue
        # Blank added or deleted lines don't need a home in the story.
        missing = [l for l in f["lines"] if l["t"] in ("add", "del") and l["text"].strip() and
                   (f["path"], "n" if l["t"] == "add" else "o", l.get("n") or l.get("o")) not in covered]
        if not missing:
            continue
        spans, run = [], [missing[0]]
        for line in missing[1:]:
            prev = run[-1]
            key, prev_key = line.get("n") or line.get("o"), prev.get("n") or prev.get("o")
            if line["t"] == prev["t"] and key == prev_key + 1:
                run.append(line)
            else:
                spans.append(run)
                run = [line]
        spans.append(run)
        index = {id(l): i for i, l in enumerate(f["lines"])}
        for span in spans:
            side = "new" if span[0]["t"] == "add" else "old"
            a, b = span[0].get("n") or span[0].get("o"), span[-1].get("n") or span[-1].get("o")
            entry = {"path": f["path"], "side": side, "from": a, "to": b, "count": len(span)}
            if side == "old":
                # A deletion has no new-side number; name the new line a range should touch to include it.
                after = next((l["n"] for l in f["lines"][index[id(span[-1])] + 1:] if "n" in l), None)
                before = next((l["n"] for l in reversed(f["lines"][:index[id(span[0])]]) if "n" in l), None)
                entry["touch"] = after or before
            report.append(entry)
    return report


def with_summary(group, chapters):
    """A group without its own summary borrows the summary of the chapter that holds exactly its
    boxes, or failing that the smallest chapter that holds them all."""
    if group.get("summary"):
        return group
    members = set(group.get("nodes", []))
    holders = sorted((c for c in chapters if members <= set(c.get("nodes", []))), key=lambda c: len(c.get("nodes", [])))
    return {**group, "summary": holders[0].get("summary", "")} if holders else group


def with_default_collapse(group, nodes_by_id):
    """A group made only of test code and glue starts collapsed, unless the story says otherwise."""
    if "collapsed" in group:
        return group
    members = [nodes_by_id.get(i) for i in group.get("nodes", [])]
    secondary = all(n and (n.get("kind") == "glue" or (n.get("kind") == "code" and n.get("role") == "test")) for n in members)
    return {**group, "collapsed": bool(members) and secondary}


def ticket_url(key):
    """PR_STORY_TICKET_URL is a template such as https://example.atlassian.net/browse/{key}."""
    template = os.environ.get("PR_STORY_TICKET_URL")
    return template.replace("{key}", key) if key and template else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--allow-uncovered", action="store_true")
    args = parser.parse_args()
    work = Path(args.dir).expanduser()
    collected = json.loads((work / "collected.json").read_text())
    story = json.loads((work / "story.json").read_text())
    files = {f["path"]: f for f in collected["files"]}

    nodes = story.get("nodes", [])
    ids = [n.get("id") for n in nodes]
    for dup in {i for i in ids if ids.count(i) > 1}:
        errors.append(f"duplicate node id {dup!r}")
    known = set(ids)

    covered, claimed_binaries = set(), set()
    # Glue first, so coverage knows which files are claimed.
    for node in sorted(nodes, key=lambda n: n.get("kind") != "glue"):
        kind = node.get("kind")
        if kind not in KINDS:
            errors.append(f"{node.get('id')}: unknown kind {kind!r}")
        elif kind == "code":
            resolve_code(node, files, collected, covered)
            for i, same in enumerate(node.get("same", [])):
                same.setdefault("id", f"{node['id']}~{i}")
                resolve_code(same, files, collected, covered)
        elif kind == "snaps":
            resolve_snaps(node, collected, claimed_binaries, work)
        elif kind == "glue":
            resolve_glue(node, files, claimed_binaries)
        if "col" not in node or "row" not in node:
            errors.append(f"{node.get('id')}: needs `col` and `row`")
        if kind == "html" and "<style" in node.get("html", ""):
            scope = f"#node-{node['id']}"
            if scope not in node["html"]:
                errors.append(f"{node['id']}: scope this node's CSS under `{scope}`")
            for other in set(re.findall(r"#node-([\w-]+)", node["html"])) - {node["id"]}:
                errors.append(f"{node['id']}: its CSS targets `#node-{other}`, another node; use `{scope}`")

    by_id = {n.get("id"): n for n in nodes}
    for node in nodes:
        for key in ("alignWith", "after"):
            if not node.get(key):
                continue
            target = by_id.get(node[key])
            if not target:
                errors.append(f"{node['id']}: {key} names unknown node {node[key]!r}")
            elif (target.get("col", 0), target.get("row", 0)) >= (node.get("col", 0), node.get("row", 0)):
                errors.append(f"{node['id']}: {key} {target['id']!r} must be in an earlier column, or earlier in this one")

    in_chapter = set()
    for chapter in story.get("chapters", []):
        for node_id in chapter.get("nodes", []):
            if node_id not in known:
                errors.append(f"chapter {chapter.get('id')}: unknown node {node_id!r}")
            in_chapter.add(node_id)
    for node_id in known - in_chapter:
        warnings.append(f"{node_id} is in no chapter")
    for edge in story.get("edges", []):
        for end in ("from", "to"):
            if edge.get(end) not in known:
                errors.append(f"edge {edge.get('from')}→{edge.get('to')}: unknown node {edge.get(end)!r}")
    for group in story.get("groups", []):
        for node_id in group.get("nodes", []):
            if node_id not in known:
                errors.append(f"group {group.get('title')!r}: unknown node {node_id!r}")
    for check in story.get("checks", []):
        if check.get("status") not in ("verified", "open"):
            errors.append(f"check {check.get('text')!r}: status must be verified or open")

    uncovered = uncovered_lines(files, covered)
    unclaimed = [f["path"] for f in files.values() if f["binary"] and f["path"] not in claimed_binaries]
    if unclaimed:
        warnings.append(f"{len(unclaimed)} binary files are in no snaps or glue node, e.g. {unclaimed[0]}")
    if uncovered:
        message = "changed lines no node shows:\n" + "\n".join(
            f"    {u['path']} {u['side']} {u['from']}-{u['to']}" +
            (f"  (deleted lines; a range that touches new line {u['touch']} includes them)" if u.get("touch") else "")
            for u in uncovered)
        (warnings if args.allow_uncovered else errors).append(message)

    for w in warnings:
        print(f"warning: {w}")
    if errors:
        for e in errors:
            print(f"error: {e}")
        sys.exit(2)

    text_files = [f for f in files.values() if not f["binary"]]
    changed_lines = sum(f["additions"] + f["deletions"] for f in text_files)
    page = {
        "pr": {**collected["pr"], "ticket": collected["ticket"],
               "ticketURL": story.get("ticketURL") or ticket_url(collected["ticket"]),
               "head": collected["head"],
               "stats": story.get("stats") or (
                   f"{len(text_files)} text files · +{sum(f['additions'] for f in text_files)} "
                   f"−{sum(f['deletions'] for f in text_files)}"
                   + (f" · {len(collected['snapshots'])} snapshot images" if collected["snapshots"] else ""))},
        "intent": story.get("intent", ""),
        "chapters": story.get("chapters", []),
        "checks": story.get("checks", []),
        "nodes": nodes,
        "edges": story.get("edges", []),
        "groups": [with_default_collapse(with_summary(g, story.get("chapters", [])), {n.get("id"): n for n in nodes})
                   for g in story.get("groups", [])],
        "coverage": {"files": len(text_files), "lines": changed_lines, "uncovered": uncovered},
        "comments": collected.get("comments", []),
        "repo": collected.get("repo"),
        "sources": sources,
    }
    payload = json.dumps(page, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("__STORY_JSON__", payload)
    (work / "index.html").write_text(html)
    print(f"Wrote {work / 'index.html'}")


if __name__ == "__main__":
    main()
