// Make copying from the chat give markdown. patch-claude-code puts this
// function expression into the webview's index.js and calls it with a CSS
// selector that matches every element holding rendered markdown.
//
// On every copy (Ctrl+C, or Copy in a context menu) whose selection
// includes rendered markdown, the text/plain of the copy becomes markdown
// made from the selected elements: headings, emphasis, links, inline code,
// code blocks, lists, quotes, tables, rules and KaTeX math come back as
// markdown syntax; any other text (prompts, tool rows) comes back as text.
// The markdown is made from the elements, not taken from the reply's
// source, so it can differ from the source in spelling, though not in
// meaning: a bullet is always "-", emphasis is always "*", and text is
// never escaped (so a literal "*" in a reply is copied as a bare "*").
// Only the text/plain of a copy that the webview's own copy listener made
// is replaced (patch-claude-code has that listener make the copy of every
// selection, not just of those with math in them), so the text/html of the
// copy stays the one that listener made.
// oxlint-disable-next-line no-unused-expressions -- patch-claude-code calls it
((markdownRoot) => {
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
		return Math.max(0, ...(text.match(/`+/g) ?? []).map((run) => run.length));
	}

	/**
	 * @param {string} text  inline markdown
	 * @param {string} mark  an emphasis delimiter, such as "**"
	 * @returns {string} `text` between two `mark`s, with the whitespace at
	 *   either end of `text` moved outside them (or `text` itself, when it is
	 *   only whitespace), since markdown does not allow that whitespace inside
	 */
	function wrap(text, mark) {
		const [, before, inner, after] = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
		return inner === "" ? text : before + mark + inner + mark + after;
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
	 * @returns {string} a fenced markdown code block, or "" when `code` is ""
	 */
	function fence(code, language) {
		if (code === "")
			return "";
		const ticks = "`".repeat(Math.max(3, longestBacktickRun(code) + 1));
		return ticks + language + "\n" + code.replace(/\n$/, "") + "\n" + ticks;
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
			// The selection is inside `top`, so `top`'s own markup (a heading's
			// "#", an item's bullet, a quote's ">") is left out, unless `top`
			// is one whose children make no sense without it.
			if (top.matches("ol, ul, table"))
				return this.block(top);
			return this.blocks(top).join("\n\n");
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
		 * @param {Element} element
		 * @returns {string} the selected text in `element`, as it is, with
		 *   nothing left out and no markdown added
		 */
		rawText(element) {
			let text = "";
			const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
			while (walker.nextNode()) {
				if (this.range.intersectsNode(walker.currentNode))
					text += this.slice(walker.currentNode);
			}
			return text;
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
		 */
		block(element) {
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
				this.inline(cell).trim().replace(/ *\n/g, "<br>").replaceAll("|", "\\|"));
		}

		/**
		 * @param {HTMLTableElement} table  a table rendered from markdown, so
		 *   its first row is its header row
		 * @returns {string} the markdown of the selected rows of `table`, along
		 *   with all of its header row, if the selection leaves that out
		 */
		table(table) {
			const [header, ...body] = table.rows;
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
			const columns = Math.max(...rows.map((cells) => cells.length));
			const widths  = [];
			const rule    = [];
			for (let column = 0; column < columns; column++) {
				const width = Math.max(3, ...rows.map((cells) => (cells[column] ?? "").length));
				const cell  = header.cells[column];
				const align = cell ? cell.style.textAlign || cell.getAttribute("align") || "" : "";
				const left  = align === "left"  || align === "center" ? ":" : "-";
				const right = align === "right" || align === "center" ? ":" : "-";
				widths.push(width);
				rule.push(left + "-".repeat(width - 2) + right);
			}
			const line = (cells) => "| " + widths.map((width, column) => (cells[column] ?? "").padEnd(width)).join(" | ") + " |";
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
		 */
		inlineNode(node) {
			if (node.nodeType === Node.TEXT_NODE)
				return this.text(node);
			if (!(node instanceof HTMLElement))
				return "";
			if (node.matches(".katex-display, .katex"))
				return this.math(node);
			switch (node.tagName) {
				case "BR":
					// A markdown hard line break.
					return "  \n";
				case "STRONG":
				case "B":
					return wrap(this.inline(node), "**");
				case "EM":
				case "I":
					return wrap(this.inline(node), "*");
				case "DEL":
				case "S":
					return wrap(this.inline(node), "~~");
				case "CODE":
					return codeSpan(this.rawText(node));
				case "A":
					return this.link(node);
				case "IMG":
					return `![${node.alt}](${node.getAttribute("src") ?? ""})`;
				case "INPUT":
					// A task list item's checkbox, followed by a space.
					return node.type !== "checkbox" ? "" : node.checked ? "[x]" : "[ ]";
				default:
					return this.inline(node);
			}
		}

		/**
		 * @param {HTMLAnchorElement} link
		 * @returns {string} the selected part of `link`, as a markdown link,
		 *   or as just its text when it has no address, when its selected
		 *   text is only whitespace, or when GFM makes the same link of that
		 *   text by itself
		 */
		link(link) {
			const text = this.inline(link);
			const href = link.getAttribute("href");
			if (!href || text.trim() === "")
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
