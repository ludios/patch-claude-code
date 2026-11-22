"""Tests for commit-links.js. Each node process that a test starts runs the
script on a list of cases: its remark plugin on mdast trees shaped like
remark-parse's, or its open function with a stand-in for the VS Code API
(the Git extension's API, the commands and the warnings)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().with_name("commit-links.js")
SCHEME = "git-commit:"
ID     = "11d4b47"
FULL   = "11d4b47e5c2f3a90b7d6e8f1a2b3c4d5e6f7a8b9"

# Text that holds commit IDs: (text, the IDs that the plugin links, in order).
LINKED = [
	(f"fixed in {ID}.", [ID]),
	(f"{ID}", [ID]),
	(f"({ID})", [ID]),
	(f"{ID}, {ID}:", [ID, ID]),
	(f"{ID}^ and {ID}~2", [ID, ID]),
	(f"{ID}'s parent", [ID]),
	(f"`{ID}`", [ID]),
	(f"{ID}..86638da", [ID, "86638da"]),
	(f"{ID}...86638da", [ID, "86638da"]),
	(f"commit {FULL}", [FULL]),
	(f"see:{ID}", [ID]),
	("defaced1", ["defaced1"]),
]
# Text that holds no commit ID.
UNLINKED = [
	"abc123",
	"1234567",
	"defaced",
	"ABC1234",
	"Abc1234",
	FULL + "c",
	"a" * 63 + "1",
	"2c5c36bd-0cd1-4e2f-9b1a-1234567890ab",
	"#4726a8ff",
	"index.abc1234",
	".abc1234",
	"abc1234.js",
	"/tmp/abc1234",
	"abc1234/foo",
	"C:\\abc1234",
	"0x7fff1234",
	"root_abc1234",
	"abc1234_x",
	"abc1234-x",
	"abc1234#L10",
]

HARNESS = """
const fs = require("fs");
const [script, scheme, specsJson] = process.argv.slice(1);
const {remarkPlugin, open} = eval(fs.readFileSync(script, "utf8"))(scheme);
const specs = JSON.parse(specsJson);

// A stand-in for the VS Code API: `git` is "ok", "missing" (no Git
// extension) or "disabled" (getAPI throws); each repository has a root and
// {argument of getCommit: hash}; `own` is the root that getRepository(cwd)
// gives (or null); `viewFails` makes the command that shows a commit throw.
function fakeVscode({git, repositories, own, viewFails}, calls) {
	const repository = ({root, commits}) => ({
		rootUri: {fsPath: root},
		async getCommit(ref) {
			calls.push(["getCommit", root, ref]);
			if (!(ref in commits))
				throw new Error("bad object");
			return {hash: commits[ref]};
		},
	});
	const api = {
		get repositories() {
			return repositories.map(repository);
		},
		getRepository(uri) {
			calls.push(["getRepository", uri.fsPath]);
			const found = repositories.find((r) => r.root === own);
			return found ? repository(found) : null;
		},
	};
	return {
		Uri: {file: (path) => ({fsPath: path})},
		extensions: {
			getExtension: (id) => id !== "vscode.git" || git === "missing" ? undefined : {
				activate: async () => ({
					getAPI(version) {
						if (git === "disabled")
							throw new Error("Git model not found");
						if (version !== 1)
							throw new Error("no such version");
						return api;
					},
				}),
			},
		},
		commands: {
			async executeCommand(...args) {
				calls.push(["executeCommand", ...args]);
				if (viewFails)
					throw new Error("no such command");
			},
		},
		window: {showWarningMessage: (message) => calls.push(["warning", message])},
	};
}

