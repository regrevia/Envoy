"""ASR checkpoints follow the bytes and never lose a confirmed cursor.

A1: the identity must not contain the temporary download path, so identical
bytes under another directory resume, a trusted revision wins, and a changed
revision misses. A2: a resumed run initializes its cursor from the checkpoint
and a round that produces no new chunk (silence, timeout, worker failure) must
leave the stored progress alone.
"""

import json
import queue
import threading
import time

import pytest

from envoy_cli import media


@pytest.fixture()
def asr_home(tmp_path, monkeypatch):
    directory = tmp_path / "asr"
    monkeypatch.setenv("ENVOY_ASR_CHECKPOINT_DIR", str(directory))
    return directory


# --- a scripted worker, no whisper and no real child process ---------------


class _FakeConnection:
    def __init__(self, inbox: queue.Queue):
        self._inbox = inbox
        self.closed = False

    def send(self, payload):
        self._inbox.put(payload)

    def recv(self):
        try:
            return self._inbox.get_nowait()
        except queue.Empty:
            raise EOFError from None

    def close(self):
        self.closed = True


class _FakeSentinel:
    pass


class _FakeProcess:
    def __init__(self, target, args, daemon=False):
        self._target = target
        self._args = args
        self.daemon = daemon
        self.sentinel = _FakeSentinel()
        self.done = threading.Event()
        self.terminated = False
        self._thread = None

    def start(self):
        def run():
            try:
                self._target(*self._args)
            finally:
                self.done.set()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class _FakeContext:
    def __init__(self):
        self.inbox = queue.Queue()
        self.process = None

    def Pipe(self, duplex=False):
        return _FakeConnection(self.inbox), _FakeConnection(self.inbox)

    def Process(self, target, args, daemon=False):
        self.process = _FakeProcess(target, args, daemon=daemon)
        return self.process


def _install_worker(monkeypatch, script):
    """Run `script()` as the ASR worker; it receives the send connection."""

    def fake_worker(audio_path, model_name, download_root, start, end, connection):
        script(start, end, connection)

    context = _FakeContext()
    monkeypatch.setattr(media.multiprocessing, "get_context", lambda method: context)
    monkeypatch.setattr(media, "_whisper_worker", fake_worker)

    def fake_wait(tokens, timeout=None):
        deadline = time.monotonic() + float(timeout or 0)
        receive_connection = tokens[0]
        while time.monotonic() <= deadline:
            if not context.inbox.empty():
                return [receive_connection]
            if context.process is not None and context.process.done.is_set():
                return [receive_connection, tokens[1]]
            time.sleep(0.002)
        return []

    monkeypatch.setattr(media, "wait_for_connections", fake_wait)
    return context


def _identity_script():
    def script(start, end, connection):
        connection.send({"event": "chunk", "start_seconds": start, "end_seconds": 75.0,
                         "language": "zh", "segments": []})
        connection.send({"event": "complete", "end_seconds": 75.0, "language": "zh"})

    return script


