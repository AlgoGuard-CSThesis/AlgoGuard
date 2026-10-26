"""Live packet capture in a separate process (DEF-17).

Decoding a packet with Scapy is Python work. In the application's process it waited for its turn on
the interpreter behind flow tracking and the monitor, so during page-load bursts packets reached
the tracker up to about 175 ms late (p95, 2026-10-26). This child captures and decodes packets on
its own interpreter and sends the tracker compact ``PacketInfo`` batches every few milliseconds,
plus a short heartbeat while the network is quiet so held and idle flows are released on time.

The child is this module run as ``python -m services.capture_worker``, talking over its own
stdin/stdout pipes (``services.process_pipe``) like ``services.inference_worker``.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from services.process_pipe import PIPE_ERRORS, receive, send

ROOT = Path(__file__).resolve().parents[1]
BATCH_SECONDS = 0.005
HEARTBEAT_SECONDS = 0.02
START_TIMEOUT_SECONDS = 30.0
MAX_BUFFERED = 100_000


class CaptureWorkerError(RuntimeError):
    """The capture process could not start, or stopped."""


class CaptureWorker:
    """One child process capturing on an interface; ``receive()`` returns its next frame.

    Frames are ``("packets", infos, ignored_ts, dropped)``: decoded IP packets, the time of the last
    non-IP packet (it still advances capture time), and packets dropped because the parent fell
    behind. A failure after start-up arrives as ``CaptureWorkerError`` from ``receive()``.
    """

    def __init__(self, interface, bpf_filter):
        self._process = subprocess.Popen(
            [sys.executable, "-m", "services.capture_worker"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._closed = False
        timer = threading.Timer(START_TIMEOUT_SECONDS, self._process.kill)
        timer.start()
        try:
            send(self._process.stdin, {"interface": interface, "filter": bpf_filter or None})
            frame = receive(self._process.stdout)
        except PIPE_ERRORS as error:
            self.close()
            raise CaptureWorkerError(f"the capture process did not start ({error})") from None
        finally:
            timer.cancel()
        if frame[0] != "ready":
            self.close()
            raise CaptureWorkerError(frame[1] if len(frame) > 1 else "capture did not start")

    def receive(self):
        try:
            frame = receive(self._process.stdout)
        except PIPE_ERRORS:
            raise CaptureWorkerError("the capture process stopped") from None
        if frame[0] == "error":
            raise CaptureWorkerError(frame[1])
        return frame

    def close(self, wait=True):
        if self._closed:
            return
        self._closed = True
        process = self._process
        try:
            if process.stdin is not None:
                process.stdin.close()  # end of input tells the child to stop
            process.wait(timeout=2 if wait else 0.1)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            process.kill()
        finally:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except OSError:
                    pass


def _serve():
    """Child side: capture until the parent closes our stdin."""
    requests, replies = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # nothing else may write into the reply stream
    config = receive(requests)

    from scapy.layers import inet, l2  # noqa: F401  (decoders for Ether/IP frames)
    from scapy.sendrecv import AsyncSniffer

    from services.flow_tracker_service import packet_info_from_scapy

    lock = threading.Lock()
    pending = {"infos": [], "ignored_ts": None, "dropped": 0}

    def on_packet(packet):
        info = packet_info_from_scapy(packet)
        with lock:
            if info is None:
                pending["ignored_ts"] = float(packet.time)
            elif len(pending["infos"]) < MAX_BUFFERED:
                pending["infos"].append(info)
            else:
                pending["dropped"] += 1

    options = {"iface": config["interface"] or None, "prn": on_packet, "store": False}
    if config["filter"]:
        options["filter"] = config["filter"]
    try:
        sniffer = AsyncSniffer(**options)
        sniffer.start()
        time.sleep(0.3)
    except Exception as error:
        send(replies, ("error", str(error)))
        return
    failure = getattr(sniffer, "exception", None)
    thread = getattr(sniffer, "thread", None)
    if failure is not None or (thread is not None and not thread.is_alive()):
        send(replies, ("error", str(failure or "capture thread exited")))
        return
    send(replies, ("ready",))

    stop = threading.Event()

    def wait_for_parent():
        requests.read()  # returns when the parent closes the pipe or exits
        stop.set()

    threading.Thread(target=wait_for_parent, daemon=True).start()
    last_sent = time.monotonic()
    try:
        while not stop.wait(BATCH_SECONDS):
            failure = getattr(sniffer, "exception", None)
            if failure is not None or (thread is not None and not thread.is_alive()):
                send(replies, ("error", str(failure or "capture thread exited")))
                return
            with lock:
                infos, ignored_ts, dropped = (pending["infos"], pending["ignored_ts"],
                                              pending["dropped"])
                pending.update(infos=[], ignored_ts=None, dropped=0)
            now = time.monotonic()
            if infos or ignored_ts is not None or dropped or now - last_sent >= HEARTBEAT_SECONDS:
                send(replies, ("packets", infos, ignored_ts, dropped))
                last_sent = now
    except PIPE_ERRORS:
        return  # the parent has gone
    finally:
        try:
            sniffer.stop()
        except Exception:
            pass


if __name__ == "__main__":
    _serve()
