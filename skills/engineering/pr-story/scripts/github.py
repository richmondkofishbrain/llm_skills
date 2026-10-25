"""GitHub calls shared by collect.py and serve.py. Everything goes through the `gh` CLI, so it
acts as whoever `gh auth` is logged in as."""
import json
import subprocess


def gh_json(*args, stdin=None):
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"gh {' '.join(args)} failed")
    return json.loads(result.stdout) if result.stdout.strip() else None


def gh_graphql(query, **variables):
    data = gh_json("api", "graphql", "--input", "-", stdin=json.dumps({"query": query, "variables": variables}))
    if data.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in data["errors"]))
    return data["data"]


def split_repo(repo):
    owner, name = repo.split("/", 1)
    return owner, name


# A pending review is visible only to its author, so this finds the viewer's own, if any. Its
# comments come with their thread, which is where GitHub keeps the line, range and side.
PENDING_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      reviews(states: [PENDING], first: 1) { nodes { id } }
      reviewThreads(first: 100) {
        nodes {
          path line startLine diffSide startDiffSide
          comments(first: 50) {
            nodes {
              id fullDatabaseId body createdAt url state viewerDidAuthor
              author { login }
              replyTo { fullDatabaseId }
            }
          }
        }
      }
    }
  }
}"""


def pending_state(repo, number):
    """The PR's node id, the viewer's pending review id (or None) and its comments."""
    owner, name = split_repo(repo)
    pr = gh_graphql(PENDING_QUERY, owner=owner, name=name, number=int(number))["repository"]["pullRequest"]
    reviews = pr["reviews"]["nodes"]
    comments = [{
        "id": int(c["fullDatabaseId"]),
        "nodeId": c["id"],
        "replyTo": int(c["replyTo"]["fullDatabaseId"]) if c.get("replyTo") else None,
        "path": t["path"],
        "line": t["line"],
        "side": t["diffSide"] or "RIGHT",
        "startLine": t["startLine"],
        "startSide": t["startDiffSide"],
        "user": (c.get("author") or {}).get("login", "?"),
        "body": c["body"],
        "created": c["createdAt"],
        "url": c["url"],
        "pending": True,
    } for t in pr["reviewThreads"]["nodes"] for c in t["comments"]["nodes"]
        if c["state"] == "PENDING" and c["viewerDidAuthor"]]
    return pr["id"], reviews[0]["id"] if reviews else None, comments


def fetch_comments(repo, number):
    """Review comments on the PR's diff, oldest first, including the viewer's own pending ones.
    `line` is None for outdated comments."""
    pages = gh_json("api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/comments?per_page=100")
    raw = [c for page in pages for c in page]
    submitted = [{
        "id": c["id"],
        "replyTo": c.get("in_reply_to_id"),
        "path": c["path"],
        "line": c.get("line"),
        "side": c.get("side") or "RIGHT",
        "originalLine": c.get("original_line"),
        "startLine": c.get("start_line"),
        "startSide": c.get("start_side"),
        "user": (c.get("user") or {}).get("login", "?"),
        "body": c.get("body", ""),
        "created": c.get("created_at"),
        "url": c.get("html_url"),
    } for c in raw]
    try:
        _, _, pending = pending_state(repo, number)
    except RuntimeError:
        pending = []
    pending_ids = {c["id"] for c in pending}
    return [c for c in submitted if c["id"] not in pending_ids] + pending


ADD_REVIEW = """
mutation($pr: ID!, $commit: GitObjectID) {
  addPullRequestReview(input: {pullRequestId: $pr, commitOID: $commit}) { pullRequestReview { id } }
}"""
ADD_THREAD = """
mutation($review: ID!, $path: String!, $line: Int!, $side: DiffSide, $startLine: Int, $startSide: DiffSide, $body: String!) {
  addPullRequestReviewThread(input: {pullRequestReviewId: $review, path: $path, line: $line, side: $side,
                                     startLine: $startLine, startSide: $startSide, body: $body}) { thread { id } }
}"""
UPDATE_COMMENT = """
mutation($id: ID!, $body: String!) {
  updatePullRequestReviewComment(input: {pullRequestReviewCommentId: $id, body: $body}) { pullRequestReviewComment { id } }
}"""
DELETE_COMMENT = """
mutation($id: ID!) { deletePullRequestReviewComment(input: {id: $id}) { clientMutationId } }"""
DELETE_REVIEW = """
mutation($id: ID!) { deletePullRequestReview(input: {pullRequestReviewId: $id}) { clientMutationId } }"""


def add_pending_comment(repo, number, commit, comment):
    """Add one comment to the viewer's pending review, starting the review if there is none.
    Nobody else sees it until the viewer submits the review on GitHub."""
    pr_id, review_id, _ = pending_state(repo, number)
    if not review_id:
        review_id = gh_graphql(ADD_REVIEW, pr=pr_id, commit=commit)["addPullRequestReview"]["pullRequestReview"]["id"]
    ranged = comment.get("startLine") and int(comment["startLine"]) != int(comment["line"])
    gh_graphql(ADD_THREAD, review=review_id, path=comment["path"], line=int(comment["line"]), side=comment["side"],
               startLine=int(comment["startLine"]) if ranged else None,
               startSide=(comment.get("startSide") or comment["side"]) if ranged else None,
               body=comment["body"])


def update_pending_comment(node_id, body):
    gh_graphql(UPDATE_COMMENT, id=node_id, body=body)


def delete_pending_comment(repo, number, node_id):
    """Delete one pending comment; a pending review left with no comments is deleted too."""
    gh_graphql(DELETE_COMMENT, id=node_id)
    _, review_id, remaining = pending_state(repo, number)
    if review_id and not remaining:
        gh_graphql(DELETE_REVIEW, id=review_id)


def create_pending_review(repo, number, commit, body, comments, dry_run=False):
    """Create a review in the PENDING state: nobody else sees it until its author submits it
    on GitHub. GitHub allows one pending review per person per PR."""
    def review_comment(c):
        comment = {"path": c["path"], "line": int(c["line"]), "side": c["side"], "body": c["body"]}
        if c.get("startLine") and int(c["startLine"]) != int(c["line"]):
            comment.update(start_line=int(c["startLine"]), start_side=c.get("startSide") or c["side"])
        return comment

    payload = {"commit_id": commit, "comments": [review_comment(c) for c in comments]}
    if body:
        payload["body"] = body
    if dry_run:
        return {"dryRun": True, "payload": payload, "html_url": None}
    review = gh_json("api", "-X", "POST", f"repos/{repo}/pulls/{number}/reviews", "--input", "-",
                     stdin=json.dumps(payload))
    return {"dryRun": False, "id": review.get("id"), "html_url": review.get("html_url"), "state": review.get("state")}
