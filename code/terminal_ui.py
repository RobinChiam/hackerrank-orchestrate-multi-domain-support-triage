from __future__ import annotations

import sys
import threading
import time


def clear_line(*, enabled: bool = True) -> None:
    """Clear the current terminal line when UI output is enabled."""
    if not enabled:
        return
    sys.stdout.write("\r" + (" " * 96) + "\r")
    sys.stdout.flush()


class Spinner:
    """A small terminal spinner for brief startup animations."""

    def __init__(
        self,
        message: str,
        *,
        enabled: bool = True,
        interval_seconds: float = 0.1,
    ) -> None:
        """Configure the spinner animation."""
        self.message = message
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self._frames = ("|", "/", "-", "\\")
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the spinner in a background thread."""
        if not self.enabled or self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="terminal-spinner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the spinner and clear its terminal line."""
        if not self.enabled or self._thread is None:
            return
        self._stop_event.set()
        self._thread.join()
        self._thread = None
        clear_line(enabled=True)

    def _run(self) -> None:
        """Render spinner frames until asked to stop."""
        frame_index = 0
        while not self._stop_event.is_set():
            with self._lock:
                sys.stdout.write(
                    f"\r{self._frames[frame_index % len(self._frames)]} {self.message}"
                )
                sys.stdout.flush()
            frame_index += 1
            time.sleep(self.interval_seconds)


class ProgressBar:
    """A thread-safe single-line terminal progress bar."""

    def __init__(
        self,
        total: int,
        *,
        enabled: bool = True,
        width: int = 18,
    ) -> None:
        """Set up the progress bar state."""
        self.total = max(total, 0)
        self.enabled = enabled
        self.width = width
        self.completed = 0
        self._lock = threading.Lock()

    def render_initial(self) -> None:
        """Render the initial 0% progress state."""
        if not self.enabled:
            return
        with self._lock:
            self._render_locked()

    def advance(self) -> None:
        """Advance the progress bar by one completed unit."""
        if not self.enabled:
            return
        with self._lock:
            if self.completed < self.total:
                self.completed += 1
            self._render_locked()

    def clear(self) -> None:
        """Clear the progress bar from the terminal."""
        if not self.enabled:
            return
        with self._lock:
            clear_line(enabled=True)

    def _render_locked(self) -> None:
        """Render the current progress state while the lock is held."""
        total = self.total or 1
        filled = int((self.completed / total) * self.width)
        bar = ("#" * filled).ljust(self.width, "-")
        percent = int((self.completed / total) * 100)
        sys.stdout.write(
            f"\r[{bar}] {percent:3d}% | Processing ticket {self.completed}/{self.total} ..."
        )
        sys.stdout.flush()


def play_startup_animation(
    message: str,
    *,
    enabled: bool = True,
    duration_seconds: float = 0.8,
) -> None:
    """Play a brief startup spinner animation."""
    if not enabled:
        return
    spinner = Spinner(message, enabled=True)
    spinner.start()
    time.sleep(duration_seconds)
    spinner.stop()


def print_success_summary(
    *,
    total: int,
    output_path: str,
    enabled: bool = True,
) -> None:
    """Print the final run summary after the progress line has been cleared."""
    if not enabled:
        return
    clear_line(enabled=True)
    sys.stdout.write("Processing Complete!\n")
    sys.stdout.write(f"Processed {total} tickets successfully.\n")
    sys.stdout.write(f"Output saved to: {output_path}\n")
    sys.stdout.flush()
