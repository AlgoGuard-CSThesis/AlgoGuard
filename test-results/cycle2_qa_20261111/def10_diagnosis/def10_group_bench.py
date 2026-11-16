"""DEF-10 hypothesis test: one durable transaction per event vs one per group of waiting events.

Same pragmas as the spool (journal_mode=delete, synchronous=full), 300 rows of ~1.6 KB,
quiet and with a pure-Python busy thread in the process. Temporary folder only.
"""
import os
import sqlite3
import tempfile
import threading
import time

ROW = "x" * 1600


def write(path, group):
    db = sqlite3.connect(path, timeout=1)
    db.execute("pragma journal_mode=delete")
    db.execute("pragma synchronous=full")
    db.execute("create table if not exists event(id integer primary key, payload text)")
    start = time.perf_counter()
    for first in range(0, 300, group):
        with db:
            for _ in range(min(group, 300 - first)):
                db.execute("insert into event(payload) values(?)", (ROW,))
    elapsed = time.perf_counter() - start
    db.close()
    return elapsed


def busy(stop):
    total = 0
    while not stop.is_set():
        for i in range(2000):
            total += i * i


def main():
    with tempfile.TemporaryDirectory() as folder:
        for label, busy_on in (("quiet", False), ("busy thread", True)):
            stop = threading.Event()
            if busy_on:
                threading.Thread(target=busy, args=(stop,), daemon=True).start()
            for group in (1, 10, 50):
                path = os.path.join(folder, f"{label}-{group}.sqlite3")
                elapsed = write(path, group)
                print(f"{label:12s} group {group:3d}: 300 rows in {elapsed:6.2f} s "
                      f"({300 / elapsed:7.1f} rows/s)")
            stop.set()
            time.sleep(0.1)


if __name__ == "__main__":
    main()
