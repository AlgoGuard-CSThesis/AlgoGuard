"""Live-capture inference in a separate process (DEF-17).

CPython runs one thread at a time. A Stacking prediction keeps the interpreter for 60-140 ms,
and while it does, the capture and flow-tracking threads wait, so flows finish late and verdicts
arrive late (p95 513-914 ms at 80-260 packets/s, 2026-10-26). A live session therefore scores
flows in a child process: the monitor only waits for the answer, which frees the interpreter for
the capture threads (p95 336-379 ms in the same conditions).

The child is this module run as ``python -m services.inference_worker``, not ``multiprocessing``,
whose Windows start method would re-run the application's start-up script. It talks over its
own stdin/stdout pipes in length-prefixed pickles, which only the two processes can read. The
pipeline it receives is the one the monitor already loaded and verified.
"""

import pickle
import struct
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_HEADER = struct.Struct("!Q")
START_TIMEOUT_SECONDS = 60.0


class InferenceWorkerError(RuntimeError):
    """The inference process could not start or stopped answering."""


def _send(stream, value):
    data = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_HEADER.pack(len(data)))
    stream.write(data)
    stream.flush()


def _receive(stream):
    header = stream.read(_HEADER.size)
    if len(header) < _HEADER.size:
        raise EOFError("The other process closed the pipe.")
    (size,) = _HEADER.unpack(header)
    data = stream.read(size)
    if len(data) < size:
        raise EOFError("The other process closed the pipe.")
    # Only ever data from the process pair that created this pipe.
    return pickle.loads(data)


class InferenceWorker:
    """One child process that scores batches of flow rows with a given pipeline."""

    def __init__(self, pipeline, feature_columns):
        self._lock = threading.Lock()
        self._closed = False
        self._process = subprocess.Popen(
            [sys.executable, "-m", "services.inference_worker"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        timer = threading.Timer(START_TIMEOUT_SECONDS, self._process.kill)
        timer.start()
        try:
            _send(self._process.stdin, (pipeline, list(feature_columns)))
            ready = _receive(self._process.stdout)
        except (OSError, EOFError, pickle.PickleError) as error:
            self.close()
            raise InferenceWorkerError(f"The inference process did not start: {error}") from None
        finally:
            timer.cancel()
        if ready != "ready":
            self.close()
            raise InferenceWorkerError(f"The inference process did not start: {ready}")

    def score(self, rows):
        """Same result as ``scoring_service.score_rows``: verdicts and model milliseconds."""
        with self._lock:
            if self._closed:
                raise InferenceWorkerError("The inference process is closed.")
            try:
                _send(self._process.stdin, rows)
                reply = _receive(self._process.stdout)
            except (OSError, EOFError, pickle.PickleError):
                raise InferenceWorkerError("The inference process stopped answering.") from None
        if isinstance(reply, tuple) and len(reply) == 2 and reply[0] == "error":
            raise InferenceWorkerError(f"The inference process failed: {reply[1]}")
        return reply

    def close(self):
        if self._closed:
            return
        self._closed = True
        process = self._process
        try:
            if process.stdin is not None:
                _send(process.stdin, None)
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            process.kill()
            process.wait(timeout=2)
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:  # unsent bytes to a process that has gone
                        pass


class LiveScorer:
    """Score in the inference process; fall back to this process if it is unavailable.

    The fallback keeps a session classifying: verdicts are only slower under load.
    ``fallback_reason`` says why, for the session log.
    """

    def __init__(self, pipeline, feature_columns, score_here):
        self.pipeline = pipeline
        self.feature_columns = feature_columns
        self._score_here = score_here
        self.worker = None
        self.fallback_reason = None
        try:
            self.worker = InferenceWorker(pipeline, feature_columns)
        except InferenceWorkerError as error:
            self.fallback_reason = str(error)

    def score(self, rows):
        if self.worker is not None:
            try:
                return self.worker.score(rows)
            except InferenceWorkerError as error:
                self.worker.close()
                self.worker = None
                self.fallback_reason = str(error)
        return self._score_here(self.pipeline, rows, self.feature_columns)

    def close(self):
        if self.worker is not None:
            self.worker.close()
            self.worker = None


def _serve():
    """Child side: receive the pipeline, then answer batches until told to stop."""
    requests, replies = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # nothing else may write into the reply stream
    from services.scoring_service import score_rows

    pipeline, feature_columns = _receive(requests)
    _send(replies, "ready")
    while True:
        try:
            rows = _receive(requests)
        except EOFError:
            return
        if rows is None:
            return
        try:
            reply = score_rows(pipeline, rows, feature_columns)
        except Exception as error:
            reply = ("error", f"{type(error).__name__}: {error}")
        _send(replies, reply)


if __name__ == "__main__":
    _serve()
