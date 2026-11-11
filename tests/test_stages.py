"""Tests for the disposable venv and stage freezes: the guarantees that the
real Python stays untouched, and that a frozen stage survives a full wipe."""

import os

import pytest

import sandbox
import stages


def _fixture(name):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "fixtures", name)


@pytest.fixture()
def saves_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEANROOM_SAVES_ROOT", str(tmp_path / "saves"))
    return str(tmp_path / "saves")


def test_child_python_is_the_sandbox_venv():
    s = sandbox.Session(local=_fixture("benign-notes-app"))
    s.open()
    try:
        code, out, err, _ = s.execute(
            'python -c "import sys; print(sys.prefix)"', capture=True)
        assert code == 0
        prefix = out.strip().splitlines()[-1]
        assert prefix.lower().startswith(s.dir.lower()), prefix
        assert os.path.isdir(os.path.join(s.dir, "venv"))
        assert s.venv_note is None
        # pip exists and belongs to the venv
        code, out, _, _ = s.execute('pip --version', capture=True)
        assert code == 0
        assert s.dir.lower() in out.lower()
    finally:
        s.close("test")


def test_pip_targets_the_venv_not_the_host():
    s = sandbox.Session(local=_fixture("benign-notes-app"))
    s.open()
    try:
        code, out, _, _ = s.execute(
            'python -m pip --version', capture=True)
        assert code == 0
        assert "cleanroom-" in out  # the venv path inside the sandbox temp dir
    finally:
        s.close("test")


def test_stage_freeze_survives_a_full_wipe(saves_root):
    before = sandbox.residue_count()
    s = sandbox.Session(local=_fixture("benign-notes-app"))
    s.open()
    code, out, _, _ = s.execute(
        'python -c "open(\'artifact.txt\', \'w\').write(\'stage-one\')"', capture=True)
    assert code == 0
    res = stages.save(s, "roundtrip")
    assert res["ok"] and res["files"] >= 4
    target = s.dir
    s.close("between")
    assert not os.path.exists(target)
    assert sandbox.residue_count() == before

    # fresh start with the same repo: the artifact is gone
    fresh = sandbox.Session(local=_fixture("benign-notes-app"))
    fresh.open()
    try:
        code, out, _, _ = fresh.execute(
            'python -c "import os; print(os.path.exists(\'artifact.txt\'))"', capture=True)
        assert out.strip().endswith("False")
    finally:
        fresh.close("test")

    # resume: the artifact is back, package list shipped inside
    r = stages.resume("roundtrip")
    try:
        assert r.resumed_from["name"] == "roundtrip"
        code, out, _, _ = r.execute(
            'python -c "print(open(\'artifact.txt\').read())"', capture=True)
        assert "stage-one" in out
        code, out, _, _ = r.execute(
            'python -c "import os; print(os.path.exists(\'_cleanroom_stage_packages.txt\'))"',
            capture=True)
        assert out.strip().endswith("True")
    finally:
        r.close("test")


def test_stage_name_rules(saves_root):
    s = sandbox.Session(local=_fixture("benign-notes-app"))
    s.open()
    try:
        with pytest.raises(ValueError):
            stages.save(s, "../escape")
        with pytest.raises(ValueError):
            stages.save(s, "a" * 80)
    finally:
        s.close("test")


def test_list_and_forget(saves_root):
    s = sandbox.Session(local=_fixture("benign-notes-app"))
    s.open()
    stages.save(s, "keep-me")
    s.close("test")
    names = [row["name"] for row in stages.list_stages()]
    assert "keep-me" in names
    stages.forget("keep-me")
    assert "keep-me" not in [row["name"] for row in stages.list_stages()]
    with pytest.raises(stages.StageError):
        stages.forget("keep-me")  # already gone


def test_resume_missing_stage_errors(saves_root):
    with pytest.raises(stages.StageError):
        stages.resume("never-existed")
