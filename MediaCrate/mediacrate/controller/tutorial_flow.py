from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QTimer


class TutorialFlowWindow(Protocol):
    def is_settings_visible(self) -> bool: ...

    def is_batch_mode_enabled(self) -> bool: ...

    def set_batch_mode_enabled(self, enabled: bool, *, emit: bool = True) -> None: ...

    def set_single_url_validation_busy(self, busy: bool) -> None: ...

    def set_single_url_analysis_state(self, state: str) -> None: ...

    def set_single_url_thumbnail(self, data: bytes | None, source_url: str) -> None: ...

    def reset_download_progress(self) -> None: ...

    def set_tutorial_mode(self, enabled: bool) -> None: ...

    def set_settings_visible(self, visible: bool, *, animated: bool = True) -> None: ...

    def tutorialTargets(self) -> dict[str, object]: ...

    def update_tutorial_step(
        self,
        *,
        title: str,
        body: str,
        index: int,
        total: int,
        target_widget: object,
    ) -> None: ...

    def ensure_settings_target_visible(self, widget: object) -> None: ...

    def ensure_multi_target_visible(self, widget: object) -> None: ...


class TutorialFlowController(Protocol):
    window: TutorialFlowWindow
    _single_analysis_timer: QTimer
    _single_analysis_pending_url: str
    _tutorial_active: bool
    _tutorial_steps: list[dict[str, object]]
    _tutorial_index: int
    _tutorial_prev_settings_visible: bool
    _tutorial_prev_batch_mode: bool
    _tutorial_wait_step_index: int
    _tutorial_wait_cycles: int
    _tutorial_step_timer: QTimer

    def _is_download_running(self) -> bool: ...

    def _show_info(self, title: str, text: str) -> int: ...

    def _refresh_batch_entries_view(self) -> None: ...

    def _apply_metadata_fetch_policy(self) -> None: ...

    def start_tutorial(self) -> None: ...

    def _build_tutorial_steps(self) -> list[dict[str, object]]: ...

    def _show_tutorial_step(self) -> None: ...

    def _end_tutorial(self, completed: bool) -> None: ...


