"""Check that patch-claude-code, when interrupted while writing a file,
leaves no half-written file that a later run would trust."""

import importlib.machinery
import importlib.util
import re
from pathlib import Path

import pytest

PATCHER = Path(__file__).resolve().with_name("patch-claude-code")
ORIGINAL = b"let size=13;\n"
PATCHED  = b"let size=12;\n"
PATTERNS = [(re.compile(r"size=13"), "size=12")]


def load_patcher() -> object:
	"""Import the patcher script (which has no .py suffix) as a module."""
	loader = importlib.machinery.SourceFileLoader("patch_claude_code", str(PATCHER))
	module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
	loader.exec_module(module)
	return module


patcher = load_patcher()


def interrupt_fsync(monkeypatch: pytest.MonkeyPatch, call: int) -> None:
	"""Make the `call`-th (counting from 1) os.fsync from now on raise
	KeyboardInterrupt, as if the user hit Ctrl-C after a file's data was
	written but before it was renamed into place."""
	real_fsync = patcher.os.fsync
	calls = 0
	def fsync(fd: int) -> None:
		nonlocal calls
		calls += 1
		if calls == call:
			raise KeyboardInterrupt
		real_fsync(fd)
	monkeypatch.setattr(patcher.os, "fsync", fsync)


@pytest.fixture
def target(tmp_path: Path) -> Path:
	"""Return the path of an unpatched extension file."""
	path = tmp_path / "index.js"
	path.write_bytes(ORIGINAL)
	return path


def backup_of(path: Path) -> Path:
	"""Return the path of the backup that the patcher keeps of `path`."""
	return path.with_name(path.name + patcher.BACKUP_SUFFIX)


def test_interrupted_backup_write_leaves_no_backup(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	interrupt_fsync(monkeypatch, call=1)
	with pytest.raises(KeyboardInterrupt):
		patcher.patch_file(target, PATTERNS)
	assert not backup_of(target).exists()
	assert target.read_bytes() == ORIGINAL

	monkeypatch.undo()
	assert patcher.patch_file(target, PATTERNS)
	assert backup_of(target).read_bytes() == ORIGINAL
	assert target.read_bytes() == PATCHED


def test_interrupted_patched_write_leaves_original_whole(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	interrupt_fsync(monkeypatch, call=2)
	with pytest.raises(KeyboardInterrupt):
		patcher.patch_file(target, PATTERNS)
	assert backup_of(target).read_bytes() == ORIGINAL
	assert target.read_bytes() == ORIGINAL

	monkeypatch.undo()
	assert patcher.patch_file(target, PATTERNS)
	assert target.read_bytes() == PATCHED


def test_write_atomically_keeps_permission_bits(target: Path) -> None:
	target.chmod(0o600)
	patcher.write_atomically(target, PATCHED)
	assert target.read_bytes() == PATCHED
	assert target.stat().st_mode & 0o777 == 0o600
