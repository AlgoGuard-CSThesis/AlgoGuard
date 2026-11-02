"""A loopback relay to the local stack that breaks chosen requests on purpose (Stage 6C).

Each connection carries one HTTP request (urllib sends ``Connection: close``). A rule matched by
path prefix decides what happens to the next matching request:

* ``refuse``   - read the request and close: nothing reaches the cloud (failure before commit);
* ``truncate`` - send half of the request body and close: the cloud never sees a whole request;
* ``drop``     - deliver the request, read the cloud's answer, close without passing it on
  (committed, acknowledgement lost);
* ``hold``     - like ``drop``, but keep the client waiting until ``release()``;
* ``stall``    - accept and never answer until ``release()`` (a stalled network).

``latency`` adds a delay before forwarding and before answering, to imitate a distant cloud.
"""

from __future__ import annotations

import socket
import threading
import time
from urllib.parse import urlsplit


def _read_request(conn):
    """(head, body) of one HTTP request, or None when the client closed first."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return None
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())
    while len(body) < length:
        chunk = conn.recv(65536)
        if not chunk:
            break
        body += chunk
    return head, body


class FaultProxy:
    def __init__(self, upstream_url, *, latency=0.0):
        parts = urlsplit(upstream_url)
        self.upstream = (parts.hostname, parts.port or 80)
        self.latency = latency
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.lock = threading.Lock()
        self.rules = []
        self.log = []
        self.waiting = 0
        self.released = threading.Event()
        self.closed = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def rule(self, path, action, times=1):
        with self.lock:
            self.rules.append({"path": path, "action": action, "times": times})

    def clear(self):
        with self.lock:
            self.rules.clear()

    def release(self):
        self.released.set()

    def actions(self, path):
        with self.lock:
            return [action for logged, action in self.log if logged.startswith(path)]

    def close(self):
        self.closed = True
        self.release()
        self.listener.close()

    def _take(self, path):
        with self.lock:
            for rule in self.rules:
                if path.startswith(rule["path"]):
                    rule["times"] -= 1
                    if rule["times"] <= 0:
                        self.rules.remove(rule)
                    self.log.append((path, rule["action"]))
                    return rule["action"]
            self.log.append((path, "pass"))
            return "pass"

    def _serve(self):
        while not self.closed:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _wait(self):
        with self.lock:
            self.waiting += 1
        self.released.wait(120)

    def _handle(self, conn):
        try:
            request = _read_request(conn)
            if request is None:
                return
            head, body = request
            path = head.split(b"\r\n", 1)[0].split(b" ")[1].decode()
            action = self._take(path)
            if action == "refuse":
                return
            if action == "stall":
                self._wait()
                return
            if self.latency:
                time.sleep(self.latency)
            with socket.create_connection(self.upstream, timeout=30) as upstream:
                if action == "truncate":
                    upstream.sendall(head + b"\r\n\r\n" + body[: len(body) // 2])
                    return
                upstream.sendall(head + b"\r\n\r\n" + body)
                response = b""
                while chunk := upstream.recv(65536):
                    response += chunk
            if action == "drop":
                return
            if action == "hold":
                self._wait()
                return
            if self.latency:
                time.sleep(self.latency)
            conn.sendall(response)
        except OSError:
            pass
        finally:
            conn.close()


def route_through(auth, proxy):
    """Send this CloudAuth's repository traffic through the relay; tokens still verify against
    the real project URL, so the issuer check is unchanged."""
    original = auth._transport

    def routed(*args, **kwargs):
        repo = original(*args, **kwargs)
        repo.base_url = proxy.url
        return repo

    auth._transport = routed
    return auth