class TutorialFlow:
    def on_tutorial_requested(controller: TutorialFlowController) -> None:
        if controller._is_download_running():
            controller._show_info("Download in progress", "Stop downloads before starting the tutorial.")
            return
        controller.start_tutorial()

    @staticmethod
    def build_tutorial_steps() -> list[dict[str, object]]:
        return [
            {
                "id": "main_ui",
                "title": "Main UI",
                "body": "This is the main download area. Start here for single-link downloads.",
                "target": "main_ui",
            },
            {
                "id": "single_input",
                "title": "URL Input",
                "body": "Paste a media URL here. The app validates links and prepares metadata when enabled.",
                "target": "single_input",
                "set_batch_mode": False,
                "open_settings": False,
            },
            {
                "id": "format_quality",
                "title": "Format and Quality",
                "body": "Format chooses container/type. Quality is populated from metadata for the URL; if metadata is disabled/unavailable, fallback defaults are used.",
                "target": "format_quality",
                "set_batch_mode": False,
                "open_settings": False,
            },
            {
                "id": "single_actions",
                "title": "Download Controls",
                "body": "Use Download to start, Pause/Resume to pause active work, and Stop to cancel.",
                "target": "single_actions",
                "set_batch_mode": False,
                "open_settings": False,
            },
            {
                "id": "progress_console",
                "title": "Progress and Console",
                "body": "Progress shows current job completion. Console logs status, retries, dependency activity, and errors.",
                "target": "progress_console",
                "set_batch_mode": False,
                "open_settings": False,
            },
            {
                "id": "settings",
                "title": "Settings",
                "body": "Settings controls behavior like UI scale, download policies, dependencies, and history.",
                "target": "settings_panel",
                "open_settings": True,
                "set_batch_mode": False,
            },
            {
                "id": "settings_general",
                "title": "General Settings",
                "body": "Switch between Single-URL and Multi-URL modes here.",
                "target": "settings_general",
                "open_settings": True,
                "set_batch_mode": False,
            },
            {
                "id": "settings_interface",
                "title": "Interface Settings",
                "body": "UI size changes manual app scale in fixed 5% steps between 75% and 200%.",
                "target": "settings_interface",
                "open_settings": True,
                "set_batch_mode": False,
            },
            {
                "id": "settings_downloads",
                "title": "Download Settings",
                "body": "Control save location, naming template, conflict policy, concurrency, retries, speed limit, and metadata behavior.",
                "target": "settings_downloads",
                "open_settings": True,
                "set_batch_mode": False,
            },
            {
                "id": "settings_dependencies",
                "title": "Dependencies",
                "body": "Node.js status/install is managed here. It is used by dependency tools and workflow integrations.",
                "target": "settings_dependencies",
                "open_settings": True,
                "set_batch_mode": False,
            },
            {
                "id": "settings_history",
                "title": "History",
                "body": "History lets you open outputs, retry URLs, and clear history with safety prompts.",
                "target": "settings_history",
                "open_settings": True,
                "set_batch_mode": False,
            },
            {
                "id": "multi_url",
                "title": "Multi-URL",
                "body": "Multi-URL mode lets you queue many links, monitor each row, and run batch downloads in one flow.",
                "target": "multi_url",
                "open_settings": False,
                "set_batch_mode": True,
            },
            {
                "id": "multi_entries",
                "title": "Queue List",
                "body": "Each row tracks readiness, progress, retries, and status. You can start, pause, resume, or remove rows individually.",
                "target": "multi_entries",
                "open_settings": False,
                "set_batch_mode": True,
            },
            {
                "id": "finish",
                "title": "You Are Ready",
                "body": "You can switch between Single-URL and Multi-URL here at any time. Enjoy MediaCrate and happy downloading.",
                "target": "mode_switch",
                "open_settings": True,
                "restore_start_mode": True,
            },
        ]

    @staticmethod
    def set_tutorial_batch_mode(controller: TutorialFlowController, enabled: bool) -> None:
        normalized = bool(enabled)
        current = bool(controller.window.is_batch_mode_enabled())
        if normalized == current:
            return
        controller.window.set_batch_mode_enabled(normalized, emit=False)
        if normalized:
            controller._single_analysis_timer.stop()
            controller._single_analysis_pending_url = ""
            controller.window.set_single_url_validation_busy(False)
            controller.window.set_single_url_analysis_state("idle")
            controller.window.set_single_url_thumbnail(None, "")
            controller._refresh_batch_entries_view()
        elif not controller._is_download_running():
            controller.window.reset_download_progress()
            controller._apply_metadata_fetch_policy()

    @staticmethod
    def start_tutorial(controller: TutorialFlowController) -> None:
        if controller._tutorial_active:
            controller._show_tutorial_step()
            return
        initial_batch_mode = bool(controller.window.is_batch_mode_enabled())
        initial_settings_visible = bool(controller.window.is_settings_visible())
        if initial_batch_mode:
            TutorialFlow.set_tutorial_batch_mode(controller, False)
        controller._tutorial_steps = controller._build_tutorial_steps()
        if not controller._tutorial_steps:
            return
        controller._tutorial_active = True
        controller._tutorial_index = 0
        controller._tutorial_prev_settings_visible = initial_settings_visible
        controller._tutorial_prev_batch_mode = initial_batch_mode
        controller._tutorial_wait_step_index = -1
        controller._tutorial_wait_cycles = 0
        timer = getattr(controller, "_tutorial_step_timer", None)
        if timer is not None:
            timer.stop()
        controller.window.set_tutorial_mode(True)
        controller._show_tutorial_step()

    @staticmethod
    def schedule_tutorial_step_refresh(controller: TutorialFlowController, delay_ms: int) -> None:
        delay = max(0, int(delay_ms))
        timer = getattr(controller, "_tutorial_step_timer", None)
        if timer is None:
            return
        timer.stop()
        timer.start(delay)

    @staticmethod
    def show_tutorial_step(controller: TutorialFlowController) -> None:
        if not controller._tutorial_active or not controller._tutorial_steps:
            return
        while 0 <= controller._tutorial_index < len(controller._tutorial_steps):
            if controller._tutorial_wait_step_index != controller._tutorial_index:
                controller._tutorial_wait_step_index = controller._tutorial_index
                controller._tutorial_wait_cycles = 0
            step = controller._tutorial_steps[controller._tutorial_index]
            defer_ms = 0
            if "open_settings" in step:
                desired_settings = bool(step.get("open_settings"))
                current_settings = bool(controller.window.is_settings_visible())
                if desired_settings != current_settings:
                    controller.window.set_settings_visible(desired_settings)
                    defer_ms = max(defer_ms, 180)
            if "set_batch_mode" in step:
                desired_mode = bool(step.get("set_batch_mode"))
                if desired_mode != bool(controller.window.is_batch_mode_enabled()):
                    TutorialFlow.set_tutorial_batch_mode(controller, desired_mode)
                    defer_ms = max(defer_ms, 140)
            if bool(step.get("restore_start_mode")):
                desired_mode = bool(controller._tutorial_prev_batch_mode)
                if desired_mode != bool(controller.window.is_batch_mode_enabled()):
                    TutorialFlow.set_tutorial_batch_mode(controller, desired_mode)
                    defer_ms = max(defer_ms, 140)
            target_key = str(step.get("target", "")).strip()
            targets = controller.window.tutorialTargets()
            target_widget = targets.get(target_key) if target_key else None
            step_title = str(step.get("title", "Tutorial"))
            step_body = str(step.get("body", ""))

            def _render_step(target: object) -> None:
                controller.window.update_tutorial_step(
                    title=step_title,
                    body=step_body,
                    index=controller._tutorial_index,
                    total=len(controller._tutorial_steps),
                    target_widget=target,
                )

            if defer_ms > 0:
                controller._tutorial_wait_cycles += 1
                if controller._tutorial_wait_cycles > 30:
                    defer_ms = 0
                else:
                    _render_step(None)
                    TutorialFlow.schedule_tutorial_step_refresh(controller, defer_ms)
                    return
            target_widgets = target_widget if isinstance(target_widget, (list, tuple)) else (target_widget,)
            measurable_targets = [
                item
                for item in target_widgets
                if all(hasattr(item, name) for name in ("isVisible", "width", "height"))
            ]
            visible_targets = [
                item
                for item in measurable_targets
                if item.isVisible()
                and item.width() > 0
                and item.height() > 0
            ]
            if target_widget is not None and measurable_targets and not visible_targets:
                controller._tutorial_wait_cycles += 1
                if controller._tutorial_wait_cycles <= 30:
                    _render_step(None)
                    TutorialFlow.schedule_tutorial_step_refresh(controller, 90)
                    return
                target_widget = None
            if target_widget is not None:
                controller.window.ensure_settings_target_visible(target_widget)
                controller.window.ensure_multi_target_visible(target_widget)
            _render_step(target_widget)
            controller._tutorial_wait_cycles = 0
            return
        controller._end_tutorial(completed=True)

    @staticmethod
    def advance_tutorial(controller: TutorialFlowController) -> None:
        if not controller._tutorial_active:
            return
        if controller._tutorial_index >= len(controller._tutorial_steps) - 1:
            controller._end_tutorial(completed=True)
            return
        controller._tutorial_index += 1
        controller._show_tutorial_step()

    @staticmethod
    def rewind_tutorial(controller: TutorialFlowController) -> None:
        if not controller._tutorial_active:
            return
        if controller._tutorial_index <= 0:
            controller._show_tutorial_step()
            return
        controller._tutorial_index -= 1
        controller._show_tutorial_step()

    @staticmethod
    def end_tutorial(controller: TutorialFlowController, completed: bool) -> None:
        _ = completed
        if not controller._tutorial_active:
            return
        timer = getattr(controller, "_tutorial_step_timer", None)
        if timer is not None:
            timer.stop()
        controller._tutorial_active = False
        controller._tutorial_steps = []
        controller._tutorial_index = 0
        controller._tutorial_wait_step_index = -1
        controller._tutorial_wait_cycles = 0
        controller.window.set_tutorial_mode(False)
        if bool(controller.window.is_settings_visible()) != controller._tutorial_prev_settings_visible:
            controller.window.set_settings_visible(controller._tutorial_prev_settings_visible)
        TutorialFlow.set_tutorial_batch_mode(controller, controller._tutorial_prev_batch_mode)
