"""AlgoGuard launcher window: set up, enter the settings, start and stop AlgoGuard.

Opened by ALGOGUARD.cmd. Standard library only; all logic is in launcher_core.py.
"""
import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import launcher_core as core

OK, NOT_OK = "#1f9d55", "#c0392b"
# Child output arrives line by line and as UTF-8, whatever the console's code page is.
CHILD_ENV = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}


def _spawn(command, folder):
    return subprocess.Popen(command, cwd=str(folder), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=CHILD_ENV, creationflags=core.NO_WINDOW)


class LauncherWindow:
    def __init__(self, root, folder):
        self.root, self.folder = root, Path(folder)
        self.process = None
        self.url = None
        self.busy = False
        self.messages = queue.Queue()
        root.title("AlgoGuard")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=14)
        frame.grid()
        ttk.Label(frame, text="AlgoGuard", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.status_labels = {}
        names = [name for name, _ in core.Status(*[core.Check(False, "")] * 5).rows()]
        for row, name in enumerate(names, start=1):
            ttk.Label(frame, text=name).grid(row=row, column=0, sticky="w", padx=(0, 12))
            label = tk.Label(frame, text="...", anchor="w", justify="left", wraplength=360)
            label.grid(row=row, column=1, columnspan=2, sticky="w")
            self.status_labels[name] = label
        actions = ttk.Frame(frame)
        actions.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 4))
        specs = [("setup", "Set up", self.set_up), ("settings", "Settings...", self.settings),
                 ("start", "Start", self.start), ("stop", "Stop", self.stop),
                 ("open", "Open AlgoGuard", self.open_browser)]
        self.buttons = {}
        for column, (key, text, command) in enumerate(specs):
            self.buttons[key] = tk.Button(actions, text=text, width=14, command=command)
            self.buttons[key].grid(row=0, column=column, padx=(0, 6))
        self.buttons["admin"] = tk.Button(
            frame, text="Restart as administrator (for live capture)", command=self.elevate)
        self.buttons["admin"].grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 8))
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=520)
        self.progress.grid(row=9, column=0, columnspan=3, sticky="we")
        self.progress.grid_remove()  # shown only while Set up works
        self.log = scrolledtext.ScrolledText(frame, width=74, height=12, state="disabled",
                                             font=("Consolas", 9))
        self.log.grid(row=10, column=0, columnspan=3, pady=(8, 0))
        self.refresh()
        root.after(200, self.pump)

    # -- display ---------------------------------------------------------------------
    def show_progress(self, working):
        """A moving bar while Set up works, nothing otherwise; a full bar left looks stuck."""
        if working:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh(self):
        state = core.status(self.folder)
        for name, check in state.rows():
            self.status_labels[name].configure(text=check.text, fg=OK if check.ok else NOT_OK)
        ours = self.process is not None and self.process.poll() is None
        ready = state.python.ok and state.packages.ok and state.settings.ok
        enabled = {
            "setup": state.python.ok and not self.busy and not ours,
            "settings": not self.busy and not ours,
            "start": ready and not self.busy and not ours and not state.running.ok,
            "stop": ours,
            "open": state.running.ok,
            "admin": not self.busy and not ours,
        }
        for key, on in enabled.items():
            self.buttons[key].configure(state="normal" if on else "disabled")
        if state.running.ok and not ours and self.url is None:
            self.url = f"http://127.0.0.1:{core.configured_port(self.folder)}"
        return state

    def pump(self):
        """Apply what the worker threads reported; runs on the window's own thread."""
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self.say(value)
                elif kind == "ready":
                    self.url = value
                    self.say("AlgoGuard is ready. Opening your browser...")
                    webbrowser.open(value)
                    self.refresh()
                elif kind == "done":
                    self.busy = False
                    self.show_progress(False)
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(200, self.pump)

    # -- actions ---------------------------------------------------------------------
    def set_up(self):
        self.busy = True
        self.show_progress(True)
        self.refresh()
        threading.Thread(target=self._run_setup, daemon=True).start()

    def _run_setup(self):
        try:
            for step in core.setup_steps(self.folder):
                self.messages.put(("log", step.label + "..."))
                process = _spawn(step.command, self.folder)
                for line in process.stdout:
                    self.messages.put(("log", line))
                if process.wait() != 0:
                    self.messages.put(("log", "Setup did not finish. Check the internet "
                                              "connection and press Set up again."))
                    return
            self.messages.put(("log", "Setup finished. Enter the settings, then press Start."))
        except OSError as error:
            self.messages.put(("log", f"Setup could not run: {error}"))
        finally:
            self.messages.put(("done", None))

    def settings(self):
        url, has_key = core.read_settings(self.folder)
        dialog = tk.Toplevel(self.root)
        dialog.title("AlgoGuard settings")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=14)
        body.grid()
        ttk.Label(body, text="Enter the two values your administrator gave you.").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(body, text="Project URL").grid(row=1, column=0, sticky="w")
        url_box = ttk.Entry(body, width=58)
        url_box.insert(0, url)
        url_box.grid(row=1, column=1, pady=2)
        ttk.Label(body, text="Publishable key").grid(row=2, column=0, sticky="w")
        key_box = ttk.Entry(body, width=58)
        key_box.grid(row=2, column=1, pady=2)
        ttk.Label(body, text="A key is saved. Leave the box empty to keep it." if has_key
                  else "No key saved yet. It starts with sb_publishable_.").grid(
            row=3, column=1, sticky="w")
        result = tk.Label(body, text="", anchor="w", justify="left", wraplength=420)
        result.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def save():
            key = key_box.get().strip()
            if not key and has_key and url_box.get().strip() == url:
                dialog.destroy()
                return
            if not key:
                result.configure(text="Enter the publishable key as well.", fg=NOT_OK)
                return
            try:
                core.write_settings(self.folder, url_box.get(), key)
            except (core.SettingsError, OSError) as error:
                result.configure(text=str(error), fg=NOT_OK)
                return
            code, message = core.check_settings(self.folder)
            result.configure(text="Saved. " + message, fg=OK if code in (0, None) else NOT_OK)
            self.say("Settings saved. " + message)
            self.refresh()

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Save", command=save).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=1)
        url_box.focus_set()

    def start(self):
        try:
            self.process = _spawn(core.start_command(self.folder), self.folder)
        except OSError as error:
            self.say(f"AlgoGuard could not start: {error}")
            return
        self.url = None
        self.say("Starting AlgoGuard...")
        threading.Thread(target=self._follow, args=(self.process,), daemon=True).start()
        self.refresh()

    def _follow(self, process):
        announced = False
        for line in process.stdout:
            self.messages.put(("log", line))
            url = core.running_url(line)
            if url and not announced:
                announced = True
                self.messages.put(("ready", url))
        process.wait()
        self.messages.put(("log", "AlgoGuard has stopped."))
        self.messages.put(("done", None))

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                           capture_output=True, creationflags=core.NO_WINDOW)
        self.url = None
        self.root.after(600, self.refresh)

    def open_browser(self):
        webbrowser.open(self.url or f"http://127.0.0.1:{core.configured_port(self.folder)}")

    def elevate(self):
        script = str(Path(__file__).resolve())
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}"', str(self.folder), 1)
        if result > 32:
            self.root.destroy()
        else:
            self.say("Windows did not start AlgoGuard as administrator.")

    def close(self):
        if self.process is not None and self.process.poll() is None:
            if not messagebox.askyesno(
                    "AlgoGuard", "AlgoGuard is running. Closing this window stops it.\n"
                    "Records waiting to upload are kept.\n\nStop AlgoGuard and close?"):
                return
            self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    LauncherWindow(root, Path(__file__).resolve().parent)
    root.mainloop()


if __name__ == "__main__":
    main()
