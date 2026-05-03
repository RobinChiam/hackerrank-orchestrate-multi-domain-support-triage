"""Textual TUI for the support triage pipeline.

Provides an interactive terminal interface for building the vector index and
processing support tickets, with a live dashboard for run progress and ticket
classification summaries.
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
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


class IndexCheckComplete(Message):
    """Result of the fast startup index-existence check."""

    def __init__(self, exists: bool, stale: bool) -> None:
        super().__init__()
        self.exists = exists
        self.stale = stale


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


class RunTicketCompleted(Message):
    """Carries a completed ticket back to the main UI thread."""

    def __init__(
        self,
        *,
        index: int,
        output: AgentOutput,
        completed: int,
        total: int,
    ) -> None:
        super().__init__()
        self.index = index
        self.output = output
        self.completed = completed
        self.total = total


class RunComplete(Message):
    """Signals the live run finished."""

    def __init__(
        self,
        *,
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

    run_total = reactive(0)
    run_completed = reactive(0)
    run_replied = reactive(0)
    run_escalated = reactive(0)
    run_elapsed_seconds = reactive(0.0)
    product_area_counts = reactive({})
    request_type_counts = reactive({})

    def __init__(self) -> None:
        super().__init__()
        self._is_indexing = False
        self._is_running = False
        self._run_started_at: float | None = None
        self._run_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        """Build the application layout."""
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
                        disabled=True,
                    )
            with Vertical(id="monitor-pane") as monitor:
                monitor.border_title = "📡 Live Run Monitor"
                yield Static("● Checking index…", id="status-label")
                yield ProgressBar(total=100, show_eta=False, id="run-progress")
                yield Static("Verifying index on startup…", id="progress-detail")
                yield Static("Elapsed: 00:00:00", id="elapsed-label")
                yield Static("Status Counts", classes="monitor-section-title")
                yield Static("Replied: 0\nEscalated: 0", id="status-counts", classes="monitor-block")
                yield Static("Product Areas", classes="monitor-section-title")
                yield Static(
                    "No tickets processed yet.",
                    id="product-area-summary",
                    classes="monitor-block",
                )
                yield Static("Request Types", classes="monitor-section-title")
                yield Static(
                    "No tickets processed yet.",
                    id="request-type-summary",
                    classes="monitor-block",
                )
                yield Static("Run Summary", classes="monitor-section-title")
                yield Static("Awaiting live run.", id="monitor-log", classes="monitor-block")
        with Vertical(id="table-container") as table_box:
            table_box.border_title = "📊 Results"
            yield DataTable(id="output-table")
        yield Footer()

    def on_mount(self) -> None:
        """Set up the DataTable columns and run the fast startup index check."""
        table = self.query_one("#output-table", DataTable)
        table.add_columns(
            "#",
            "Status",
            "Company",
            "Product Area",
            "Response",
            "Justification",
        )
        table.cursor_type = "row"
        self.query_one("#run-progress", ProgressBar).update(total=100, progress=0)
        self._render_status_counts()
        self._render_count_block("#product-area-summary", self.product_area_counts)
        self._render_count_block("#request-type-summary", self.request_type_counts)
        index_path = Path(self.query_one("#index-path-input", Input).value.strip())
        self._check_index_on_startup(index_path)

    @work(thread=True)
    def _check_index_on_startup(self, index_path: Path) -> None:
        """Perform a fast index_exists + is_index_stale check in a worker."""
        try:
            agent = SupportTriageAgent(index_path=index_path, verbose=False)
            exists = agent.index_exists()
            stale = agent.is_index_stale() if exists else False
            self.post_message(IndexCheckComplete(exists=exists, stale=stale))
        except Exception:
            self.post_message(IndexCheckComplete(exists=False, stale=False))

    def on_index_check_complete(self, message: IndexCheckComplete) -> None:
        """Handle the startup index readiness check."""
        btn = self.query_one("#start-run-btn", Button)
        if message.exists:
            btn.disabled = False
            if message.stale:
                self._set_status("⚠ Index exists but may be stale", "status-warning")
                self._set_detail("Corpus files changed since last build. Consider rebuilding.")
            else:
                self._set_status("✔ Index Ready", "status-success")
                self._set_detail("Index is up to date — ready to process tickets.")
        else:
            btn.disabled = True
            self._set_status("✖ No Index Found", "status-error")
            self._set_detail("Build the index first using the button on the left.")

    def action_focus_index(self) -> None:
        """Trigger the index build button from the keyboard."""
        self.query_one("#build-index-btn", Button).press()

    def action_focus_run(self) -> None:
        """Trigger the live run button from the keyboard."""
        self.query_one("#start-run-btn", Button).press()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route button presses to the appropriate action."""
        if event.button.id == "build-index-btn":
            if self._is_indexing:
                return
            self._start_index_build()
        elif event.button.id == "start-run-btn":
            if self._is_running:
                return
            self._start_live_run()

    def _start_index_build(self) -> None:
        """Start an asynchronous index build."""
        self._is_indexing = True
        btn = self.query_one("#build-index-btn", Button)
        btn.add_class("-running")
        btn.label = "⟳ Building…"
        self._set_status("● Building Index…", "status-building")
        self._set_detail("Embedding corpus chunks — this may take a moment…")
        self._set_log("")
        self.query_one("#run-progress", ProgressBar).update(total=None)
        index_path = Path(self.query_one("#index-path-input", Input).value.strip())
        self._do_index_build(index_path)

    @work(thread=True)
    def _do_index_build(self, index_path: Path) -> None:
        """Build the vector index in a worker thread."""
        try:
            agent = SupportTriageAgent(index_path=index_path, verbose=False)
            agent.ensure_index(force_rebuild=True)
            self.post_message(IndexComplete(success=True))
        except Exception as exc:
            self.post_message(IndexComplete(success=False, error_msg=str(exc)))

    def on_index_complete(self, message: IndexComplete) -> None:
        """Restore the UI once the index build finishes."""
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
            self.query_one("#start-run-btn", Button).disabled = False
        else:
            self._set_status("✖ Index Build Failed", "status-error")
            self._set_detail(f"Error: {message.error_msg}")

    def _start_live_run(self) -> None:
        """Start a live CSV processing run and reset the dashboard state."""
        if self._is_indexing:
            self._set_status("⚠ Wait for index build to finish", "status-warning")
            return

        self._is_running = True
        self._reset_run_metrics()
        self._run_started_at = time.monotonic()
        self._restart_run_timer()

        btn = self.query_one("#start-run-btn", Button)
        btn.add_class("-running")
        btn.label = "⟳ Running…"

        table = self.query_one("#output-table", DataTable)
        table.clear()

        self._set_status("● Processing Tickets…", "status-running")
        self._set_detail("Initializing pipeline…")
        self._set_log("Live dashboard active. Waiting for completed tickets…")
        self.query_one("#run-progress", ProgressBar).update(total=1, progress=0)

        index_path = Path(self.query_one("#index-path-input", Input).value.strip())
        input_csv = Path(self.query_one("#input-csv-input", Input).value.strip())
        output_csv = Path(self.query_one("#output-csv-input", Input).value.strip())
        self._do_live_run(index_path, input_csv, output_csv)

    @work(thread=True)
    def _do_live_run(self, index_path: Path, input_csv: Path, output_csv: Path) -> None:
        """Execute a live ticket run and stream ticket completions back to the UI."""
        try:
            agent = SupportTriageAgent(index_path=index_path, verbose=False)
            if not agent.index_exists():
                agent.ensure_index(force_rebuild=False)

            total = len(agent._read_tickets(input_csv))
            self.post_message(RunStarted(total=total))

            def on_ticket_complete(
                ticket_index: int,
                output: AgentOutput,
                completed: int,
                total_rows: int,
            ) -> None:
                self.post_message(
                    RunTicketCompleted(
                        index=ticket_index,
                        output=output,
                        completed=completed,
                        total=total_rows,
                    )
                )

            outputs = agent.process_csv(
                input_path=input_csv,
                output_path=output_csv,
                top_k=5,
                limit=None,
                force_rebuild_index=False,
                on_ticket_complete=on_ticket_complete,
            )
            self.post_message(
                RunComplete(
                    total=len(outputs),
                    output_path=str(output_csv),
                    success=True,
                )
            )
        except Exception as exc:
            self.post_message(
                RunComplete(
                    total=0,
                    output_path=str(output_csv),
                    success=False,
                    error_msg=str(exc),
                )
            )

    def on_run_started(self, message: RunStarted) -> None:
        """Initialize the progress bar and detail text for a live run."""
        self.run_total = message.total
        self.run_completed = 0
        if message.total == 0:
            self._set_detail("No tickets found in the selected CSV.")
            self._set_log("Run completed with zero tickets.")
            self.query_one("#run-progress", ProgressBar).update(total=1, progress=0)
            return
        self._refresh_progress_widgets()

    def on_run_ticket_completed(self, message: RunTicketCompleted) -> None:
        """Update the dashboard and results table for a completed ticket."""
        self.run_total = message.total
        self.run_completed = message.completed
        self._tally_ticket(message.output)
        self._append_ticket_row(message.index, message.output)

    def on_run_complete(self, message: RunComplete) -> None:
        """Finalize the live run dashboard state."""
        self._is_running = False
        self._stop_run_timer()
        if self._run_started_at is not None:
            self.run_elapsed_seconds = time.monotonic() - self._run_started_at
            self._run_started_at = None

        btn = self.query_one("#start-run-btn", Button)
        btn.remove_class("-running")
        btn.label = "▶ Start Live Run"

        progress = self.query_one("#run-progress", ProgressBar)
        if message.success:
            if self.run_total > 0:
                progress.update(total=self.run_total, progress=self.run_total)
            else:
                progress.update(total=1, progress=0)
            self._set_status("✔ Run Complete", "status-success")
            self._set_detail(
                f"Processed {self.run_total} tickets. Output saved to: {message.output_path}"
            )
            self._set_log(self._build_success_summary(message.output_path))
        else:
            self._set_status("✖ Run Failed", "status-error")
            self._set_detail(f"Error: {message.error_msg}")
            self._set_log(self._build_failure_summary(message.error_msg))

    def watch_run_total(self, _value: int) -> None:
        """Refresh progress display when the ticket total changes."""
        self._refresh_progress_widgets()

    def watch_run_completed(self, _value: int) -> None:
        """Refresh progress display when the completed count changes."""
        self._refresh_progress_widgets()

    def watch_run_replied(self, _value: int) -> None:
        """Refresh the replied vs escalated status counts."""
        self._render_status_counts()

    def watch_run_escalated(self, _value: int) -> None:
        """Refresh the replied vs escalated status counts."""
        self._render_status_counts()

    def watch_run_elapsed_seconds(self, elapsed_seconds: float) -> None:
        """Refresh the elapsed run timer label."""
        if not self.is_mounted:
            return
        self.query_one("#elapsed-label", Static).update(
            f"Elapsed: {self._format_elapsed(elapsed_seconds)}"
        )

    def watch_product_area_counts(self, counts: dict[str, int]) -> None:
        """Refresh the product area summary block."""
        self._render_count_block("#product-area-summary", counts)

    def watch_request_type_counts(self, counts: dict[str, int]) -> None:
        """Refresh the request type summary block."""
        self._render_count_block("#request-type-summary", counts)

    def _tick_run_clock(self) -> None:
        """Advance the elapsed timer while a live run is active."""
        if not self._is_running or self._run_started_at is None:
            return
        self.run_elapsed_seconds = time.monotonic() - self._run_started_at

    def _restart_run_timer(self) -> None:
        """Reset and start the run clock."""
        self._stop_run_timer()
        self.run_elapsed_seconds = 0.0
        self._run_timer = self.set_interval(0.2, self._tick_run_clock)

    def _stop_run_timer(self) -> None:
        """Stop the elapsed timer if it is running."""
        if self._run_timer is not None:
            self._run_timer.stop()
            self._run_timer = None

    def _reset_run_metrics(self) -> None:
        """Reset all reactive dashboard counters for a fresh run."""
        self.run_total = 0
        self.run_completed = 0
        self.run_replied = 0
        self.run_escalated = 0
        self.product_area_counts = {}
        self.request_type_counts = {}
        self.run_elapsed_seconds = 0.0

    def _refresh_progress_widgets(self) -> None:
        """Update the live progress bar and detail text from reactive state."""
        if not self.is_mounted:
            return
        progress = self.query_one("#run-progress", ProgressBar)
        total = self.run_total
        if total <= 0:
            progress.update(total=1, progress=0)
            if not self._is_running:
                self._set_detail("Verifying index on startup…")
            return
        progress.update(total=total, progress=min(self.run_completed, total))
        self._set_detail(f"Processing {self.run_completed}/{total} tickets…")

    def _render_status_counts(self) -> None:
        """Render the replied versus escalated counters."""
        if not self.is_mounted:
            return
        self.query_one("#status-counts", Static).update(
            f"Replied: {self.run_replied}\nEscalated: {self.run_escalated}"
        )

    def _render_count_block(self, widget_id: str, counts: dict[str, int]) -> None:
        """Render one of the live frequency summary blocks."""
        if not self.is_mounted:
            return
        if not counts:
            text = "No tickets processed yet."
        else:
            lines = [
                f"- {self._display_label(key)}: {value}"
                for key, value in sorted(
                    counts.items(),
                    key=lambda item: (-item[1], item[0].lower()),
                )
            ]
            text = "\n".join(lines)
        self.query_one(widget_id, Static).update(text)

    def _tally_ticket(self, output: AgentOutput) -> None:
        """Update live counters and frequency summaries for a completed ticket."""
        if output.status.lower() == "replied":
            self.run_replied += 1
        else:
            self.run_escalated += 1

        product_areas = dict(self.product_area_counts)
        product_area_key = self._normalize_summary_key(output.product_area)
        product_areas[product_area_key] = product_areas.get(product_area_key, 0) + 1
        self.product_area_counts = product_areas

        request_types = dict(self.request_type_counts)
        request_type_key = self._normalize_summary_key(output.request_type)
        request_types[request_type_key] = request_types.get(request_type_key, 0) + 1
        self.request_type_counts = request_types

    def _append_ticket_row(self, ticket_index: int, output: AgentOutput) -> None:
        """Append one processed ticket to the bottom-row results table."""
        table = self.query_one("#output-table", DataTable)
        response_preview = (
            f"{output.response[:100]}…" if len(output.response) > 100 else output.response
        )
        justification_preview = (
            f"{output.justification[:100]}…"
            if len(output.justification) > 100
            else output.justification
        )
        table.add_row(
            str(ticket_index),
            self._styled_status(output.status),
            output.company or "—",
            output.product_area or "—",
            response_preview,
            justification_preview,
            key=f"ticket-{ticket_index}",
        )

    def _build_success_summary(self, output_path: str) -> str:
        """Render the final dashboard summary for a successful run."""
        return "\n".join(
            [
                "Run finished successfully.",
                f"Processed: {self.run_completed}/{self.run_total}",
                f"Replied: {self.run_replied}",
                f"Escalated: {self.run_escalated}",
                f"Elapsed: {self._format_elapsed(self.run_elapsed_seconds)}",
                f"Output: {output_path}",
            ]
        )

    def _build_failure_summary(self, error_msg: str) -> str:
        """Render the final dashboard summary for a failed run."""
        return "\n".join(
            [
                "Run failed.",
                f"Completed before failure: {self.run_completed}/{self.run_total}",
                f"Replied: {self.run_replied}",
                f"Escalated: {self.run_escalated}",
                f"Elapsed: {self._format_elapsed(self.run_elapsed_seconds)}",
                f"Error: {error_msg}",
            ]
        )

    def _set_status(self, text: str, css_class: str = "") -> None:
        """Update the main status label and swap its state class."""
        label = self.query_one("#status-label", Static)
        label.update(text)
        label.remove_class(
            "status-building",
            "status-running",
            "status-success",
            "status-error",
            "status-warning",
        )
        if css_class:
            label.add_class(css_class)

    def _set_detail(self, text: str) -> None:
        """Update the secondary status detail line."""
        self.query_one("#progress-detail", Static).update(text)

    def _set_log(self, text: str) -> None:
        """Update the dashboard run summary block."""
        self.query_one("#monitor-log", Static).update(text)

    @staticmethod
    def _normalize_summary_key(raw_value: str) -> str:
        """Normalize a product area or request type into a stable summary key."""
        value = raw_value.strip()
        return value or "unknown"

    @staticmethod
    def _display_label(raw_value: str) -> str:
        """Render compact, human-readable labels in the summary blocks."""
        value = raw_value.strip()
        if not value:
            return "Unknown"
        if "_" in value:
            return value.replace("_", " ").strip().title()
        return value

    @staticmethod
    def _format_elapsed(elapsed_seconds: float) -> str:
        """Format elapsed wall time as HH:MM:SS."""
        total_seconds = max(int(elapsed_seconds), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _styled_status(status: str) -> Text:
        """Return a Rich Text object with colour-coded status label."""
        status_upper = status.upper()
        if status.lower() == "replied":
            markup = f"[bold green]{status_upper}[/bold green]"
        elif status.lower() == "escalated":
            markup = f"[bold dodger_blue1]{status_upper}[/bold dodger_blue1]"
        else:
            markup = status_upper
        return Text.from_markup(markup)


def run_tui() -> None:
    """Launch the TUI application."""
    app = SupportTriageApp()
    app.run()


if __name__ == "__main__":
    run_tui()