(async () => {
	const results = [];
	for (const spec of specs) {
		if (spec.tree !== undefined) {
			remarkPlugin()(spec.tree);
			results.push(spec.tree);
		} else {
			const calls = [];
			const shown = await open(fakeVscode(spec, calls), spec.cwd, spec.url);
			results.push({shown, calls});
		}
	}
	process.stdout.write(JSON.stringify(results));
})();
"""


def node() -> str:
	"""Return the path of node, or skip the test if it is not installed."""
	path = shutil.which("node")
	if not path:
		pytest.skip("no node to run commit-links.js in")
	return path


def run(specs: list[dict[str, object]]) -> list[object]:
	"""Return what the script gives for each of `specs`: for {tree}, the
	tree after the remark plugin ran on it; for an open spec (see
	fakeVscode in HARNESS), {shown, calls}."""
	output = subprocess.run(
		[node(), "-e", HARNESS, str(SCRIPT), SCHEME, json.dumps(specs)],
		capture_output=True, text=True, timeout=60, check=True,
	).stdout
	return json.loads(output)


# A position, as remark-parse gives each node.
POSITION = {"start": {"line": 1, "column": 1, "offset": 0}, "end": {"line": 1, "column": 8, "offset": 7}}


def paragraph(*children: dict[str, object]) -> dict[str, object]:
	"""Return an mdast root that holds one paragraph of `children`."""
	return {"type": "root", "children": [{"type": "paragraph", "children": list(children)}]}


def text(value: str) -> dict[str, object]:
	return {"type": "text", "value": value}


def hast_link(id: str) -> dict[str, object]:
	"""Return the hast link that the plugin puts in inline code for `id`."""
	return {"type": "element", "tagName": "a", "properties": {"href": SCHEME + id}, "children": [text(id)]}


def linked_code(id: str) -> dict[str, object]:
	"""Return the inline code that the plugin makes of the commit ID `id`
	in a text node: code whose one child is the link."""
	return {"type": "inlineCode", "value": id, "data": {"hChildren": [hast_link(id)]}}


def linked_ids(tree: dict[str, object]) -> list[str]:
	"""Return the IDs of the links in `tree`'s paragraph, after checking that
	it holds only text nodes and inline code shaped like the plugin's."""
	ids = []
	for node in tree["children"][0]["children"]:
		if node["type"] == "inlineCode":
			assert node == linked_code(node["value"])
			ids.append(node["value"])
		else:
			assert node["type"] == "text"
	return ids


def shown_text(tree: dict[str, object]) -> str:
	"""Return the text of `tree`'s paragraph (which holds only text nodes
	and inline code), as the reader sees it."""
	return "".join(node["value"] for node in tree["children"][0]["children"])


@pytest.fixture(scope="module")
def text_results() -> list[object]:
	"""Return the plugin's tree for a paragraph of each LINKED, then of
	each UNLINKED, text."""
	return run([{"tree": paragraph(text(value) | {"position": POSITION})} for value, _ in LINKED] +
	           [{"tree": paragraph(text(value) | {"position": POSITION})} for value in UNLINKED])


@pytest.mark.parametrize(("index", "value", "ids"), [(i, *case) for i, case in enumerate(LINKED)], ids=[v for v, _ in LINKED])
def test_links_ids_in_text(text_results: list[object], index: int, value: str, ids: list[str]) -> None:
	tree = text_results[index]
	assert linked_ids(tree) == ids
	assert shown_text(tree) == value


@pytest.mark.parametrize(("index", "value"), list(enumerate(UNLINKED)), ids=UNLINKED)
def test_leaves_other_text_alone(text_results: list[object], index: int, value: str) -> None:
	# The same node, position and all.
	assert text_results[len(LINKED) + index] == paragraph(text(value) | {"position": POSITION})


def test_tree_shapes() -> None:
	"""Links come in nested inline nodes and inline code, but not in
	existing links, code blocks or math."""
	position = POSITION
	inside = [
		paragraph({"type": "strong", "children": [text(f"in {ID}")]}),
		paragraph({"type": "link", "url": "https://example.com/", "title": None, "children": [text(ID)]}),
		paragraph({"type": "linkReference", "identifier": "x", "label": "x", "referenceType": "full", "children": [text(ID)]}),
		{"type": "root", "children": [{"type": "code", "lang": None, "meta": None, "value": ID}]},
		paragraph({"type": "inlineMath", "value": ID}),
		paragraph({"type": "inlineCode", "value": ID, "position": position}),
		paragraph({"type": "inlineCode", "value": f"git show\n{ID}", "data": {"kept": True}}),
		paragraph({"type": "inlineCode", "value": "git show HEAD"}),
		paragraph({**text("nothing here"), "position": position}),
	]
	results = run([{"tree": tree} for tree in inside])
	assert results == [
		paragraph({"type": "strong", "children": [text("in "), linked_code(ID)]}),
		*inside[1:5],
		paragraph({"type": "inlineCode", "value": ID, "position": position, "data": {"hChildren": [hast_link(ID)]}}),
		paragraph({"type": "inlineCode", "value": f"git show\n{ID}",
		           "data": {"kept": True, "hChildren": [text("git show "), hast_link(ID)]}}),
		*inside[7:],
	]


