from pathlib import Path

import pytest

from aichaind.main import acquire_single_instance, release_single_instance


def test_acquire_single_instance_writes_pid(monkeypatch, tmp_path):
    pid_file = tmp_path / "aichaind.pid"
    monkeypatch.setattr("aichaind.main.os.getpid", lambda: 1234)
    monkeypatch.setattr("aichaind.main._pid_exists", lambda pid: False)

    class _Log:
        def info(self, *_args, **_kwargs):
            return None

    acquire_single_instance(pid_file, _Log())
    assert pid_file.read_text(encoding="utf-8") == "1234"


def test_acquire_single_instance_rejects_running_process(monkeypatch, tmp_path):
    pid_file = tmp_path / "aichaind.pid"
    pid_file.write_text("5678", encoding="utf-8")
    monkeypatch.setattr("aichaind.main.os.getpid", lambda: 1234)
    monkeypatch.setattr("aichaind.main._pid_exists", lambda pid: pid == 5678)

    class _Log:
        def info(self, *_args, **_kwargs):
            return None

    with pytest.raises(RuntimeError, match="another aichaind instance"):
        acquire_single_instance(pid_file, _Log())


def test_release_single_instance_only_removes_owned_pid(monkeypatch, tmp_path):
    pid_file = tmp_path / "aichaind.pid"
    pid_file.write_text("1234", encoding="utf-8")
    monkeypatch.setattr("aichaind.main.os.getpid", lambda: 1234)
    release_single_instance(pid_file)
    assert not pid_file.exists()

    pid_file.write_text("9999", encoding="utf-8")
    release_single_instance(pid_file)
    assert pid_file.exists()

def test_pid_exists_treats_windows_systemerror_as_not_running(monkeypatch):
    from aichaind.main import _pid_exists

    def _raise(_pid, _sig):
        raise SystemError('kill returned a result with an exception set')

    monkeypatch.setattr('aichaind.main.os.kill', _raise)

    assert _pid_exists(5678) is False
