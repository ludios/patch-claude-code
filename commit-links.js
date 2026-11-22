// Turn each commit ID in the chat into a link that shows the commit in
// VS Code, set as inline code (as a commit ID in backticks is).
// patch-claude-code puts this function expression into both the webview's
// index.js and extension.js, and calls it with the scheme of the links'
// addresses (such as "git-commit:"). The webview uses the remarkPlugin
// that the call returns, and the extension uses its open function.
//
// A commit ID is 7 to 40 of 0-9 and a-f, with at least one of 0-9 and at
// least one of a-f (so that numbers and words like "defaced" are not
// taken for one), and with no word character, "-", "/", "\" or "#" right
// before or after it. A "." may come right before it only as the end of
// "..", and right after it only when no word character follows the ".".
// So "11d4b47..86638da" holds two IDs, while a UUID, a hex color, a file
// name and a path hold none. Only the text of one mdast node (a text node
// or inline code) is looked at, so a path that markup splits, such as
// "/tmp/*abc1234*", can hold an ID.
// oxlint-disable-next-line no-unused-expressions -- patch-claude-code calls it
((scheme) => {
	const COMMIT_ID = /(?<![\w#/\\-]|(?<!\.)\.)(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![\w#/\\-]|\.\w)/g;
	// The mdast node types whose text is already the text of a link.
	const LINKED = new Set(["link", "linkReference"]);

	/**
	 * @param {string} text
	 * @returns {(string | {id: string})[]} `text` cut into the commit IDs in
	 *   it ({id}) and the text around them (strings, never empty), in order
	 */
	function cut(text) {
		const pieces = [];
		let end = 0;
		for (const match of text.matchAll(COMMIT_ID)) {
			if (match.index > end)
				pieces.push(text.slice(end, match.index));
			pieces.push({id: match[0]});
			end = match.index + match[0].length;
		}
		if (end < text.length)
			pieces.push(text.slice(end));
		return pieces;
	}

	/**
	 * @param {string} id  a commit ID
	 * @returns {object} a hast link to `scheme` + `id`, whose text is `id`
	 */
	function hastLink(id) {
		return {type: "element", tagName: "a", properties: {href: scheme + id}, children: [{type: "text", value: id}]};
	}

	/**
	 * Replace each commit ID in the text under `parent`, an mdast node with
	 * children, with a hast link to `scheme` + the ID, inside the code
	 * element that inline code becomes: in a text node, the ID becomes
	 * inline code of its own; in inline code, the ID's link goes in among
	 * the rest of the code's text. The text of links stays as it is.
	 *
	 * @param {{children: object[]}} parent
	 */
	function linkCommits(parent) {
		parent.children = parent.children.flatMap((node) => {
			if (node.type === "text") {
				const pieces = cut(node.value);
				if (pieces.every((piece) => typeof piece === "string"))
					return [node];
				return pieces.map((piece) => typeof piece === "string"
					? {type: "text", value: piece}
					: {type: "inlineCode", value: piece.id, data: {hChildren: [hastLink(piece.id)]}});
			}
			if (node.type === "inlineCode") {
				// As mdast-util-to-hast makes the code element's text.
				const pieces = cut(node.value.replace(/\r?\n|\r/g, " "));
				if (pieces.some((piece) => typeof piece !== "string")) {
					const hChildren = pieces.map((piece) => typeof piece === "string"
						? {type: "text", value: piece}
						: hastLink(piece.id));
					node.data = {...node.data, hChildren};
				}
				return [node];
			}
			if (node.children && !LINKED.has(node.type))
				linkCommits(node);
			return [node];
		});
	}

	/**
	 * A remark plugin: it links each commit ID in the document (see
	 * linkCommits).
	 *
	 * @returns {(tree: {children: object[]}) => void}
	 */
	function remarkPlugin() {
		return linkCommits;
	}

	/**
	 * Show the commit that a link's address names, in the editor that Git's
	 * "Open Commit" opens (the commit's changes, in a multi-diff editor),
	 * from the first of the repositories that Git has open in the window
	 * that has exactly one commit whose ID starts with the link's ID, trying
	 * the repository that holds `cwd` (if Git has it open) first. Tell the
	 * user when no commit is shown, and why.
	 *
	 * @param {typeof import("vscode")} vscode  the extension's VS Code API
	 * @param {string} cwd  the directory that the chat's sessions run in
	 * @param {string} url  a link's address, which starts with `scheme`
	 * @returns {Promise<boolean>} whether the command that shows the commit
	 *   ran
	 */
	async function open(vscode, cwd, url) {
		if (!url.startsWith(scheme))
			throw new Error(`not a commit link's address: ${url}`);
		const id = url.slice(scheme.length);
		// Any markdown can hold a link like this, and Git takes the ID as an
		// argument (where "--output=FILE", say, would be an option).
		if (!/^[0-9a-f]{7,40}$/.test(id)) {
			vscode.window.showWarningMessage(`Not a commit ID: ${JSON.stringify(id)}`);
			return false;
		}
		let repositories;
		try {
			// getAPI throws when Git is disabled (git.enabled).
			const api = (await vscode.extensions.getExtension("vscode.git")?.activate())?.getAPI(1);
			const own = api?.getRepository(vscode.Uri.file(cwd))?.rootUri.fsPath;
			// The own repository first; the rest in Git's order.
			repositories = (api?.repositories ?? []).toSorted((a, b) => (b.rootUri.fsPath === own) - (a.rootUri.fsPath === own));
		} catch (error) {
			vscode.window.showWarningMessage(`Cannot look up commit ${id}: ${error}`);
			return false;
		}
		if (repositories.length === 0) {
			vscode.window.showWarningMessage(`Cannot look up commit ${id}: Git has no repository open in this window.`);
			return false;
		}
		for (const repository of repositories) {
			let commit;
			try {
				// "^{commit}" makes Git fail for an ID that is not a commit's
				// (or an annotated tag's) instead of showing that object.
				commit = await repository.getCommit(id + "^{commit}");
			} catch {
				// The ID names no commit, or more than one.
				continue;
			}
			// Git takes a branch or tag with that name ahead of the commit.
			if (!commit.hash.startsWith(id))
				continue;
			try {
				await vscode.commands.executeCommand("git.viewCommit", repository.rootUri.fsPath, commit.hash);
			} catch (error) {
				vscode.window.showWarningMessage(`Cannot show commit ${id}: ${error}`);
				return false;
			}
			return true;
		}
		vscode.window.showWarningMessage(`No Git repository open in this window has exactly one commit whose ID starts with ${id}.`);
		return false;
	}

	return {remarkPlugin, open};
})
