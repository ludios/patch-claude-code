// Make copying from the chat give markdown. patch-claude-code puts this
// function expression into the webview's index.js and calls it with a CSS
// selector that matches every element holding rendered markdown, and with
// a scheme (such as "git-commit:") of link addresses that a copy leaves
// out.
//
// When a copy (Ctrl+C, or Copy in a context menu) is of a selection that
// includes rendered markdown, its text/plain becomes GFM markdown made from
// the selected elements: headings, emphasis, links, inline code, code
// blocks, lists, quotes, tables, rules and KaTeX math (between "$"s, or
// "$$"s for a display formula) come back as markdown syntax; any other
// text (prompts, tool rows) comes back as the text that the page shows
// for it. A link whose address has that scheme comes back as just its
// text, and so does inline code that holds just such a link (the way that
// the chat shows a commit ID), without the code's backticks.
//
// The markdown is made from the elements, not taken from the reply's
// source, so it can differ from the source: a bullet is always "-",
// italic text is always between "*"s and bold text between "**"s, and
// text is not escaped (except for a "|" in a table cell), so text that
// looks like markdown syntax, such as a literal "*", becomes that syntax.
// Where the selection starts or ends inside an element:
// - a selection that is all code is copied as just that code;
// - a selection inside one block is copied without that block's markup (a
//   heading's "#", an item's bullet), but with the markup of the inline
//   elements around it (bold, a link);
// - a formula is copied whole, when any of its drawn glyphs are selected;
// - rows of a table are copied with its header row, selected or not.
//
// Only the text/plain of a copy that the webview's own copy listener made
// is replaced (patch-claude-code has that listener make the copy of every
// selection, not just of those with math in them), so the text/html of the
// copy stays the one that listener made.
// oxlint-disable-next-line no-unused-expressions -- patch-claude-code calls it
((markdownRoot, droppedScheme) => {
	/**
	 * @param {Range} range
	 * @returns {Element} the innermost element that contains all of `range`
	 */
	function topElement(range) {
		const common = range.commonAncestorContainer;
		return common instanceof Element ? common : common.parentElement ?? document.documentElement;
	}

	/**
	 * @param {string} text
	 * @returns {number} the length of the longest run of backticks in `text`
	 */
	function longestBacktickRun(text) {
		let longest = 0;
		for (const [run] of text.matchAll(/`+/g))
			longest = Math.max(longest, run.length);
		return longest;
	}

	/**
	 * @param {string} text  inline markdown
	 * @param {string} mark  a delimiter, such as "**" or "~~"
	 * @returns {string} `text` between two `mark`s, with the whitespace at
	 *   either end of `text` moved outside them (or `text` itself, when it is
	 *   only whitespace), since markdown does not allow whitespace right
	 *   inside the delimiters
	 */
	function wrap(text, mark) {
		const inner = text.trim();
		if (inner === "")
			return text;
		const before = text.slice(0, text.length - text.trimStart().length);
		const after  = text.slice(text.trimEnd().length);
		return before + mark + inner + mark + after;
	}

	/**
	 * @param {string} code  the text of an inline code element
	 * @returns {string} a markdown code span that renders as `code`, or ""
	 *   when `code` is ""
	 */
	function codeSpan(code) {
		if (code === "")
			return "";
		const ticks = "`".repeat(longestBacktickRun(code) + 1);
		// Markdown strips one space from each end of a code span that starts
		// and ends with one (unless it is all spaces), and a backtick at
		// either end would merge with the delimiter.
		const pad = /^[ `]|[ `]$/.test(code) && /[^ ]/.test(code) ? " " : "";
		return ticks + pad + code + pad + ticks;
	}

	/**
	 * @param {string} code  the text of a code block
	 * @param {string} language  its language, or ""
	 * @returns {string} a fenced markdown code block, or "" when `code` is
	 *   empty (apart from a final newline)
	 */
	function fence(code, language) {
		code = code.replace(/\n$/, "");
		if (code === "")
			return "";
		const ticks = "`".repeat(Math.max(3, longestBacktickRun(code) + 1));
		return ticks + language + "\n" + code + "\n" + ticks;
	}

	/**
	 * @param {Element} pre
	 * @returns {string} the language of the code block `pre`, or ""
	 */
	function languageOf(pre) {
		const className = pre.querySelector("code")?.className ?? "";
		return /(?:^|\s)language-(\S+)/.exec(className)?.[1] ?? "";
	}

	/**
	 * @param {string} text  markdown blocks
	 * @returns {string} `text` as a markdown block quote
	 */
	function quote(text) {
		return text === "" ? "" : text.replace(/^/gm, "> ").replace(/^> $/gm, ">");
	}

	/**
	 * @param {Element} math  a .katex (inline) or .katex-display (display)
	 *   element, as KaTeX renders it
	 * @returns {string} its TeX source, between markdown math delimiters
	 */
	function mathOf(math) {
		const tex = (math.querySelector('annotation[encoding="application/x-tex"]') ?? math).textContent.trim();
		return math.matches(".katex-display") ? "$$\n" + tex + "\n$$" : "$" + tex + "$";
	}

	/** Markdown made from the elements that a Range selects. */
	class SelectionToMarkdown {
		/** @param {Range} range  the selected part of the document */
		constructor(range) {
			this.range = range;
		}

		/** @returns {string} the selection, as markdown */
		convert() {
			let top = topElement(this.range);
			const math = top.closest(".katex-display") ?? top.closest(".katex");
			if (math)
				return this.math(math);
			// A selection that is all code is copied as just that code.
			if (top.closest("pre, code"))
				return this.rawText(top);
			if (top.matches("thead, tbody, tfoot, tr"))
				top = top.closest("table");
			// The selection is inside `top`, so the markup of `top`'s block
			// (a heading's "#", an item's bullet, a quote's ">") is left out,
			// unless `top` is one whose children make no sense without it;
			// but the markup of each inline element that holds the selection
			// (bold, italic, strikethrough, a link) is kept.
			if (top.matches("ol, ul, table"))
				return this.block(top);
			let markdown = this.blocks(top).join("\n\n");
			for (let element = top; !this.isBlock(element); element = element.parentElement)
				markdown = this.markup(element, markdown);
			return markdown;
		}

		/**
		 * @param {Element} parent
		 * @returns {Node[]} the children of `parent` that are (at least
		 *   partly) selected and shown, with each display:contents child
		 *   replaced by its own such children
		 */
		children(parent) {
			const children = [];
			for (const child of parent.childNodes) {
				if (!this.range.intersectsNode(child))
					continue;
				if (child.nodeType === Node.TEXT_NODE) {
					children.push(child);
					continue;
				}
				if (!(child instanceof Element))
					continue;
				const style = getComputedStyle(child);
				if (style.display === "contents")
					children.push(...this.children(child));
				else if (child.checkVisibility({visibilityProperty: true}) && style.userSelect !== "none")
					children.push(child);
			}
			return children;
		}

		/**
		 * @param {Element} element  a selected, shown element
		 * @returns {boolean} whether `element` gives markdown blocks, rather
		 *   than inline markdown: whether it is a markdown root, or is laid
		 *   out as a block, or holds an element that is (as the webview's
		 *   inline wrapper around a display formula does). Inline code and
		 *   inline formulas give inline markdown however they are laid out
		 *   (the "★ Insight" lines of a reply are inline code laid out as
		 *   blocks).
		 */
		isBlock(element) {
			if (element.matches(markdownRoot))
				return true;
			if (element.matches("code, .katex"))
				return false;
			const laidOutAsBlock = (element) => !/^(inline|none|contents)/.test(getComputedStyle(element).display);
			return laidOutAsBlock(element) || [...element.children].some(laidOutAsBlock);
		}

		/**
		 * @param {Text} node  a selected text node
		 * @returns {string} the selected part of `node`'s text
		 */
		slice(node) {
			const {startContainer, startOffset, endContainer, endOffset} = this.range;
			const start = node === startContainer ? startOffset : 0;
			const end   = node === endContainer   ? endOffset   : node.data.length;
			return node.data.slice(start, end);
		}

		/**
		 * @param {Text} node  a selected text node
		 * @returns {string} the selected part of `node`'s text, with each run
		 *   of whitespace collapsed to one space where the page collapses it
		 */
		text(node) {
			let text = this.slice(node);
			if (getComputedStyle(node.parentElement).whiteSpaceCollapse === "collapse")
				text = text.replace(/[ \t\n\r\f]+/g, " ");
			// A <br> already makes a line break, so the newline that the
			// renderer puts after one (a space, where whitespace collapses)
			// is left out.
			return node.previousSibling?.nodeName === "BR" ? text.replace(/^[ \n]/, "") : text;
		}

		/**
		 * @param {Element} element  a selected element
		 * @returns {Range} the selected part of `element`'s contents
		 */
		part(element) {
			const part = document.createRange();
			part.selectNodeContents(element);
			if (part.compareBoundaryPoints(Range.START_TO_START, this.range) < 0)
				part.setStart(this.range.startContainer, this.range.startOffset);
			if (part.compareBoundaryPoints(Range.END_TO_END, this.range) > 0)
				part.setEnd(this.range.endContainer, this.range.endOffset);
			return part;
		}

		/**
		 * @param {Element} element  a selected element
		 * @returns {string} the text of the selected part of `element`'s text
		 *   nodes, as it is, with nothing left out and no markdown added
		 */
		rawText(element) {
			return this.part(element).toString();
		}

		/**
		 * @param {Element} element
		 * @returns {boolean} whether `element` is outside rendered markdown,
		 *   and holds none
		 */
		isPlain(element) {
			return !element.closest(markdownRoot) && !element.querySelector(markdownRoot);
		}

		/**
		 * @param {Element} element  a selected element
		 * @returns {string} the text that the page shows for the selected
		 *   part of `element`, as innerText gives it
		 */
		shownText(element) {
			// innerText is of laid-out elements, so it is taken of a copy of
			// `element` that holds just that part, next to `element` (so it
			// gets the same styles) but out of sight.
			const copy = element.cloneNode(false);
			copy.removeAttribute("id");
			Object.assign(copy.style, {position: "fixed", left: "-99999px", top: "0"});
			copy.append(this.part(element).cloneContents());
			element.after(copy);
			try {
				return copy.innerText;
			} finally {
				copy.remove();
			}
		}

		/**
		 * @param {Element} math  a selected .katex (inline) or .katex-display
		 *   (display) element, as KaTeX renders it
		 * @returns {string} `math` between markdown math delimiters (see
		 *   mathOf), or "" when the selection holds none of its drawn glyphs
		 *   (it only touches the formula's edge)
		 */
		math(math) {
			const drawn = this.rawText(math.querySelector(".katex-html") ?? math);
			return drawn.replaceAll("\u200b", "").trim() === "" ? "" : mathOf(math);
		}

		/**
		 * @param {Element} parent
		 * @returns {string[]} the markdown of each block in the selected part of
		 *   `parent`'s contents, where each run of inline content between
		 *   blocks counts as a block too; empty blocks are left out
		 */
		blocks(parent) {
			const blocks = [];
			let run = "";
			const endRun = () => {
				run = run.trim();
				if (run !== "")
					blocks.push(run);
				run = "";
			};
			for (const child of this.children(parent)) {
				if (child instanceof Element && this.isBlock(child)) {
					endRun();
					const block = this.block(child);
					if (block !== "")
						blocks.push(block);
				} else {
					run += this.inlineNode(child);
				}
			}
			endRun();
			return blocks;
		}

		/**
		 * @param {Element} element  a selected block element
		 * @returns {string} the markdown of the selected part of `element`
		 *   (just its shown text, outside rendered markdown)
		 */
		block(element) {
			if (this.isPlain(element))
				return this.shownText(element).replace(/^\n+|\n+$/g, "");
			if (element.matches(".katex-display"))
				return this.math(element);
			const heading = /^H([1-6])$/.exec(element.tagName);
			if (heading) {
				const text = this.inline(element).trim();
				return text === "" ? "" : "#".repeat(Number(heading[1])) + " " + text;
			}
			switch (element.tagName) {
				case "PRE":
					return fence(this.rawText(element), languageOf(element));
				case "OL":
				case "UL":
					return this.list(element);
				case "LI":
					return this.item(element, "- ", false);
				case "BLOCKQUOTE":
					return quote(this.blocks(element).join("\n\n"));
				case "HR":
					return "---";
				case "TABLE":
					return this.table(element);
				default:
					return this.blocks(element).join("\n\n");
			}
		}

		/**
		 * @param {HTMLOListElement | HTMLUListElement} list
		 * @returns {string} the markdown of the selected items of `list`,
		 *   each numbered as it is shown, when `list` is ordered
		 */
		list(list) {
			const items = [...list.children].filter((child) => child.tagName === "LI");
			// Markdown renders the items of a loose list (one with blank lines
			// between its items) as paragraphs.
			const loose = items.some((item) => item.querySelector(":scope > p"));
			const markdown = [];
			for (const [index, item] of items.entries()) {
				if (!this.range.intersectsNode(item))
					continue;
				const marker = list.tagName === "OL" ? `${list.start + index}. ` : "- ";
				const text = this.item(item, marker, loose);
				if (text !== "")
					markdown.push(text);
			}
			return markdown.join(loose ? "\n\n" : "\n");
		}

		/**
		 * @param {Element} item  a list item
		 * @param {string} marker  its bullet or number, then a space
		 * @param {boolean} loose  whether its list is a loose one
		 * @returns {string} the markdown of the selected part of `item`, or ""
		 *   when none of its text is selected
		 */
		item(item, marker, loose) {
			const text = this.blocks(item).join(loose ? "\n\n" : "\n");
			if (text === "")
				return "";
			return marker + text.replace(/\n(?=.)/g, "\n" + " ".repeat(marker.length));
		}

		/**
		 * @param {HTMLTableRowElement} row
		 * @returns {string[]} the inline markdown of the selected part of each
		 *   of `row`'s cells ("" for a cell with nothing selected), escaped for
		 *   use in a markdown table row
		 */
		cells(row) {
			return [...row.cells].map((cell) =>
				this.inline(cell).trim().replaceAll("  \n", "\n").replaceAll("\n", "<br>").replaceAll("|", "\\|"));
		}

		/**
		 * @param {HTMLTableElement} table  a table rendered from markdown, so
		 *   its first row is its header row
		 * @returns {string} the markdown of the selected rows of `table`, along
		 *   with all of its header row, if the selection leaves that out
		 */
		table(table) {
			const [header, ...body] = table.rows;
			if (!header)
				return "";
			const filled = (cells) => cells.some((cell) => cell !== "");
			const rows = body
				.filter((row) => this.range.intersectsNode(row))
				.map((row) => this.cells(row))
				.filter(filled);
			let head = this.range.intersectsNode(header) ? this.cells(header) : [];
			if (!filled(head)) {
				if (rows.length === 0)
					return "";
				// A markdown table needs its header row.
				const whole = document.createRange();
				whole.selectNodeContents(header);
				head = new SelectionToMarkdown(whole).cells(header);
			}
			rows.unshift(head);
			const columns = rows.reduce((columns, cells) => Math.max(columns, cells.length), 0);
			const rule    = [];
			for (let column = 0; column < columns; column++) {
				const cell  = header.cells[column];
				const align = cell ? cell.style.textAlign || cell.getAttribute("align") || "" : "";
				const left  = align === "left"  || align === "center" ? ":" : "";
				const right = align === "right" || align === "center" ? ":" : "";
				rule.push(left + "---" + right);
			}
			const line = (cells) => "| " + rule.map((_, column) => cells[column] ?? "").join(" | ") + " |";
			return [line(rows[0]), "| " + rule.join(" | ") + " |", ...rows.slice(1).map(line)].join("\n");
		}

		/**
		 * @param {Element} parent
		 * @returns {string} the inline markdown of the selected part of
		 *   `parent`'s contents
		 */
		inline(parent) {
			return this.children(parent).map((child) => this.inlineNode(child)).join("");
		}

		/**
		 * @param {Node} node  a selected, shown node
		 * @returns {string} the inline markdown of the selected part of `node`
		 *   (just its shown text, outside rendered markdown)
		 */
		inlineNode(node) {
			if (node.nodeType === Node.TEXT_NODE)
				return this.text(node);
			if (!(node instanceof HTMLElement))
				return "";
			if (this.isPlain(node))
				return this.shownText(node);
			if (node.matches(".katex-display, .katex"))
				return this.math(node);
			switch (node.tagName) {
				case "BR":
					// A markdown hard line break.
					return "  \n";
				case "CODE":
					if (node.childNodes.length === 1 && node.firstChild instanceof HTMLAnchorElement
					    && node.firstChild.getAttribute("href")?.startsWith(droppedScheme))
						return this.rawText(node);
					return codeSpan(this.rawText(node));
				case "IMG":
					return `![${node.alt}](${node.getAttribute("src") ?? ""})`;
				case "INPUT":
					// A task list item's checkbox (the space after it is in the
					// text that follows it).
					return node.type !== "checkbox" ? "" : node.checked ? "[x]" : "[ ]";
				default:
					return this.markup(node, this.inline(node));
			}
		}

		/**
		 * @param {Element} element  an inline element
		 * @param {string} text  the inline markdown of the selected part of
		 *   `element`'s contents
		 * @returns {string} `text` with `element`'s markup around it, if
		 *   `element` is bold, italic, struck through or a link
		 */
		markup(element, text) {
			switch (element.tagName) {
				case "STRONG":
				case "B":
					return wrap(text, "**");
				case "EM":
				case "I":
					return wrap(text, "*");
				case "DEL":
				case "S":
					return wrap(text, "~~");
				case "A":
					return this.link(element, text);
				default:
					return text;
			}
		}

		/**
		 * @param {HTMLAnchorElement} link
		 * @param {string} text  the inline markdown of the selected part of
		 *   `link`'s contents
		 * @returns {string} `text` as a markdown link to `link`'s address, or
		 *   just `text` when `link` has no address, when its address has
		 *   `droppedScheme`, when `text` is only whitespace, or when GFM
		 *   makes the same link of `text` by itself
		 */
		link(link, text) {
			const href = link.getAttribute("href");
			if (!href || href.startsWith(droppedScheme) || text.trim() === "")
				return text;
			// GFM links a bare web address (leaving out punctuation at its
			// end) and a bare email address.
			if (href === text && /^https?:\/\/[^\s<>]*[\w/]$/.test(href) || href === "mailto:" + text)
				return text;
			return `[${text}](${/[\s()<>]/.test(href) ? `<${href}>` : href})`;
		}
	}

	/**
	 * @param {Range} range
	 * @returns {boolean} whether `range` selects any rendered markdown
	 */
	function selectsMarkdown(range) {
		const top = topElement(range);
		if (top.closest(markdownRoot))
			return true;
		return [...top.querySelectorAll(markdownRoot)].some((root) => range.intersectsNode(root));
	}

	/**
	 * A "copy" listener on the window, so that it runs after the webview's
	 * own listener on the document, and replaces the text/plain that that
	 * one sets.
	 *
	 * @param {ClipboardEvent} event
	 */
	function onCopy(event) {
		const data = event.clipboardData;
		const selection = document.getSelection();
		if (!data || !selection || selection.isCollapsed || selection.rangeCount !== 1)
			return;
		// The webview's own listener sets text/html whenever it makes the copy.
		if (!data.types.includes("text/html"))
			return;
		if (event.target instanceof Element && event.target.closest("input, textarea, [contenteditable]"))
			return;
		const range = selection.getRangeAt(0);
		if (!selectsMarkdown(range))
			return;
		let markdown;
		try {
			markdown = new SelectionToMarkdown(range).convert();
		} catch (error) {
			console.error("copy-as-markdown: converting the selection failed, so the webview's own copy stays:", error);
			return;
		}
		if (markdown === "")
			return;
		data.setData("text/plain", markdown);
		event.preventDefault();
	}

	window.addEventListener("copy", onCopy);
})