def _audio(tmp_path, name, payload=b"same-bytes"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _stored_checkpoint(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- A1: identity ----------------------------------------------------------


def test_identity_does_not_follow_the_temp_path(tmp_path):
    first = _audio(tmp_path / "one", "a.m4a")
    second = _audio(tmp_path / "two", "b.m4a")
    assert media._asr_identity(first, "base", 0.0, 60.0) == media._asr_identity(
        second, "base", 0.0, 60.0
    )

    second.write_bytes(b"different-bytes")
    assert media._asr_identity(first, "base", 0.0, 60.0) != media._asr_identity(
        second, "base", 0.0, 60.0
    )


def test_identity_prefers_a_trusted_revision(tmp_path):
    first = _audio(tmp_path, "a.m4a", b"one")
    second = _audio(tmp_path, "b.m4a", b"two")

    same = media._asr_identity(first, "base", 0.0, 60.0, source_revision="video:abc:60")
    resigned = media._asr_identity(second, "base", 0.0, 60.0, source_revision="video:abc:60")
    assert same == resigned, "a re-signed URL over the same media must reuse the work"

    changed = media._asr_identity(first, "base", 0.0, 60.0, source_revision="video:def:60")
    assert changed != same, "a new source revision must not resume old progress"


# --- A2: cursor ------------------------------------------------------------


def _worker(source, tmp_path, monkeypatch, script, *, resume_from, end=100.0,
            prior_segments=(), prior_language="zh", timeout=2):
    _install_worker(monkeypatch, script)
    checkpoint = tmp_path / "work.json"
    return media._transcribe_with_worker(
        source,
        "base",
        tmp_path,
        0.0,
        end,
        100.0,
        timeout,
        time.monotonic(),
        checkpoint_path=checkpoint,
        prior_segments=list(prior_segments),
        resume_from=resume_from,
        prior_language=prior_language,
        checkpoint_identity="identity-under-test",
    ), checkpoint


def test_a_resumed_timeout_keeps_its_cursor(tmp_path, asr_home, monkeypatch):
    source = _audio(tmp_path, "a.m4a")
    audio, checkpoint = _worker(
        source,
        tmp_path,
        monkeypatch,
        lambda start, end, connection: time.sleep(1.2),
        resume_from=60.0,
        timeout=1,
        prior_segments=[{"start": 0.0, "end": 5.0, "text": "first"}],
    )

    assert audio["next_seconds"] == 60.0, "resume must not fall back to the range start"
    assert audio["status"] == "partial"
    assert audio["language"] == "zh"
    # A round with nothing new leaves the previous checkpoint alone; a fresh
    # range that produced nothing has nothing worth storing.
    if checkpoint.exists():
        stored = _stored_checkpoint(checkpoint)
        assert stored["next_seconds"] == 60.0
        assert stored["language"] == "zh"
        assert stored["segments"][0]["text"] == "first"


def test_no_new_chunk_keeps_the_old_checkpoint(tmp_path, asr_home, monkeypatch):
    source = _audio(tmp_path, "a.m4a")
    checkpoint = asr_home / "work.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(
        json.dumps({
            "audio": "old-temp-path",
            "model": "base",
            "start": 0.0,
            "end": 100.0,
            "next_seconds": 60.0,
            "language": "zh",
            "segments": [{"start": 0.0, "end": 5.0, "text": "first"}],
            "status": "partial",
        }),
        encoding="utf-8",
    )

    before = _stored_checkpoint(checkpoint)
    audio, _ = _worker(
        source,
        tmp_path,
        monkeypatch,
        lambda start, end, connection: None,
        resume_from=60.0,
        prior_segments=[{"start": 0.0, "end": 5.0, "text": "first"}],
    )

    assert audio["next_seconds"] == 60.0
    assert _stored_checkpoint(checkpoint) == before, "an empty round must not rewrite progress"


def test_a_silent_chunk_still_records_progress(tmp_path, asr_home, monkeypatch):
    source = _audio(tmp_path, "a.m4a")

    def script(start, end, connection):
        connection.send({"event": "chunk", "start_seconds": start, "end_seconds": 75.0,
                         "language": "zh", "segments": []})

    audio, checkpoint = _worker(source, tmp_path, monkeypatch, script, resume_from=60.0)

    assert audio["next_seconds"] == 75.0
    assert audio["status"] == "partial"
    stored = _stored_checkpoint(checkpoint)
    assert stored["next_seconds"] == 75.0


def test_a_worker_failure_does_not_roll_back(tmp_path, asr_home, monkeypatch):
    source = _audio(tmp_path, "a.m4a")

    def script(start, end, connection):
        connection.send({"event": "chunk", "start_seconds": start, "end_seconds": 70.0,
                         "language": "zh", "segments": []})
        connection.send({"event": "failed", "error": "boom"})

    audio, _ = _worker(source, tmp_path, monkeypatch, script, resume_from=60.0)

    assert audio["next_seconds"] == 70.0


# --- A1 + A2 through the public call --------------------------------------


@pytest.fixture()
def whisper_installed(monkeypatch):
    monkeypatch.setattr(
        media.importlib.util, "find_spec", lambda name: object() if name == "whisper" else None
    )


def test_identical_bytes_in_two_temp_dirs_hit_the_same_checkpoint(
    tmp_path, asr_home, monkeypatch, whisper_installed
):
    first = _audio(tmp_path / "run-one", "audio.m4a", b"stable-audio")
    second = _audio(tmp_path / "run-two", "audio.m4a", b"stable-audio")
    monkeypatch.setattr(media, "probe_media_duration", lambda *a, **k: 100.0)

    checkpoint = asr_home / f"{media._asr_identity(first, 'base', 0.0, 100.0)}.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(
        json.dumps({
            "audio": str(first),
            "model": "base",
            "start": 0.0,
            "end": 100.0,
            "next_seconds": 100.0,
            "language": "zh",
            "segments": [{"start": 0.0, "end": 5.0, "text": "cached"}],
            "status": "complete",
        }),
        encoding="utf-8",
    )

    result = media.transcribe_audio_file(second, end_seconds=100.0)

    assert result["cached"] is True
    assert result["segments"][0]["text"] == "cached"


def test_a_changed_source_revision_rejects_old_progress(
    tmp_path, asr_home, monkeypatch, whisper_installed
):
    source = _audio(tmp_path, "audio.m4a", b"revised-audio")
    monkeypatch.setattr(media, "probe_media_duration", lambda *a, **k: 100.0)
    old_identity = media._asr_identity(source, "base", 0.0, 100.0, source_revision="video:old")
    checkpoint = asr_home / f"{old_identity}.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(
        json.dumps({
            "identity": old_identity,
            "model": "base",
            "start": 0.0,
            "end": 100.0,
            "next_seconds": 100.0,
            "language": "zh",
            "segments": [{"start": 0.0, "end": 5.0, "text": "stale"}],
            "status": "complete",
        }),
        encoding="utf-8",
    )
    seen = {}

    def fake_worker(*args, **kwargs):
        seen.update(kwargs)
        return {"status": "partial", "segments": [], "next_seconds": 0.0, "language": "zh",
                "duration_seconds": 100.0, "model": "base", "elapsed_ms": 1}

    monkeypatch.setattr(media, "_transcribe_with_worker", fake_worker)

    result = media.transcribe_audio_file(source, end_seconds=100.0, source_revision="video:new")

    assert result["cached"] is False
    assert seen["prior_segments"] == [], "progress from another revision must not be carried"
    assert seen["checkpoint_identity"] == media._asr_identity(
        source, "base", 0.0, 100.0, source_revision="video:new"
    )