# A commit in another repository whose ID also starts with ID.
OTHER = ID + "c" * 33
REPOSITORIES = [
	{"root": "/a", "commits": {}},
	{"root": "/b", "commits": {f"{ID}^{{commit}}": FULL}},
	{"root": "/c", "commits": {f"{ID}^{{commit}}": OTHER}},
]


def open_spec(url: str = SCHEME + ID, git: str = "ok", own: str | None = None,
              repositories: list[dict[str, object]] = REPOSITORIES, view_fails: bool = False) -> dict[str, object]:
	"""Return a spec for running open(url) in the cwd "/cwd". `git` is "ok",
	"missing" (there is no Git extension) or "disabled" (its getAPI
	throws); `repositories` are the repositories that Git has open, each
	{root, commits: {argument of getCommit: the commit's hash}}; `own` is
	the root of the one that Git's getRepository gives for the cwd (None:
	none); `view_fails` makes the command that shows a commit throw."""
	return {"url": url, "cwd": "/cwd", "git": git, "own": own, "repositories": repositories, "viewFails": view_fails}


def get_commit(root: str) -> list[str]:
	"""Return the call of the getCommit of the repository at `root` for ID."""
	return ["getCommit", root, f"{ID}^{{commit}}"]


def view_commit(root: str, hash: str) -> list[str]:
	"""Return the command that shows the commit `hash` of the repository at
	`root`."""
	return ["executeCommand", "git.viewCommit", root, hash]


def test_open() -> None:
	results = run([
		open_spec(),
		open_spec(own="/c"),
		open_spec(own="/a"),
		open_spec(repositories=REPOSITORIES[:1]),
		open_spec(url=SCHEME + "--output=/tmp/x"),
		open_spec(url=SCHEME + ID + " --"),
		open_spec(git="missing"),
		open_spec(git="disabled"),
		open_spec(repositories=[]),
		# A branch named like the ID, in /a.
		open_spec(repositories=[{"root": "/a", "commits": {f"{ID}^{{commit}}": "c" * 40}}, *REPOSITORIES[1:]]),
		open_spec(view_fails=True),
	])
	no_commit = ["warning", f"No Git repository open in this window has exactly one commit whose ID starts with {ID}."]
	no_repository = ["warning", f"Cannot look up commit {ID}: Git has no repository open in this window."]
	assert results == [
		# The first repository with the commit.
		{"shown": True, "calls": [["getRepository", "/cwd"], get_commit("/a"), get_commit("/b"), view_commit("/b", FULL)]},
		# The cwd's repository first.
		{"shown": True, "calls": [["getRepository", "/cwd"], get_commit("/c"), view_commit("/c", OTHER)]},
		{"shown": True, "calls": [["getRepository", "/cwd"], get_commit("/a"), get_commit("/b"), view_commit("/b", FULL)]},
		{"shown": False, "calls": [["getRepository", "/cwd"], get_commit("/a"), no_commit]},
		# Anything but an ID never gets to Git.
		{"shown": False, "calls": [["warning", 'Not a commit ID: "--output=/tmp/x"']]},
		{"shown": False, "calls": [["warning", f'Not a commit ID: "{ID} --"']]},
		{"shown": False, "calls": [no_repository]},
		{"shown": False, "calls": [["warning", f"Cannot look up commit {ID}: Error: Git model not found"]]},
		{"shown": False, "calls": [["getRepository", "/cwd"], no_repository]},
		{"shown": True, "calls": [["getRepository", "/cwd"], get_commit("/a"), get_commit("/b"), view_commit("/b", FULL)]},
		{"shown": False, "calls": [["getRepository", "/cwd"], get_commit("/a"), get_commit("/b"), view_commit("/b", FULL),
		                           ["warning", f"Cannot show commit {ID}: Error: no such command"]]},
	]
