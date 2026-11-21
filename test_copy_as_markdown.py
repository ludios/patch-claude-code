"""Tests for copy-as-markdown.js. All of the cases go on one page, along with
the script, which headless Chrome loads once; for each case, the page selects
some of the case's text, dispatches a copy event, and records the
text/plain and text/html that the event's listeners set (in the event's
own DataTransfer, not on the system clipboard), with a stand-in for the
webview's own copy listener. The cases' HTML is shaped like the webview's
rendered markdown (react-markdown's output, with code blocks wrapped as the
webview wraps them)."""

import base64
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().with_name("copy-as-markdown.js")
# The class of the elements that hold rendered markdown, in the cases.
ROOT = "root_test"
# The text/plain and text/html of the copy that a copy listener on the
# document, standing in for the webview's own, makes in a case with
# webview_copies.
OWN_PLAIN = "own"
OWN_HTML  = "<b>own</b>"
# The webview's rules that change what a copy gives.
WEBVIEW_CSS = (
	f".{ROOT} p {{white-space: pre-wrap}}"
	# KaTeX's own rules.
	" .katex-display {display: block}"
	" .katex .katex-mathml {position: absolute; clip: rect(1px, 1px, 1px, 1px); width: 1px; height: 1px; overflow: hidden}"
)
CODE_BLOCK = (
	'<div class="codeBlockWrapper"><button class="copyButton" style="opacity:0">'
	'<svg viewBox="0 0 16 16"><path d="M0 0h16v16z"></path></svg></button>'
	'<pre><code class="language-{language}">{code}</code></pre></div>'
)


def markdown(html: str) -> str:
	"""Return `html` as the contents of an element holding rendered markdown."""
	return f'<span class="{ROOT}">{html}</span>'


def katex(tex: str, display: bool) -> str:
	"""Return HTML shaped like the webview's rendering of `tex`, in display
	mode when `display`: KaTeX's, whose drawn glyphs are the text
	"rendered", in an inline wrapper."""
	math = (
		'<span class="katex"><span class="katex-mathml"><math><semantics><mrow><mi>x</mi></mrow>'
		f'<annotation encoding="application/x-tex">{tex}</annotation></semantics></math></span>'
		'<span class="katex-html" aria-hidden="true">rendered</span></span>'
	)
	if display:
		math = f'<span class="katex-display">{math}</span>'
	return f'<span data-redrawn="">{math}</span>'


@dataclass(frozen=True)
class Case:
	name: str
	"""A unique name for the case."""
	html: str
	"""The case's contents."""
	want: str | None
	"""The text/plain that the copy must give, or None when the script must
	leave the copy alone."""
	select: tuple[str, str] | None = None
	"""(first, last): the selection runs from the start of the first
	`first` in the case's text to the end of the first `last` at or after
	it. A `first` that starts with "]" makes it start right after the rest
	of `first`, and a `last` that starts with "[" makes it end right before
	the rest of `last`, each in the text node that holds that text. None
	selects all of the case."""
	webview_copies: bool = True
	"""Whether the stand-in for the webview's own copy listener makes the
	copy (OWN_PLAIN and OWN_HTML) before the script's listener runs, as the
	webview's listener does for any selection of shown text."""


