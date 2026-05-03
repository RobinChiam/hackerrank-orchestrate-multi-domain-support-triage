"""Textual TUI for the support triage pipeline.

Provides an interactive terminal interface for building the vector index and
processing support tickets, with real-time progress feedback and a colour-coded
results table.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    Static,
)

from agent import SupportTriageAgent
from config import DEFAULT_INPUT_CSV, DEFAULT_OUTPUT_CSV, INDEX_DB_PATH
from models import AgentOutput

from rich.text import Text


# ─── Custom Messages ────────────────────────────────────────────────────────


class IndexStarted(Message):
    """Signals that the index build has started."""


class IndexComplete(Message):
    """Signals that the index build finished."""

    def __init__(self, success: bool, error_msg: str = "") -> None:
        super().__init__()
        self.success = success
        self.error_msg = error_msg


class RunStarted(Message):
    """Signals that the live run has started."""

    def __init__(self, total: int) -> None:
        super().__init__()
        self.total = total


class RunProgress(Message):
    """Reports completion of one ticket during the live run."""

    def __init__(self, completed: int, total: int) -> None:
        super().__init__()
        self.completed = completed
        self.total = total


class RunTicketResult(Message):
    """Carries a single processed ticket result for the DataTable."""

    def __init__(self, index: int, output: AgentOutput) -> None:
        super().__init__()
        self.index = index
        self.output = output


class RunComplete(Message):
    """Signals the live run finished."""

    def __init__(
        self,
        total: int,
        output_path: str,
        success: bool,
        error_msg: str = "",
    ) -> None:
        super().__init__()
        self.total = total
        self.output_path = output_path
        self.success = success
        self.error_msg = error_msg


# ─── Textual Application ────────────────────────────────────────────────────


class SupportTriageApp(App):
    """Interactive TUI for the support triage agent."""

    TITLE = "Support Triage Agent"
    SUB_TITLE = "Interactive Pipeline Runner"
    CSS_PATH = "tui.tcss"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "toggle_dark", "Toggle Dark Mode"),
        ("i", "focus_index", "Build Index"),
        ("r", "focus_run", "Start Run"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._is_indexing = False
        self._is_running = False
        self._run_total = 0

    # ── Layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="top-row"):
            with Vertical(id="config-pane") as config:
                config.border_title = "⚙  Configuration"
                yield Label("Index DB Path", classes="field-label")
                yield Input(
                    value=str(INDEX_DB_PATH),
                    placeholder="Path to SQLite index",
                    id="index-path-input",
                )
                yield Label("Input CSV", classes="field-label")
                yield Input(
                    value=str(DEFAULT_INPUT_CSV),
                    placeholder="Path to input CSV",
                    id="input-csv-input",
                )
                yield Label("Output CSV", classes="field-label")
                yield Input(
                    value=str(DEFAULT_OUTPUT_CSV),
                    placeholder="Path to output CSV",
                    id="output-csv-input",
                )
                with Horizontal(id="button-row"):
                    yield Button(
                        "⬡ Build Index",
                        id="build-index-btn",
                        variant="primary",
                    )
                    yield Button(
                        "▶ Start Live Run",
                        id="start-run-btn",
                        variant="success",
                    )
            with Vertical(id="monitor-pane") as monitor:
                monitor.border_title = "📡 Live Run Monitor"
                yield Static("● Idle", id="status-label")
                yield ProgressBar(total=100, show_eta=False, id="run-progress")
                yield Static("Waiting for action…", id="progress-detail")
                yield Static("", id="monitor-log")
        with Vertical(id="table-container") as table_box:
            table_box.border_title = "📊 Results"
            yield DataTable(id="output-table")
        yield Footer()

    def on_mount(self) -> None:
        """Set up the DataTable columns on startup."""
        table = self.query_one("#output-table", DataTable)
        table.add_columns("#", "Status", "Company", "Product Area", "Response", "Justification")
        table.cursor_type = "row"
        # Start with progress bar at 0
        progress = self.query_one("#run-progress", ProgressBar)
        progress.update(total=100, progress=0)

    # ── Actions ──────────────────────────────────────────────────────────

    def action_focus_index(self) -> None:
        self.query_one("#build-index-btn", Button).press()

    def action_focus_run(self) -> None:
        self.query_one("#start-run-btn", Button).press()

    # ── Button Handlers ──────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "build-index-btn":
            if self._is_indexing:
                return
            self._start_index_build()
        elif event.button.id == "start-run-btn":
            if self._is_running:
                return
            self._start_live_run()

    # ── Index Build ──────────────────────────────────────────────────────

    def _start_index_build(self) -> None:
        self._is_indexing = True
        btn = self.query_one("#build-index-btn", Button)
        btn.add_class("-running")
        btn.label = "⟳ Building…"
        self._set_status("● Building Index…", "status-building")
        self._set_detail("Embedding corpus chunks — this may take a moment…")
        self._set_log("")
        progress = self.query_one("#run-progress", ProgressBar)
        progress.update(total=None)  # indeterminate mode
        self._do_index_build()

    @work(thread=True)
    def _do_index_build(self) -> None:
        index_path = Path(self.query_one("#index-path-input", Input).value.strip())
        try:
            agent = SupportTriageAgent(index_path=index_path, verbose=False)
            agent.ensure_index(force_rebuild=True)
            self.post_message(IndexComplete(success=True))
        except Exception as exc:
            self.post_message(IndexComplete(success=False, error_msg=str(exc)))

    def on_index_complete(self, message: IndexComplete) -> None:
        self._is_indexing = False
        btn = self.query_one("#build-index-btn", Button)
        btn.remove_class("-running")
        btn.label = "⬡ Build Index"
        progress = self.query_one("#run-progress", ProgressBar)
        progress.update(total=100, progress=100 if message.success else 0)
        if message.success:
            self._set_status("✔ Index Built Successfully", "status-success")
            self._set_detail("Vector index is ready.")
            self._set_log("")
        else:
            self._set_status("✖ Index Build Failed", "status-error")
            self._set_detail(f"Error: {message.error_msg}")

    # ── Live Run ─────────────────────────────────────────────────────────

    def _start_live_run(self) -> None:
        if self._is_indexing:
            self._set_status("⚠ Wait for index build to finish", "status-warning")
            return
        self._is_running = True
        btn = self.query_one("#start-run-btn", Button)
        btn.add_class("-running")
        btn.label = "⟳ Running…"
        # Clear the table for a fresh run
        table = self.query_one("#output-table", DataTable)
        table.clear()
        self._set_status("● Processing Tickets…", "status-running")
        self._set_detail("Initializing pipeline…")
        self._set_log("")
        progress = self.query_one("#run-progress", ProgressBar)
        progress.update(total=100, progress=0)
        self._do_live_run()

    @work(thread=True)
    def _do_live_run(self) -> None:
        index_path = Path(self.query_one("#index-path-input", Input).value.strip())
        input_csv = Path(self.query_one("#input-csv-input", Input).value.strip())
        output_csv = Path(self.query_one("#output-csv-input", Input).value.strip())

        try:
            agent = SupportTriageAgent(index_path=index_path, verbose=False)
            agent.ensure_index(force_rebuild=False)

            rows = agent._read_tickets(input_csv)
            total = len(rows)
            self.post_message(RunStarted(total=total))

            ordered_results: list[dict[str, str] | None] = [None] * total
            completed_count = 0
            completed_lock = threading.Lock()
            top_k = 5

            with ThreadPoolExecutor(max_workers=10, thread_name_prefix="tui-ticket") as executor:
                future_to_index = {
                    executor.submit(
                        agent._process_single_ticket, row, top_k, idx + 1
                    ): idx
                    for idx, row in enumerate(rows)
                }
                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    try:
                        result = future.result()
                        ordered_results[idx] = result
                        output = agent._agent_output_from_row(result)
                        with completed_lock:
                            completed_count += 1
                            current = completed_count
                        self.post_message(RunTicketResult(index=idx + 1, output=output))
                        self.post_message(RunProgress(completed=current, total=total))
                    except Exception as exc:
                        with completed_lock:
                            completed_count += 1
                            current = completed_count
                        self.post_message(RunProgress(completed=current, total=total))
                        self.post_message(
                            RunComplete(
                                total=current,
                                output_path=str(output_csv),
                                success=False,
                                error_msg=f"Ticket {idx + 1} failed: {exc}",
                            )
                        )
                        return

            # Write outputs
            outputs = [
                agent._agent_output_from_row(r) for r in ordered_results if r is not None
            ]
            agent._write_outputs(output_csv, outputs)
            self.post_message(
                RunComplete(
                    total=total,
                    output_path=str(output_csv),
                    success=True,
                )
            )
        except Exception as exc:
            self.post_message(
                RunComplete(
                    total=0,
                    output_path="",
                    success=False,
                    error_msg=str(exc),
                )
            )

    def on_run_started(self, message: RunStarted) -> None:
        self._run_total = message.total
        progress = self.query_one("#run-progress", ProgressBar)
        progress.update(total=message.total, progress=0)
        self._set_detail(f"Processing 0/{message.total} tickets…")

    def on_run_progress(self, message: RunProgress) -> None:
        progress = self.query_one("#run-progress", ProgressBar)
        progress.update(total=message.total, progress=message.completed)
        self._set_detail(f"Processing {message.completed}/{message.total} tickets…")

    def on_run_ticket_result(self, message: RunTicketResult) -> None:
        table = self.query_one("#output-table", DataTable)
        output = message.output

        # Build styled status text directly — avoids update_cell key mismatch
        styled_status = self._styled_status(output.status)

        # Truncate long text for readability
        response_preview = (output.response[:100] + "…") if len(output.response) > 100 else output.response
        justification_preview = (
            (output.justification[:100] + "…") if len(output.justification) > 100 else output.justification
        )

        table.add_row(
            str(message.index),
            styled_status,
            output.company or "—",
            output.product_area or "—",
            response_preview,
            justification_preview,
            key=f"ticket-{message.index}",
        )

    def on_run_complete(self, message: RunComplete) -> None:
        self._is_running = False
        btn = self.query_one("#start-run-btn", Button)
        btn.remove_class("-running")
        btn.label = "▶ Start Live Run"
        progress = self.query_one("#run-progress", ProgressBar)
        if message.success:
            progress.update(total=message.total, progress=message.total)
            self._set_status("✔ Run Complete", "status-success")
            self._set_detail(
                f"Processed {message.total} tickets. Output saved to: {message.output_path}"
            )
            self._set_log("")
        else:
            self._set_status("✖ Run Failed", "status-error")
            self._set_detail(f"Error: {message.error_msg}")

    # ── Helpers ──────────────────────────────────────────────────────────

    def _set_status(self, text: str, css_class: str = "") -> None:
        label = self.query_one("#status-label", Static)
        label.update(text)
        # Update visual colour based on state
        label.remove_class("status-building", "status-running", "status-success", "status-error", "status-warning")
        if css_class:
            label.add_class(css_class)

    def _set_detail(self, text: str) -> None:
        self.query_one("#progress-detail", Static).update(text)

    def _set_log(self, text: str) -> None:
        self.query_one("#monitor-log", Static).update(text)

    @staticmethod
    def _styled_status(status: str) -> Text:
        """Return a Rich Text object with colour-coded status label.

        Green for REPLIED, blue for ESCALATED, plain for anything else.
        """
        status_upper = status.upper()
        if status.lower() == "replied":
            markup = f"[bold green]{status_upper}[/bold green]"
        elif status.lower() == "escalated":
            markup = f"[bold dodger_blue1]{status_upper}[/bold dodger_blue1]"
        else:
            markup = status_upper
        return Text.from_markup(markup)


# ─── Entry Point ─────────────────────────────────────────────────────────────


def run_tui() -> None:
    """Launch the TUI application."""
    app = SupportTriageApp()
    app.run()


if __name__ == "__main__":
    run_tui()
