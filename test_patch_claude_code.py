"""Check that patch-claude-code, when interrupted while writing a file,
leaves no half-written file that a later run would trust."""

import importlib.machinery
import importlib.util
import os
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


class Interrupted(BaseException):
	"""The simulated interruption. Like KeyboardInterrupt, it is not an
	Exception, but pytest does not take it as the user stopping the tests."""


def interrupt_write(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
	"""Make the patcher's next write of `path` raise Interrupted once that
	write's data is in the .patching file but before it is renamed over
	`path`, as if the user hit Ctrl-C there."""
	temp = str(path.resolve().with_name(path.name + patcher.TEMP_SUFFIX))
	real_fsync = patcher.os.fsync
	def fsync(fd: int) -> None:
		if os.readlink(f"/proc/self/fd/{fd}") == temp:
			raise Interrupted
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


def assert_no_temp_files(directory: Path) -> None:
	assert not list(directory.glob(f"*{patcher.TEMP_SUFFIX}"))


def test_interrupted_backup_write_leaves_no_backup(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	interrupt_write(monkeypatch, backup_of(target))
	with pytest.raises(Interrupted):
		patcher.patch_file(target, PATTERNS)
	assert not backup_of(target).exists()
	assert target.read_bytes() == ORIGINAL
	assert_no_temp_files(target.parent)

	monkeypatch.undo()
	assert patcher.patch_file(target, PATTERNS)
	assert backup_of(target).read_bytes() == ORIGINAL
	assert target.read_bytes() == PATCHED


def test_interrupted_patched_write_leaves_original_whole(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	interrupt_write(monkeypatch, target)
	with pytest.raises(Interrupted):
		patcher.patch_file(target, PATTERNS)
	assert backup_of(target).read_bytes() == ORIGINAL
	assert target.read_bytes() == ORIGINAL
	assert_no_temp_files(target.parent)

	monkeypatch.undo()
	assert patcher.patch_file(target, PATTERNS)
	assert target.read_bytes() == PATCHED


def test_interrupted_undo_keeps_backup(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	assert patcher.patch_file(target, PATTERNS)
	interrupt_write(monkeypatch, target)
	with pytest.raises(Interrupted):
		patcher.undo_dir(target.parent)
	assert backup_of(target).read_bytes() == ORIGINAL
	assert target.read_bytes() == PATCHED
	assert_no_temp_files(target.parent)

	monkeypatch.undo()
	patcher.undo_dir(target.parent)
	assert not backup_of(target).exists()
	assert target.read_bytes() == ORIGINAL


def test_write_atomically_keeps_permission_bits(target: Path) -> None:
	target.chmod(0o600)
	patcher.write_atomically(target, PATCHED)
	assert target.read_bytes() == PATCHED
	assert target.stat().st_mode & 0o777 == 0o600


def test_patching_keeps_line_endings(tmp_path: Path) -> None:
	path = tmp_path / "index.js"
	path.write_bytes(b"let size=13;\r\nlet x;\r")
	assert patcher.patch_file(path, PATTERNS)
	assert path.read_bytes() == b"let size=12;\r\nlet x;\r"