CASES = [
	Case("whole reply", markdown(
		'<h2>Plan</h2>\n'
		'<p>Use <strong>bold</strong>, <em>em</em>, <del>old</del>, <code>a`b</code>, <code> a</code>,\n'
		'<a href="https://example.com/x_(y)">a link</a>, <a href="https://example.com">https://example.com</a>'
		' and <a href="mailto:me@example.com">me@example.com</a>.</p>\n'
		'<ul>\n<li><strong>Fix</strong>: handled <code>None</code> in <a href="src/main.py">main.py</a></li>\n</ul>\n'
		+ CODE_BLOCK.format(language="python", code='<span class="tok">def</span> f():\n    return 1\n')
		+ '\n<hr>\n<p>Done.</p>'),
		"## Plan\n\n"
		"Use **bold**, *em*, ~~old~~, ``a`b``, `  a `,\n[a link](<https://example.com/x_(y)>),"
		" https://example.com and me@example.com.\n\n"
		"- **Fix**: handled `None` in [main.py](src/main.py)\n\n"
		"```python\ndef f():\n    return 1\n```\n\n"
		"---\n\n"
		"Done."),
	Case("nested lists", markdown(
		'<ul>\n<li>one</li>\n<li>two\n<ol start="3">\n<li>three</li>\n<li>four</li>\n</ol>\n</li>\n</ul>'),
		"- one\n- two\n  3. three\n  4. four"),
	Case("loose list", markdown(
		'<ol>\n<li>\n<p>first</p>\n<p>more</p>\n</li>\n<li>\n<p>second</p>\n'
		+ CODE_BLOCK.format(language="sh", code="echo hi\n") + '\n</li>\n</ol>'),
		"1. first\n\n   more\n\n2. second\n\n   ```sh\n   echo hi\n   ```"),
	Case("task list", markdown(
		'<ul class="contains-task-list">\n'
		'<li class="task-list-item"><input type="checkbox" disabled> todo</li>\n'
		'<li class="task-list-item"><input type="checkbox" checked disabled> done</li>\n</ul>'),
		"- [ ] todo\n- [x] done"),
	Case("fence in a code block", markdown(
		CODE_BLOCK.format(language="md", code="```\ninner\n```\n")),
		"````md\n```\ninner\n```\n````"),
	Case("table", markdown(
		'<table>\n<thead>\n<tr>\n<th style="text-align:left">Left</th>\n<th style="text-align:center">Mid</th>\n'
		'<th style="text-align:right">Right</th>\n<th>None</th>\n</tr>\n</thead>\n<tbody>\n'
		'<tr>\n<td style="text-align:left"><code>x|y</code></td>\n<td style="text-align:center"><strong>b</strong></td>\n'
		'<td style="text-align:right">c</td>\n<td></td>\n</tr>\n</tbody>\n</table>'),
		"| Left   | Mid   | Right | None |\n"
		"| :----- | :---: | ----: | ---- |\n"
		"| `x\\|y` | **b** | c     |      |"),
	Case("links", markdown(
		'<p><a href="README.md">README.md</a>, <a href="https://x.test/a.">https://x.test/a.</a>,'
		' <a href="https://x.test/b">https://x.test/b</a></p>'),
		"[README.md](README.md), [https://x.test/a.](https://x.test/a.), https://x.test/b"),
	Case("hard line break", markdown('<p>Hard<br>\nbreak</p>'),
		"Hard  \nbreak"),
	Case("quote and rule", markdown(
		'<blockquote>\n<p>quoted</p>\n<ul>\n<li>item</li>\n</ul>\n</blockquote>\n<hr>\n<p>after</p>'),
		"> quoted\n>\n> - item\n\n---\n\nafter"),
	Case("math", markdown(
		f'<p>Energy {katex("E = mc^2", False)} holds.</p>\n{katex(r"\int_0^1 x\,dx", True)}'),
		"Energy $E = mc^2$ holds.\n\n$$\n\\int_0^1 x\\,dx\n$$"),
	Case("display math between paragraphs", markdown(
		f'<p>Before</p>\n{katex("a^2", True)}\n<p>After</p>'),
		"Before\n\n$$\na^2\n$$\n\nAfter"),
	Case("formula touched at its end", markdown(f'<p>Energy {katex("E", False)} holds.</p>'),
		"holds", select=("]rendered", "holds")),
	Case("formula touched at its start", markdown(f'<p>Para</p>\n{katex("E", True)}'),
		"Para", select=("Para", "[rendered")),
	Case("insight lines", markdown(
		'<p><code style="display:block">★ Insight ─────</code>\nKey point\n<code style="display:block">─────</code></p>'),
		"`★ Insight ─────`\nKey point\n`─────`"),
	Case("inside a formula", markdown(f'<p>Energy {katex("E = mc^2", False)} holds.</p>'),
		"$E = mc^2$", select=("rend", "ered")),
	Case("inside a paragraph", markdown('<p>Hello <strong>bold</strong> world</p>'),
		"ello **bold** wor", select=("ello", "wor")),
	Case("from inside bold", markdown('<p>Hello <strong>bold</strong> world</p>'),
		"**old** world", select=("old", "world")),
	Case("inside bold", markdown('<p>Hello <strong>bold</strong> world</p>'),
		"**ol**", select=("ol", "ol")),
	Case("inside a link", markdown('<p>See <a href="https://x.test/">my <em>own</em> site</a>.</p>'),
		"[*own* site](https://x.test/)", select=("own", "site")),
	Case("inside a heading", markdown('<h2>Title</h2>\n<p>text</p>'),
		"itl", select=("itl", "itl")),
	Case("inside a code block", markdown(CODE_BLOCK.format(language="py", code="a = 1\nb = 2\nc = 3\n")),
		"= 1\nb = 2\nc", select=("= 1", "c")),
	Case("inside inline code", markdown('<p>Call <code>foo_bar()</code> now.</p>'),
		"o_bar(", select=("o_b", "r(")),
	Case("across numbered items", markdown(
		'<ol start="3">\n<li>third</li>\n<li>fourth</li>\n<li>fifth</li>\n</ol>'),
		"3. ird\n4. fou", select=("ird", "fou")),
	Case("table rows without the header", markdown(
		'<table>\n<thead>\n<tr>\n<th>Name</th>\n<th>Value</th>\n</tr>\n</thead>\n<tbody>\n'
		'<tr>\n<td>one</td>\n<td>1</td>\n</tr>\n<tr>\n<td>two</td>\n<td>2</td>\n</tr>\n'
		'<tr>\n<td>three</td>\n<td>3</td>\n</tr>\n</tbody>\n</table>'),
		"| Name | Value |\n| ---- | ----- |\n| wo   | 2     |\n| thr  |       |", select=("wo", "thr")),
	Case("hidden text", markdown(
		'<p>shown</p>\n<p style="display:none">hidden</p>\n<p><span style="user-select:none">nope</span>yes</p>'),
		"shown\n\nyes"),
	Case("no markdown", '<div>a prompt</div>', None),
	Case("no copy by the webview", markdown('<p><em>hi</em></p>'), None, webview_copies=False),
	Case("prompt, then reply",
		'<div style="white-space:pre-wrap">line 1\nline 2</div>'
		+ markdown('<p>Reply with <code>code</code>.</p>'),
		"line 1\nline 2\n\nReply with `code`."),
]


def chrome() -> str:
	"""Return the path of a Chrome binary, or skip the test if none is installed."""
	for name in ("google-chrome-stable", "google-chrome", "chromium"):
		path = shutil.which(name)
		if path:
			return path
	pytest.skip("no Chrome to run copy-as-markdown.js in")


def page(cases: list[Case]) -> str:
	"""Return the HTML of a page that runs `cases` when loaded, and then
	puts, in its body's data-out attribute, the base64 of the UTF-8 of a
	JSON list of each case's {plain, html} (text/plain and text/html of the
	copy) or
	{error}."""
	body = "\n".join(f'<div class="case">{case.html}</div>' for case in cases)
	specs = json.dumps([asdict(case) | {"html": None} for case in cases])
	return f"""<!doctype html><html><head><meta charset="utf-8"><style>{WEBVIEW_CSS}</style></head><body>
{body}
<script>{SCRIPT.read_text(encoding="utf-8")}(".{ROOT}");</script>
<script>
const OWN_PLAIN = {json.dumps(OWN_PLAIN)};
const OWN_HTML  = {json.dumps(OWN_HTML)};

// Return the Range in `container` that a Case's select = [first, last]
// describes.
function textRange(container, first, last) {{
	const nodes = [];
	let text = "";
	const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
	while (walker.nextNode()) {{
		nodes.push([walker.currentNode, text.length]);
		text += walker.currentNode.data;
	}}
	const startsAfter = first.startsWith("]");
	const endsBefore  = last.startsWith("[");
	first = first.slice(startsAfter ? 1 : 0);
	last  = last.slice(endsBefore ? 1 : 0);
	const firstAt = text.indexOf(first);
	const start   = startsAfter ? firstAt + first.length : firstAt;
	const lastAt  = text.indexOf(last, start);
	if (firstAt < 0 || lastAt < 0)
		throw new Error(`no ${{JSON.stringify(first)}}...${{JSON.stringify(last)}} in ${{JSON.stringify(text)}}`);
	const end = endsBefore ? lastAt : lastAt + last.length;
	// The text node and offset in it for `offset` in `text`; at the
	// boundary of two text nodes, the first of them when `inFirst`.
	const at = (offset, inFirst) => {{
		const [node, nodeStart] = nodes.findLast(([node, nodeStart]) =>
			inFirst ? nodeStart < offset : nodeStart <= offset);
		return [node, offset - nodeStart];
	}};
	const range = document.createRange();
	range.setStart(...at(start, startsAfter));
	range.setEnd(...at(end, !endsBefore));
	return range;
}}

function ownListener(event) {{
	event.clipboardData.setData("text/plain", OWN_PLAIN);
	event.clipboardData.setData("text/html", OWN_HTML);
	event.preventDefault();
}}

const specs = {specs};
const results = [...document.querySelectorAll(".case")].map((container, index) => {{
	const spec = specs[index];
	try {{
		let range = document.createRange();
		if (spec.select)
			range = textRange(container, ...spec.select);
		else
			range.selectNodeContents(container);
		const selection = document.getSelection();
		selection.removeAllRanges();
		selection.addRange(range);
		if (spec.webview_copies)
			document.addEventListener("copy", ownListener);
		const data = new DataTransfer();
		const event = new ClipboardEvent("copy", {{clipboardData: data, bubbles: true, cancelable: true}});
		const start = range.startContainer;
		(start instanceof Element ? start : start.parentElement).dispatchEvent(event);
		document.removeEventListener("copy", ownListener);
		return {{plain: data.getData("text/plain"), html: data.getData("text/html")}};
	}} catch (error) {{
		return {{error: String(error)}};
	}}
}});
document.body.dataset.out = btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(results))));
</script></body></html>"""


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, object]]:
	"""Return {case name: what the page recorded for the case}."""
	names = [case.name for case in CASES]
	assert len(set(names)) == len(names), "case names must be unique"
	path = tmp_path_factory.mktemp("copy-as-markdown") / "page.html"
	path.write_text(page(CASES), encoding="utf-8")
	dom = subprocess.run(
		[chrome(), "--headless=new", "--disable-gpu", "--dump-dom", path.as_uri()],
		capture_output=True, text=True, timeout=60, check=True,
	).stdout
	match = re.search(r'data-out="([^"]*)"', dom)
	assert match, f"the page recorded no results:\n{dom}"
	return dict(zip(names, json.loads(base64.b64decode(match[1]).decode("utf-8")), strict=True))


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_copy(case: Case, results: dict[str, dict[str, object]]) -> None:
	result = results[case.name]
	assert "error" not in result, result
	# The script never changes the copy's text/html.
	own = {"plain": OWN_PLAIN, "html": OWN_HTML} if case.webview_copies else {"plain": "", "html": ""}
	assert result == own | ({} if case.want is None else {"plain": case.want})
