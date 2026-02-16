from __future__ import annotations

import os
import re
from datetime import datetime
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    Signal,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.app_metadata import APP_NAME, APP_VERSION
from ..core.config_service import DEFAULT_FILENAME_TEMPLATE, UI_SCALE_MAX, UI_SCALE_MIN, UI_SCALE_STEP
from ..core.formatting import format_batch_stats_line
from ..core.models import (
    AppConfig,
    BatchEntry,
    BatchEntryStatus,
    DownloadHistoryEntry,
    RetryProfile,
    is_audio_format_choice,
)
from .batch_entry_presenter import batch_entry_render_signature
from .dialogs import apply_dialog_theme, build_message_box, exec_dialog
from .layout_metrics import (
    normalize_scale_factor as _normalize_scale_factor,
    single_url_baseline_metrics as _single_url_baseline_metrics,
)
from .tutorial_overlay import TutorialOverlay
from .widget_utils import rounded_pixmap, set_widget_pointer_cursor
from .widgets import BatchEntryRowWidget, ChevronComboBox, RoundHandleSliderStyle, SquareCheckBoxStyle
from .theme import ThemePalette, build_stylesheet

SUBTITLE_TEXT = "Download video or audio from most sites."
FORMAT_LOAD_LABEL = "Load others"
FORMAT_LOADING_LABEL = "Loading formats..."
_FILENAME_TEMPLATE_PRESETS: tuple[tuple[str, str], ...] = (
    ("Title [ID] (Default)", DEFAULT_FILENAME_TEMPLATE),
    ("Title only", "%(title).150B.%(ext)s"),
    ("Uploader - Title", "%(uploader).80B - %(title).120B [%(id)s].%(ext)s"),
    ("ID - Title", "%(id)s - %(title).140B.%(ext)s"),
)
_FILENAME_TEMPLATE_CUSTOM_LABEL = "Custom (edit your own)"
_RETRY_PROFILE_LABELS: tuple[tuple[str, str], ...] = (
    ("Off", RetryProfile.OFF.value),
    ("Basic (Default)", RetryProfile.BASIC.value),
    ("Aggressive", RetryProfile.AGGRESSIVE.value),
)
_STALE_PART_CLEANUP_HOURS_OPTIONS: tuple[int, ...] = (0, 6, 12, 24, 48, 72, 168, 336, 720)


def _build_speed_limit_steps_kbps() -> list[int]:
    values: list[int] = []
    values.extend(range(10, 101, 10))
    values.extend(range(200, 1001, 100))
    values.extend(range(2000, 10001, 1000))
    values.extend(range(20000, 100001, 10000))
    values.append(0)
    return values


_SPEED_LIMIT_VALUES_KBPS = _build_speed_limit_steps_kbps()
_SPEED_LIMIT_SLIDER_MAX = len(_SPEED_LIMIT_VALUES_KBPS) - 1


def _format_speed_limit_label(kbps: int) -> str:
    clamped = max(0, int(kbps))
    if clamped <= 0:
        return "No limit"
    if clamped >= 1000 and clamped % 1000 == 0:
        return f"{clamped // 1000} MB/s"
    return f"{clamped:,} KB/s"


def _speed_limit_kbps_from_slider_value(value: int) -> int:
    index = max(0, min(_SPEED_LIMIT_SLIDER_MAX, int(value)))
    return int(_SPEED_LIMIT_VALUES_KBPS[index])


def _speed_limit_slider_value_from_kbps(value: int) -> int:
    requested = max(0, int(value))
    best_index = 0
    best_distance = None
    for index, step in enumerate(_SPEED_LIMIT_VALUES_KBPS):
        distance = abs(int(step) - requested)
        if best_distance is None or distance < best_distance:
            best_index = index
            best_distance = distance
            continue
        if distance == best_distance and int(step) == requested:
            best_index = index
    return best_index


def _format_stale_cleanup_label(hours: int) -> str:
    clamped = max(0, int(hours))
    if clamped <= 0:
        return "Disabled"
    suffix = "hour" if clamped == 1 else "hours"
    return f"{clamped} {suffix}"














class MainWindow(QMainWindow):
    startDownloadRequested = Signal()
    stopRequested = Signal()
    singlePauseResumeRequested = Signal()
    openDownloadsRequested = Signal()
    officialPageRequested = Signal()
    checkUpdatesRequested = Signal()
    themeModeChanged = Signal(str)
    uiScaleChanged = Signal(int)
    downloadLocationChanged = Signal(str)
    batchConcurrencyChanged = Signal(int)
    skipExistingFilesChanged = Signal(bool)
    autoStartReadyLinksChanged = Signal(bool)
    metadataFetchDisabledChanged = Signal(bool)
    batchRetryCountChanged = Signal(int)
    retryProfileChanged = Signal(str)
    fallbackDownloadOnMetadataErrorChanged = Signal(bool)
    filenameTemplateChanged = Signal(str)
    conflictPolicyChanged = Signal(str)
    speedLimitChanged = Signal(int)
    adaptiveConcurrencyChanged = Signal(bool)
    stalePartCleanupHoursChanged = Signal(int)
    batchModeChanged = Signal(bool)
    autoCheckUpdatesChanged = Signal(bool)
    disableHistoryChanged = Signal(bool)
    resetSettingsRequested = Signal()
    installDependencyRequested = Signal(str)
    qualityDropdownOpened = Signal()
    singleFormatChanged = Signal(str)
    singleQualityChanged = Signal(str)
    urlTextChanged = Signal(str)
    loadOtherFormatsRequested = Signal()
    multiAddUrlRequested = Signal(str)
    multiBulkAddRequested = Signal(str)
    multiStartAllRequested = Signal()
    multiPauseResumeAllRequested = Signal()
    multiStartEntryRequested = Signal(str)
    multiPauseEntryRequested = Signal(str)
    multiResumeEntryRequested = Signal(str)
    multiEntryFormatChanged = Signal(str, str)
    multiEntryQualityChanged = Signal(str, str)
    multiRemoveEntryRequested = Signal(str)
    multiExportRequested = Signal(str)
    historyOpenFileRequested = Signal(str)
    historyOpenFolderRequested = Signal(str)
    historyRetryRequested = Signal(str)
    historyClearRequested = Signal()
    tutorialRequested = Signal()
    tutorialNextRequested = Signal()
    tutorialBackRequested = Signal()
    tutorialSkipRequested = Signal()
    tutorialFinishRequested = Signal()

    def __init__(
        self,
        theme: ThemePalette,
        *,
        theme_mode: str,
        ui_scale_percent: int,
        icon_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.theme = theme
        self._theme_mode = "light" if theme_mode == "light" else "dark"
        self._ui_scale_percent = self._normalize_ui_scale_percent(ui_scale_percent)
        self._pending_ui_scale_percent: int | None = None
        self._close_handler: Callable[[], bool] | None = None
        self._settings_visible = False
        self._base_width = 680
        self._base_height = 400
        self._single_mode_extra_height = 8
        self._batch_inline_target_height = 0
        self._multi_entries_scroll_default_height = 0
        self._batch_mode_extra_width = 0
        self._base_settings_width = 340
        self._settings_min_width = 340
        self._settings_target_width = self._base_settings_width
        self._settings_animation_expected_end_width: int | None = None
        self._batch_mode_extra_height = 0
        self._render_scale = 1.0
        self._batch_entry_widgets: dict[str, BatchEntryRowWidget] = {}
        self._batch_entry_thumbnail_urls: dict[str, str] = {}
        self._batch_thumbnail_payload_by_url: dict[str, bytes] = {}
        self._batch_row_render_signatures: dict[str, tuple[object, ...]] = {}
        self._displayed_batch_entry_ids: list[str] = []
        self._all_batch_entries: list[BatchEntry] = []
        self._base_formats = ["VIDEO", "AUDIO", "MP4", "MP3"]
        self._other_formats: list[str] = []
        self._formats_loading = False
        self._show_all_formats = False
        self._last_non_loader_format = "VIDEO"
        self._quality_stale = True
        self._controls_locked = False
        self._single_url_validating = False
        self._single_meta_state = "idle"
        self._single_meta_full_title = ""
        self._single_meta_full_size = ""
        self._single_meta_full_info_lines = ["", "", ""]
        self._single_meta_thumbnail_source = ""
        self._single_meta_thumbnail_original: QPixmap | None = None
        self._single_meta_refresh_pending = False
        self._slider_styles: list[RoundHandleSliderStyle] = []
        self._checkbox_styles: list[SquareCheckBoxStyle] = []
        self._settings_card_layouts: list[QVBoxLayout] = []
        self._dependency_installed: dict[str, bool] = {"ffmpeg": False, "node": False}
        self._history_entries: list[DownloadHistoryEntry] = []
        self._filename_template_updating = False
        self._post_show_layout_synced = False
        self._tutorial_mode = False

        self.setWindowTitle(APP_NAME)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        if hasattr(Qt, "MSWindowsFixedSizeDialogHint"):
            self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, True)
        if icon_path and icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._connect_signals()
        self._install_control_styles()
        self._apply_combo_arrow_palette()
        self._install_wheel_guards()
        self._set_interaction_cursors()
        self._apply_window_layout()
        self._refresh_theme_toggle_icon()
        self.apply_windows_titlebar_theme()

    @staticmethod
    def _scaled(value: int, scale: float, minimum: int = 1) -> int:
        normalized = _normalize_scale_factor(scale)
        scaled = int(round(value * normalized))
        if normalized < 1.0:
            return max(1, scaled)
        return max(minimum, scaled)

    @staticmethod
    def _normalize_ui_scale_percent(value: int | str | None) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = 100
        clamped = max(int(UI_SCALE_MIN), min(int(UI_SCALE_MAX), parsed))
        step = max(1, int(UI_SCALE_STEP))
        snapped = int(round(clamped / float(step)) * step)
        return max(int(UI_SCALE_MIN), min(int(UI_SCALE_MAX), snapped))

    def _compute_dimensions(self, scale: float, *, batch_expansion: float | None = None) -> tuple[int, int]:
        if batch_expansion is None:
            expansion = 1.0 if self._is_batch_mode_enabled() else 0.0
        else:
            expansion = max(0.0, min(1.0, float(batch_expansion)))
        base_width = int(round(self._base_width * scale))
        base_height = int(round(self._base_height * scale))
        single_mode_extra = int(round(max(0, self._single_mode_extra_height) * (1.0 - expansion)))
        mode_extra_width = int(round(max(0, self._batch_mode_extra_width) * expansion))
        batch_extra = int(round(max(0, self._batch_inline_target_height) * expansion))
        mode_extra = int(round(max(0, self._batch_mode_extra_height) * expansion))
        return base_width + mode_extra_width, base_height + single_mode_extra + batch_extra + mode_extra

    def _compute_batch_inline_target_height(self) -> int:
        margins = self._batch_inline_layout.contentsMargins()
        spacing = max(0, self._batch_inline_layout.spacing())
        list_h = max(1, int(self._multi_entries_scroll_default_height or self.multi_entries_scroll.height()))
        return max(
            0,
            margins.top()
            + margins.bottom()
            + self.multi_toolbar_row.height()
            + self.batch_preflight_label.height()
            + self.multi_filter_row.height()
            + self.multi_filter_gap.height()
            + list_h
            + (spacing * 4)
        )

    def _set_batch_inline_section_height(self, height: int, *, force_visible: bool = False) -> None:
        clamped = max(0, int(height))
        visible = force_visible or clamped > 0
        if visible:
            self.batch_inline_section.setMinimumHeight(clamped)
            self.batch_inline_section.setMaximumHeight(16777215)
            self.batch_inline_section.setVisible(True)
            return
        self.batch_inline_section.setMinimumHeight(0)
        self.batch_inline_section.setMaximumHeight(0)
        self.batch_inline_section.setVisible(False)

    def _compute_settings_target_width(self, scale: float, window_width: int) -> int:
        desired = max(int(round(self._base_settings_width * scale)), self._settings_min_width)
        margins = self._outer_layout.contentsMargins()
        content_width = max(1, window_width - margins.left() - margins.right())
        reserve_main = self._scaled(235, scale, 150)
        max_overlay_by_reserve = max(0, content_width - reserve_main)
        max_overlay_by_ratio = int(round(content_width * 0.86))
        max_overlay = max(self._settings_min_width, min(max_overlay_by_reserve, max_overlay_by_ratio))
        return min(desired, max_overlay)

    def _set_settings_container_width(self, width: int) -> None:
        clamped = max(0, int(width))
        self.settings_panel.setMinimumWidth(clamped)
        self.settings_panel.setMaximumWidth(clamped)

    def _resolve_render_scale(self) -> float:
        requested_scale = self._normalize_ui_scale_percent(self._ui_scale_percent) / 100.0
        geometry = self._available_screen_geometry()
        render_scale = requested_scale
        if geometry is not None:
            max_width = max(1, int(geometry.width() * 0.92))
            max_height = max(1, int(geometry.height() * 0.92))
            target_width, target_height = self._compute_dimensions(render_scale)
            fit = self._fit_factor_for_bounds(target_width, target_height, max_width, max_height)
            render_scale = max(0.55, render_scale * fit)
        return _normalize_scale_factor(render_scale)

    @staticmethod
    def _available_screen_geometry():
        screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    @staticmethod
    def _fit_factor_for_bounds(width: int, height: int, max_width: int, max_height: int) -> float:
        if width <= 0 or height <= 0:
            return 1.0
        if max_width <= 0 or max_height <= 0:
            return 1.0
        return min(1.0, max_width / width, max_height / height)

    @staticmethod
    def _set_uniform_fixed_height(widgets: tuple[QWidget, ...], height: int) -> None:
        for widget in widgets:
            widget.setFixedHeight(int(height))

    def _settings_layout_metrics(self, scale: float) -> dict[str, int | tuple[int, int, int, int]]:
        base_margin = self._scaled(14, scale, 8)
        return {
            "root_inset": 0,
            "layout_margins": (base_margin, base_margin, base_margin, base_margin),
            "layout_spacing": self._scaled(7, scale, 4),
            "mode_pad": self._scaled(2, scale, 1),
            "row_spacing": self._scaled(6, scale, 4),
            "card_margins": (
                self._scaled(8, scale, 5),
                self._scaled(7, scale, 4),
                self._scaled(8, scale, 5),
                self._scaled(7, scale, 4),
            ),
            "card_spacing": self._scaled(6, scale, 4),
        }

    def _single_url_layout_metrics(self, scale: float) -> dict[str, int]:
        return _single_url_baseline_metrics(scale)

    def _apply_settings_control_heights(self, scale: float) -> None:
        settings_button_height = self._scaled(32, scale, 24)
        self._set_uniform_fixed_height(
            (
                self.download_path_browse_btn,
                self.check_updates_button,
                self.ffmpeg_install_button,
                self.node_install_button,
                self.history_open_file_button,
                self.history_open_folder_button,
                self.history_retry_button,
                self.history_clear_button,
            ),
            settings_button_height,
        )
        combo_height = self._scaled(28, scale, 18)
        self._set_uniform_fixed_height(
            (
                self.filename_template_combo,
                self.filename_template_custom_edit,
                self.conflict_policy_combo,
                self.retry_profile_combo,
                self.stale_part_cleanup_combo,
                self.history_combo,
            ),
            combo_height,
        )
        self._set_uniform_fixed_height(
            (self.speed_limit_slider, self.batch_retry_slider),
            self._scaled(20, scale, 12),
        )
        self._set_uniform_fixed_height(
            (self.single_mode_button, self.multi_mode_button),
            self._scaled(30, scale, 20),
        )
        min_checkbox_height = self._scaled(26, scale, 16)
        for checkbox in (
            self.auto_updates_checkbox,
            self.skip_existing_checkbox,
            self.adaptive_concurrency_checkbox,
            self.auto_start_ready_links_checkbox,
            self.disable_metadata_fetch_checkbox,
            self.fallback_metadata_checkbox,
            self.disable_history_checkbox,
        ):
            checkbox.setMinimumHeight(min_checkbox_height)

    def _apply_scaled_metrics(self, scale: float) -> None:
        _margin_x, console_margin_y = self._apply_scaled_global_layout_metrics(scale)
        self._apply_scaled_settings_layout_metrics(scale)
        batch_mode_enabled = self._apply_scaled_batch_toolbar_metrics(scale)
        self._apply_scaled_single_section_metrics(
            scale,
            batch_mode_enabled=batch_mode_enabled,
            console_margin_y=console_margin_y,
        )
        self._apply_scaled_footer_metrics(scale)
        self.settings_panel.setMinimumWidth(self._scaled(208, scale, 162))
        self._settings_min_width = max(self.settings_panel.minimumSizeHint().width(), self.settings_panel.minimumWidth())
        self._settings_target_width = max(
            self._settings_min_width,
            self._scaled(self._base_settings_width, scale, 160),
        )
        self._sync_batch_entry_row_scales(scale)

    def _apply_scaled_global_layout_metrics(self, scale: float) -> tuple[int, int]:
        self._outer_layout.setContentsMargins(
            self._scaled(8, scale, 5),
            self._scaled(8, scale, 5),
            self._scaled(8, scale, 5),
            self._scaled(4, scale, 3),
        )
        self._outer_layout.setSpacing(self._scaled(5, scale, 3))
        self._content_row_layout.setSpacing(self._scaled(6, scale, 3))
        self._main_layout.setSpacing(self._scaled(5, scale, 3))

        margin_x = self._scaled(8, scale, 5)
        margin_y = self._scaled(6, scale, 4)
        self._header_layout.setContentsMargins(margin_x, self._scaled(4, scale, 2), margin_x, self._scaled(4, scale, 2))
        self._header_layout.setSpacing(self._scaled(1, scale, 1))
        self._input_layout.setContentsMargins(margin_x, margin_y, margin_x, margin_y)
        self._input_layout.setSpacing(self._scaled(5, scale, 3))
        self._url_row_layout.setSpacing(self._scaled(7, scale, 4))
        self._format_grid_layout.setHorizontalSpacing(self._scaled(7, scale, 4))
        self._format_grid_layout.setVerticalSpacing(self._scaled(5, scale, 3))
        self._format_grid_layout.setContentsMargins(0, 0, 0, 0)
        self._action_row_layout.setSpacing(self._scaled(7, scale, 4))
        self._batch_inline_layout.setContentsMargins(0, 0, 0, 0)
        self._batch_inline_layout.setSpacing(self._scaled(5, scale, 3))
        self._multi_toolbar_layout.setSpacing(self._scaled(6, scale, 4))
        self._multi_filter_layout.setSpacing(self._scaled(6, scale, 4))
        console_margin_y = self._scaled(6, scale, 4)
        self._console_layout.setContentsMargins(
            margin_x,
            console_margin_y,
            margin_x,
            console_margin_y,
        )
        header_margin_y = self._scaled(4, scale, 2)
        title_h = QFontMetrics(self.title_label.font()).lineSpacing()
        subtitle_h = QFontMetrics(self.subtitle_label.font()).lineSpacing()
        header_height = title_h + subtitle_h + self._header_layout.spacing() + (header_margin_y * 2) + self._scaled(6, scale, 4)
        self.header_card.setFixedHeight(max(self._scaled(52, scale, 34), header_height))
        return margin_x, console_margin_y

    def _apply_scaled_settings_layout_metrics(self, scale: float) -> None:
        settings_metrics = self._settings_layout_metrics(scale)
        settings_root_inset = int(settings_metrics["root_inset"])
        self._settings_root_layout.setContentsMargins(
            settings_root_inset,
            settings_root_inset,
            settings_root_inset,
            settings_root_inset,
        )
        settings_margins = tuple(settings_metrics["layout_margins"])
        self._settings_layout.setContentsMargins(*settings_margins)
        self._settings_layout.setSpacing(int(settings_metrics["layout_spacing"]))
        mode_pad = int(settings_metrics["mode_pad"])
        self._mode_holder_layout.setContentsMargins(mode_pad, mode_pad, mode_pad, mode_pad)
        self._mode_holder_layout.setSpacing(self._scaled(2, scale, 1))
        shared_row_spacing = int(settings_metrics["row_spacing"])
        self._ui_size_row_layout.setSpacing(shared_row_spacing)
        self._concurrency_row_layout.setSpacing(shared_row_spacing)
        self._retry_row_layout.setSpacing(shared_row_spacing)
        self._speed_row_layout.setSpacing(shared_row_spacing)
        self._footer_layout.setContentsMargins(
            self._scaled(2, scale, 1),
            0,
            self._scaled(2, scale, 1),
            self._scaled(2, scale, 1),
        )
        self._footer_layout.setSpacing(self._scaled(8, scale, 4))
        card_margins = tuple(settings_metrics["card_margins"])
        card_spacing = int(settings_metrics["card_spacing"])
        for card_layout in self._settings_card_layouts:
            card_layout.setContentsMargins(*card_margins)
            card_layout.setSpacing(card_spacing)

    def _apply_scaled_batch_toolbar_metrics(self, scale: float) -> bool:
        self._batch_mode_extra_height = 0
        self._batch_mode_extra_width = self._scaled(200, scale, 120)
        self.multi_toolbar_row.setFixedHeight(self._scaled(38, scale, 26))
        self.multi_add_input.setFixedHeight(self._scaled(34, scale, 24))
        toolbar_btn_h = self.multi_add_input.height()
        self.multi_add_button.setFixedHeight(toolbar_btn_h)
        self.multi_bulk_button.setFixedHeight(toolbar_btn_h)
        self.multi_import_button.setFixedHeight(toolbar_btn_h)
        self.multi_export_button.setFixedHeight(toolbar_btn_h)
        self.multi_add_button.setFixedWidth(self._scaled(82, scale, 62))
        self.multi_bulk_button.setFixedWidth(self._scaled(112, scale, 82))
        self.multi_import_button.setFixedWidth(self._scaled(82, scale, 62))
        self.multi_export_button.setFixedWidth(self._scaled(102, scale, 76))
        self.multi_search_input.setFixedHeight(self._scaled(32, scale, 22))
        self.multi_status_filter.setFixedHeight(self.multi_search_input.height())
        self.multi_status_filter.setFixedWidth(self._scaled(122, scale, 90))
        self.multi_filter_row.setFixedHeight(self.multi_search_input.height())
        self.multi_filter_gap.setFixedHeight(self._scaled(4, scale, 2))
        batch_mode_enabled = self._is_batch_mode_enabled()
        if self._settings_visible and (not batch_mode_enabled):
            self._format_grid_layout.setColumnStretch(0, 9)
            self._format_grid_layout.setColumnStretch(1, 12)
            self._format_grid_layout.setColumnMinimumWidth(0, self._scaled(94, scale, 70))
            self._format_grid_layout.setColumnMinimumWidth(1, self._scaled(144, scale, 104))
        else:
            self._format_grid_layout.setColumnStretch(0, 1)
            self._format_grid_layout.setColumnStretch(1, 1)
            self._format_grid_layout.setColumnMinimumWidth(0, self._scaled(80, scale, 62))
            self._format_grid_layout.setColumnMinimumWidth(1, self._scaled(136, scale, 98))
        return batch_mode_enabled

    def _apply_format_quality_width_policy(self) -> None:
        self.format_combo.setMinimumWidth(0)
        self.format_combo.setMaximumWidth(16777215)
        self.quality_combo.setMinimumWidth(0)
        self.quality_combo.setMaximumWidth(16777215)
        if self._is_batch_mode_enabled():
            self._format_quality_layout.setStretch(0, 1)
            self._format_quality_layout.setStretch(1, 1)
            return
        self._format_quality_layout.setStretch(0, 0)
        self._format_quality_layout.setStretch(1, 1)
        target_width = max(self._scaled(80, self._render_scale, 62), int(self.download_button.width()))
        self.format_combo.setFixedWidth(target_width)

    def _apply_scaled_single_section_metrics(
        self,
        scale: float,
        *,
        batch_mode_enabled: bool,
        console_margin_y: int,
    ) -> None:
        single_metrics = self._single_url_layout_metrics(scale)
        self._apply_scaled_single_base_sizes(
            scale,
            batch_mode_enabled=batch_mode_enabled,
            console_margin_y=console_margin_y,
            single_metrics=single_metrics,
        )
        thumb_top_offset, thumb_bottom_offset, status_h = self._apply_single_layout_spacing_metrics(single_metrics)
        visible_info_lines = 2
        self._set_single_meta_info_line_visibility(visible_info_lines)
        combo_h, action_h = self._single_combo_action_heights(single_metrics)
        controls_row_h = combo_h + max(0, self._format_grid_layout.verticalSpacing()) + action_h
        row_h = self._single_meta_row_height(
            scale,
            single_metrics=single_metrics,
            controls_row_h=controls_row_h,
            visible_info_lines=visible_info_lines,
            status_h=status_h,
        )
        self._apply_single_row_geometry(
            scale,
            batch_mode_enabled=batch_mode_enabled,
            single_metrics=single_metrics,
            row_h=row_h,
            status_h=status_h,
            thumb_top_offset=thumb_top_offset,
            thumb_bottom_offset=thumb_bottom_offset,
            combo_h=combo_h,
            action_h=action_h,
        )
        self._apply_settings_control_heights(scale)

    def _apply_scaled_single_base_sizes(
        self,
        scale: float,
        *,
        batch_mode_enabled: bool,
        console_margin_y: int,
        single_metrics: dict[str, int],
    ) -> None:
        multi_entries_h = self._scaled(258, scale, 160) if batch_mode_enabled else self._scaled(220, scale, 130)
        self._multi_entries_scroll_default_height = multi_entries_h
        self.multi_entries_scroll.setMinimumHeight(multi_entries_h)
        self.multi_entries_scroll.setMaximumHeight(16777215)
        progress_h = int(single_metrics["progress_bar_height"])
        if batch_mode_enabled:
            self._single_url_row.setFixedHeight(0)
            self._single_progress_gap.setFixedHeight(0)
            self.download_progress.setFixedHeight(0)
        else:
            url_row_h = max(self.single_url_input.sizeHint().height(), self.paste_button.sizeHint().height())
            self._single_url_row.setFixedHeight(max(1, int(url_row_h)))
            self.download_progress.setFixedHeight(progress_h)
        console_height = self._scaled(98, scale, 54)
        console_card_height = console_height + (console_margin_y * 2) + 2
        self.console_output.setMinimumHeight(console_height)
        self.console_output.setMaximumHeight(console_height)
        self.console_card.setMinimumHeight(console_card_height)
        self.console_card.setMaximumHeight(console_card_height)
        self.paste_button.setFixedWidth(self._scaled(88, scale, 76))

    def _apply_single_layout_spacing_metrics(self, single_metrics: dict[str, int]) -> tuple[int, int, int]:
        self._single_features_layout.setContentsMargins(
            int(single_metrics["features_left_margin"]),
            int(single_metrics["features_top_margin"]),
            int(single_metrics["features_right_margin"]),
            int(single_metrics["features_bottom_margin"]),
        )
        self._single_features_layout.setSpacing(int(single_metrics["features_spacing"]))
        self._single_meta_layout.setContentsMargins(0, 0, 0, 0)
        self._single_meta_layout.setSpacing(int(single_metrics["meta_spacing"]))
        self._single_meta_text_layout.setContentsMargins(0, 0, 0, 0)
        self._single_meta_text_layout.setSpacing(int(single_metrics["meta_text_spacing"]))
        self._single_meta_top_row.setSpacing(int(single_metrics["meta_top_row_spacing"]))
        self._single_meta_bottom_row.setSpacing(int(single_metrics["meta_bottom_row_spacing"]))

        thumb_top_offset = int(single_metrics["thumb_top_offset"])
        thumb_bottom_offset = int(single_metrics["thumb_bottom_offset"])
        self._single_thumbnail_holder_layout.setContentsMargins(0, thumb_top_offset, 0, thumb_bottom_offset)
        self._single_meta_bottom_row.setContentsMargins(0, 0, 0, int(single_metrics["status_bottom_offset"]))
        status_h = int(single_metrics["status_height"])
        return thumb_top_offset, thumb_bottom_offset, status_h

    def _set_single_meta_info_line_visibility(self, visible_info_lines: int) -> None:
        for index, info_label in enumerate(self.single_meta_info_labels):
            info_label.setVisible(index < visible_info_lines)

    def _single_combo_action_heights(self, single_metrics: dict[str, int]) -> tuple[int, int]:
        combo_height_bump = int(single_metrics["combo_height_bump"])
        button_height_bump = int(single_metrics["button_height_bump"])
        combo_h = max(self.format_combo.sizeHint().height(), self.quality_combo.sizeHint().height()) + combo_height_bump
        action_h = max(self.download_button.sizeHint().height(), self.stop_button.sizeHint().height()) + button_height_bump
        return combo_h, action_h

    def _single_meta_row_height(
        self,
        scale: float,
        *,
        single_metrics: dict[str, int],
        controls_row_h: int,
        visible_info_lines: int,
        status_h: int,
    ) -> int:
        title_h = QFontMetrics(self.single_meta_title_label.font()).lineSpacing()
        info_h = QFontMetrics(self.single_meta_size_label.font()).lineSpacing()
        info_rows = 1 + visible_info_lines
        text_spacing = max(0, self._single_meta_text_layout.spacing())
        text_lines_h = title_h + (info_h * info_rows) + status_h
        text_spacings_h = text_spacing * (2 + info_rows)
        text_bottom_guard = int(single_metrics["status_bottom_offset"]) + self._scaled(6, scale, 3)
        row_h_base = max(
            int(single_metrics["row_base_min"]),
            text_lines_h + text_spacings_h + text_bottom_guard,
            controls_row_h,
        )
        compact_delta = int(single_metrics["compact_delta"])
        controls_row_guard = controls_row_h + self._scaled(6, scale, 4)
        return max(
            controls_row_guard,
            int(single_metrics["row_min"]),
            row_h_base - compact_delta,
        )

    def _apply_single_row_geometry(
        self,
        scale: float,
        *,
        batch_mode_enabled: bool,
        single_metrics: dict[str, int],
        row_h: int,
        status_h: int,
        thumb_top_offset: int,
        thumb_bottom_offset: int,
        combo_h: int,
        action_h: int,
    ) -> None:
        self.format_combo.setFixedHeight(combo_h)
        self.quality_combo.setFixedHeight(combo_h)
        self.download_button.setFixedHeight(action_h)
        self.pause_resume_button.setFixedHeight(action_h)
        self.stop_button.setFixedHeight(action_h)
        if batch_mode_enabled:
            self._action_row_layout.setStretch(0, 10)
            self._action_row_layout.setStretch(1, 11)
            self._action_row_layout.setStretch(2, 5)
        elif self._settings_visible:
            self._action_row_layout.setStretch(0, 12)
            self._action_row_layout.setStretch(1, 14)
            self._action_row_layout.setStretch(2, 5)
        else:
            self._action_row_layout.setStretch(0, 12)
            self._action_row_layout.setStretch(1, 13)
            self._action_row_layout.setStretch(2, 6)

        controls_mid_gap = int(single_metrics["controls_mid_gap"])
        controls_block_h = combo_h + action_h + max(0, controls_mid_gap)
        if batch_mode_enabled:
            self._format_grid_layout.setContentsMargins(0, 0, 0, 0)
            self._format_grid_layout.setVerticalSpacing(controls_mid_gap)
            self._single_controls_col.setFixedHeight(controls_block_h)
            single_frame_h = (
                controls_block_h
                + self._single_features_layout.contentsMargins().top()
                + self._single_features_layout.contentsMargins().bottom()
            )
            self.single_features_row.setFixedHeight(single_frame_h)
            self._single_progress_gap.setFixedHeight(0)
            self._apply_format_quality_width_policy()
            return

        self.single_meta_row.setFixedHeight(row_h)
        max_thumb_h_from_row = max(
            1,
            row_h
            - self._single_meta_layout.contentsMargins().top()
            - self._single_meta_layout.contentsMargins().bottom()
            - thumb_top_offset
            - thumb_bottom_offset,
        )
        thumb_h = max_thumb_h_from_row
        thumb_w = min(
            int(single_metrics["thumb_width_max"]),
            max(int(single_metrics["thumb_width_min"]), int(round(thumb_h * 1.25))),
        )
        thumb_total_h = thumb_h + thumb_top_offset + thumb_bottom_offset
        self.single_meta_thumbnail_label.setFixedSize(thumb_w, thumb_h)
        self._single_thumbnail_holder.setFixedSize(thumb_w, thumb_total_h)
        self._single_controls_col.setFixedHeight(thumb_total_h)
        self._single_meta_text_col.setFixedHeight(thumb_total_h)
        self.single_meta_status_label.setFixedHeight(status_h)

        spare_h = max(0, thumb_total_h - controls_block_h)
        controls_top_gap = spare_h // 2
        controls_bottom_gap = spare_h - controls_top_gap
        self._format_grid_layout.setContentsMargins(0, controls_top_gap, 0, controls_bottom_gap)
        self._format_grid_layout.setVerticalSpacing(controls_mid_gap)

        single_frame_h = (
            row_h
            + self._single_features_layout.contentsMargins().top()
            + self._single_features_layout.contentsMargins().bottom()
        )
        self.single_features_row.setFixedHeight(single_frame_h)
        self._single_progress_gap.setFixedHeight(self._scaled(16, scale, 10))
        self._apply_format_quality_width_policy()

    def _apply_scaled_footer_metrics(self, scale: float) -> None:
        padding = self._scaled(12, scale, 8)
        batch_width = QFontMetrics(self.batch_concurrency_value_label.font()).horizontalAdvance(
            str(self.batch_concurrency_slider.maximum())
        )
        retry_width = QFontMetrics(self.batch_retry_value_label.font()).horizontalAdvance(
            str(self.batch_retry_slider.maximum())
        )
        speed_metrics = QFontMetrics(self.speed_limit_value_label.font())
        speed_width = max(
            speed_metrics.horizontalAdvance(_format_speed_limit_label(item))
            for item in _SPEED_LIMIT_VALUES_KBPS
        )
        ui_size_width = QFontMetrics(self.ui_scale_value_label.font()).horizontalAdvance(f"{int(UI_SCALE_MAX)}%")
        self.batch_concurrency_value_label.setMinimumWidth(max(self._scaled(28, scale, 22), batch_width + padding))
        self.batch_retry_value_label.setMinimumWidth(max(self._scaled(24, scale, 18), retry_width + padding))
        self.speed_limit_value_label.setMinimumWidth(max(self._scaled(88, scale, 54), speed_width + padding))
        self.ui_scale_value_label.setMinimumWidth(max(self._scaled(52, scale, 38), ui_size_width + padding))
        icon_px = self._scaled(18, scale, 16)
        self.theme_toggle_button.setIconSize(QSize(icon_px, icon_px))
        self.batch_preflight_label.setFixedHeight(self._scaled(18, scale, 12))
        self._batch_inline_target_height = self._compute_batch_inline_target_height()

    def _sync_batch_entry_row_scales(self, scale: float) -> None:
        normalized = _normalize_scale_factor(scale)
        for row in self._batch_entry_widgets.values():
            row.set_ui_scale(normalized)

    def _refresh_control_style_metrics(self, scale: float) -> None:
        handle_size = self._scaled(18, scale, 14)
        groove_height = self._scaled(6, scale, 4)
        check_size = self._scaled(20, scale, 14)
        check_radius = self._scaled(4, scale, 2)
        for style in self._slider_styles:
            style.set_metrics(handle_size=handle_size, groove_height=groove_height)
        for style in self._checkbox_styles:
            style.set_metrics(size=check_size, radius=check_radius)
        self.ui_scale_slider.update()
        for checkbox in self.findChildren(QCheckBox):
            checkbox.update()

    def _refresh_control_style_colors(self) -> None:
        for style in self._slider_styles:
            style.set_colors(
                handle_color=self.theme.text_primary,
                border_color=self.theme.border,
                groove_color=self.theme.border,
                fill_color=self.theme.accent,
            )
        for style in self._checkbox_styles:
            style.set_colors(
                border_color=self.theme.border,
                fill_color=self.theme.accent,
                check_color=self.theme.text_primary,
            )
        self.ui_scale_slider.update()
        for checkbox in self.findChildren(QCheckBox):
            checkbox.update()

    def _apply_manual_dpi_scale(self, scale: float) -> None:
        normalized = _normalize_scale_factor(scale)
        self._render_scale = normalized
        self.setStyleSheet(build_stylesheet(self.theme, normalized))
        self._apply_scaled_metrics(normalized)
        self._refresh_control_style_metrics(normalized)

    def _apply_window_layout(self) -> None:
        self.setUpdatesEnabled(False)
        try:
            geometry = self._available_screen_geometry()
            self._apply_manual_dpi_scale(self._resolve_render_scale())
            desired_batch_height = self._batch_inline_target_height if self._is_batch_mode_enabled() else 0
            self._set_batch_inline_section_height(desired_batch_height)

            width, height = self._compute_dimensions(self._render_scale)
            if geometry is not None:
                width = min(width, max(1, int(geometry.width() * 0.92)))
                height = min(height, max(1, int(geometry.height() * 0.92)))
            width = max(1, width)
            height = max(1, height)
            self.setFixedSize(width, height)
            self.resize(width, height)

            self._settings_target_width = self._compute_settings_target_width(self._render_scale, self.width())
            self._settings_animation_expected_end_width = None
            self.settings_animation.stop()
            if self._settings_visible:
                self._set_settings_container_width(self._settings_target_width)
            else:
                self._set_settings_container_width(0)
            self._refresh_settings_scroll_metrics()
            self._refresh_single_meta_display()
            self._schedule_single_meta_refresh()
            self._apply_single_meta_thumbnail_pixmap()
            self._apply_format_quality_width_policy()
            self._sync_tutorial_overlay()
        finally:
            self.setUpdatesEnabled(True)

    def _refresh_settings_scroll_metrics(self) -> None:
        self.settings_scroll.update()

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("mcRoot")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 6)
        outer.setSpacing(7)
        self._outer_layout = outer

        row = QHBoxLayout()
        row.setSpacing(8)
        self._content_row_layout = row
        outer.addLayout(row, 1)

        self._build_main_section(root, row)
        self._build_settings_section(root, row)
        self._build_footer_section(root, outer)
        self._build_tutorial_overlay(root)

    def _build_main_section(self, root: QWidget, row: QHBoxLayout) -> None:
        self.main_column = QWidget(root)
        self.main_column.setObjectName("mainColumn")
        main_layout = QVBoxLayout(self.main_column)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(7)
        self._main_layout = main_layout
        row.addWidget(self.main_column, 1)

        self._build_main_header_card()
        self._build_main_input_card()
        self._build_main_console_card()

    def _build_main_header_card(self) -> None:
        header = QFrame(self.main_column)
        self.header_card = header
        header.setObjectName("card")
        header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(2)
        self._header_layout = header_layout
        self.title_label = QLabel(APP_NAME, header)
        self.title_label.setObjectName("title")
        self.subtitle_label = QLabel(SUBTITLE_TEXT, header)
        self.subtitle_label.setObjectName("subtitle")
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        self._main_layout.addWidget(header)


    def _build_main_input_card(self) -> None:
        input_card = QFrame(self.main_column)
        self._main_input_card = input_card
        input_card.setObjectName("card")
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setSpacing(6)
        self._input_layout = input_layout

        self._build_batch_inline_ui(input_card, input_layout)
        self._build_single_url_input_ui(input_card, input_layout)
        self._build_single_features_ui(input_card, input_layout)
        self._build_single_progress_ui(input_card, input_layout)
        self._rebuild_input_layout_for_mode(batch_mode_enabled=False)
        self._main_layout.addWidget(input_card)

    def _rebuild_input_layout_for_mode(self, *, batch_mode_enabled: bool) -> None:
        ordered_widgets = (
            self.batch_inline_section,
            self._single_url_row,
            self.single_features_row,
            self._single_progress_gap,
            self.download_progress,
        )
        for widget in ordered_widgets:
            self._input_layout.removeWidget(widget)
        if batch_mode_enabled:
            self._input_layout.addWidget(self.batch_inline_section, 1)
            self._input_layout.addWidget(self.single_features_row, 0)
            return
        self._input_layout.addWidget(self.batch_inline_section, 0)
        self._input_layout.addWidget(self._single_url_row, 0)
        self._input_layout.addWidget(self.single_features_row, 0)
        self._input_layout.addWidget(self._single_progress_gap, 0)
        self._input_layout.addWidget(self.download_progress, 0)

    def _build_batch_inline_ui(self, input_card: QFrame, input_layout: QVBoxLayout) -> None:
        self.batch_inline_section = QWidget(input_card)
        self.batch_inline_section.setObjectName("batchInlineSection")
        self.batch_inline_section.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        batch_inline_layout = QVBoxLayout(self.batch_inline_section)
        batch_inline_layout.setContentsMargins(0, 0, 0, 0)
        batch_inline_layout.setSpacing(5)
        self._batch_inline_layout = batch_inline_layout

        self.multi_toolbar_row = QWidget(self.batch_inline_section)
        self.multi_toolbar_row.setObjectName("multiToolbarRow")
        multi_toolbar_layout = QHBoxLayout(self.multi_toolbar_row)
        multi_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        multi_toolbar_layout.setSpacing(6)
        self._multi_toolbar_layout = multi_toolbar_layout
        self.multi_add_input = QLineEdit(self.multi_toolbar_row)
        self.multi_add_input.setObjectName("multiAddInput")
        self.multi_add_input.setPlaceholderText("Add URL, press Enter")
        self.multi_add_button = QPushButton("PASTE", self.multi_toolbar_row)
        self.multi_add_button.setObjectName("settingsActionButton")
        self.multi_bulk_button = QPushButton("BULK PASTE", self.multi_toolbar_row)
        self.multi_bulk_button.setObjectName("settingsActionButton")
        self.multi_import_button = QPushButton("IMPORT", self.multi_toolbar_row)
        self.multi_import_button.setObjectName("settingsActionButton")
        self.multi_export_button = QPushButton("EXPORT", self.multi_toolbar_row)
        self.multi_export_button.setObjectName("settingsActionButton")
        multi_toolbar_layout.addWidget(self.multi_add_input, 1)
        multi_toolbar_layout.addWidget(self.multi_add_button, 0)
        multi_toolbar_layout.addWidget(self.multi_bulk_button, 0)
        multi_toolbar_layout.addWidget(self.multi_import_button, 0)
        multi_toolbar_layout.addWidget(self.multi_export_button, 0)
        batch_inline_layout.addWidget(self.multi_toolbar_row)

        self.batch_preflight_label = QLabel(
            format_batch_stats_line(
                downloaded=0,
                downloading=0,
                in_progress=0,
                queued=0,
                valid=0,
                invalid=0,
                duplicates=0,
                pending=0,
            ),
            self.batch_inline_section,
        )
        self.batch_preflight_label.setObjectName("muted")
        batch_inline_layout.addWidget(self.batch_preflight_label)
        self.multi_filter_row = QWidget(self.batch_inline_section)
        self.multi_filter_row.setObjectName("multiFilterRow")
        multi_filter_layout = QHBoxLayout(self.multi_filter_row)
        multi_filter_layout.setContentsMargins(0, 0, 0, 0)
        multi_filter_layout.setSpacing(6)
        self._multi_filter_layout = multi_filter_layout
        self.multi_search_input = QLineEdit(self.multi_filter_row)
        self.multi_search_input.setObjectName("multiSearchInput")
        self.multi_search_input.setPlaceholderText("Search URL or title")
        self.multi_search_input.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.multi_status_filter = ChevronComboBox(self.multi_filter_row)
        self.multi_status_filter.setObjectName("multiStatusFilter")
        self.multi_status_filter.addItems(["ALL", "READY", "ACTIVE", "PAUSED", "DONE", "FAILED"])
        self.multi_status_filter.setCurrentText("ALL")
        self.multi_status_filter.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        multi_filter_layout.addWidget(self.multi_search_input, 1, Qt.AlignVCenter)
        multi_filter_layout.addWidget(self.multi_status_filter, 0, Qt.AlignVCenter)
        batch_inline_layout.addWidget(self.multi_filter_row)
        self.multi_filter_gap = QWidget(self.batch_inline_section)
        self.multi_filter_gap.setObjectName("multiFilterGap")
        self.multi_filter_gap.setFixedHeight(4)
        self.multi_filter_gap.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        batch_inline_layout.addWidget(self.multi_filter_gap)

        self.multi_entries_scroll = QScrollArea(self.batch_inline_section)
        self.multi_entries_scroll.setObjectName("multiEntriesScroll")
        self.multi_entries_scroll.setWidgetResizable(True)
        self.multi_entries_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.multi_entries_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.multi_entries_scroll.setFrameShape(QFrame.NoFrame)
        self.multi_entries_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.multi_entries_container = QWidget(self.multi_entries_scroll)
        self.multi_entries_container.setObjectName("multiEntriesContainer")
        self.multi_entries_scroll.setWidget(self.multi_entries_container)
        multi_entries_layout = QVBoxLayout(self.multi_entries_container)
        multi_entries_layout.setContentsMargins(0, 0, 0, 0)
        multi_entries_layout.setSpacing(6)
        self._multi_entries_layout = multi_entries_layout
        self.multi_empty_label = QLabel("No links yet. Add one above or use Bulk paste.", self.multi_entries_container)
        self.multi_empty_label.setObjectName("muted")
        self._multi_entries_layout.addWidget(self.multi_empty_label)
        self._multi_entries_layout.addStretch(1)
        batch_inline_layout.addWidget(self.multi_entries_scroll)

        self.batch_inline_section.hide()
        self.batch_inline_section.setMinimumHeight(0)
        self.batch_inline_section.setMaximumHeight(0)
        input_layout.addWidget(self.batch_inline_section)


    def _build_single_url_input_ui(self, input_card: QFrame, input_layout: QVBoxLayout) -> None:
        self._single_url_row = QWidget(input_card)
        self._single_url_row.setObjectName("singleUrlRow")
        url_row = QHBoxLayout(self._single_url_row)
        url_row.setContentsMargins(0, 0, 0, 0)
        url_row.setSpacing(7)
        self._url_row_layout = url_row
        self.single_url_input = QLineEdit(input_card)
        self.single_url_input.setObjectName("singleUrlInput")
        self.single_url_input.setPlaceholderText("Paste media link/URL")

        url_holder = QWidget(input_card)
        url_holder_layout = QVBoxLayout(url_holder)
        url_holder_layout.setContentsMargins(0, 0, 0, 0)
        url_holder_layout.setSpacing(0)
        url_holder_layout.addWidget(self.single_url_input)
        url_row.addWidget(url_holder, 1)

        self.paste_button = QPushButton("PASTE", input_card)
        self.paste_button.setObjectName("pasteButton")
        self.paste_button.setFixedWidth(88)
        url_row.addWidget(self.paste_button, 0, Qt.AlignTop)
        input_layout.addWidget(self._single_url_row)


    def _build_single_features_ui(self, input_card: QFrame, input_layout: QVBoxLayout) -> None:
        self.single_features_row = QFrame(input_card)
        self.single_features_row.setObjectName("card")
        self.single_features_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        single_features_layout = QHBoxLayout(self.single_features_row)
        single_features_layout.setContentsMargins(7, 6, 7, 6)
        single_features_layout.setSpacing(6)
        self._single_features_layout = single_features_layout

        single_meta_row = self._build_single_meta_panel(input_card)
        single_features_layout.addWidget(single_meta_row, 13)
        single_controls_col = self._build_single_controls_panel(input_card)
        single_features_layout.addWidget(single_controls_col, 9, Qt.AlignTop)
        self._init_single_input_animations()
        input_layout.addWidget(self.single_features_row)

    def _build_single_meta_panel(self, input_card: QFrame) -> QFrame:
        self.single_meta_row = QFrame(input_card)
        self.single_meta_row.setObjectName("singleMetaPanel")
        self.single_meta_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        single_meta_layout = QHBoxLayout(self.single_meta_row)
        single_meta_layout.setContentsMargins(0, 0, 0, 0)
        single_meta_layout.setSpacing(6)
        self._single_meta_layout = single_meta_layout

        self._single_thumbnail_holder = QWidget(self.single_meta_row)
        self._single_thumbnail_holder.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        single_thumbnail_holder_layout = QVBoxLayout(self._single_thumbnail_holder)
        single_thumbnail_holder_layout.setContentsMargins(0, 0, 0, 0)
        single_thumbnail_holder_layout.setSpacing(0)
        self._single_thumbnail_holder_layout = single_thumbnail_holder_layout

        self.single_meta_thumbnail_label = QLabel("THUMBNAIL", self._single_thumbnail_holder)
        self.single_meta_thumbnail_label.setObjectName("batchEntryThumbnail")
        self.single_meta_thumbnail_label.setAlignment(Qt.AlignCenter)
        self.single_meta_thumbnail_label.setFixedSize(90, 72)
        self.single_meta_thumbnail_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        single_thumbnail_holder_layout.addWidget(self.single_meta_thumbnail_label, 0, Qt.AlignTop)
        single_meta_layout.addWidget(self._single_thumbnail_holder, 0, Qt.AlignTop)

        single_meta_text_col = QWidget(self.single_meta_row)
        self._single_meta_text_col = single_meta_text_col
        single_meta_text_layout = QVBoxLayout(single_meta_text_col)
        single_meta_text_layout.setContentsMargins(0, 0, 0, 0)
        single_meta_text_layout.setSpacing(3)
        self._single_meta_text_layout = single_meta_text_layout
        single_meta_layout.addWidget(single_meta_text_col, 1, Qt.AlignTop)

        single_meta_top_row = QHBoxLayout()
        single_meta_top_row.setContentsMargins(0, 0, 0, 0)
        single_meta_top_row.setSpacing(4)
        self._single_meta_top_row = single_meta_top_row
        self.single_meta_title_label = QLabel("", single_meta_text_col)
        self.single_meta_title_label.setObjectName("singleMetaTitle")
        self.single_meta_title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.single_meta_title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.single_meta_title_label.setWordWrap(False)
        single_meta_top_row.addWidget(self.single_meta_title_label, 1)
        single_meta_text_layout.addLayout(single_meta_top_row)

        self.single_meta_size_label = QLabel("", single_meta_text_col)
        self.single_meta_size_label.setObjectName("singleMetaInfoLine")
        self.single_meta_size_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.single_meta_size_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.single_meta_size_label.setWordWrap(False)
        single_meta_text_layout.addWidget(self.single_meta_size_label)

        self.single_meta_info_labels: list[QLabel] = []
        for _index in range(3):
            info_label = QLabel("", single_meta_text_col)
            info_label.setObjectName("singleMetaInfoLine")
            info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            info_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            info_label.setWordWrap(False)
            single_meta_text_layout.addWidget(info_label)
            self.single_meta_info_labels.append(info_label)

        single_meta_text_layout.addStretch(1)

        single_meta_bottom_row = QHBoxLayout()
        single_meta_bottom_row.setContentsMargins(
            0,
            0,
            0,
            3,
        )
        single_meta_bottom_row.setSpacing(4)
        self._single_meta_bottom_row = single_meta_bottom_row
        self.single_meta_status_label = QLabel("Idle", single_meta_text_col)
        self.single_meta_status_label.setObjectName("singleMetaStatus")
        self.single_meta_status_label.setAlignment(Qt.AlignCenter)
        self.single_meta_status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.single_meta_status_label.setMinimumWidth(0)
        self.single_meta_status_label.setMaximumWidth(16777215)
        self.single_meta_status_label.setFixedHeight(22)
        single_meta_bottom_row.addWidget(self.single_meta_status_label, 1)
        single_meta_text_layout.addLayout(single_meta_bottom_row)
        self.single_meta_row.hide()
        return self.single_meta_row

    def _build_single_controls_panel(self, input_card: QFrame) -> QWidget:
        single_controls_col = QWidget(input_card)
        self._single_controls_col = single_controls_col
        single_controls_col.setObjectName("singleControlsCol")
        single_controls_col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        single_controls_layout = QGridLayout(single_controls_col)
        single_controls_layout.setContentsMargins(0, 0, 0, 0)
        single_controls_layout.setHorizontalSpacing(7)
        single_controls_layout.setVerticalSpacing(5)
        self._format_grid_layout = single_controls_layout

        self._format_quality_row = QWidget(single_controls_col)
        self._format_quality_row.setObjectName("formatQualityRow")
        self._format_quality_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        format_quality_layout = QHBoxLayout(self._format_quality_row)
        format_quality_layout.setContentsMargins(0, 0, 0, 0)
        format_quality_layout.setSpacing(7)
        self._format_quality_layout = format_quality_layout

        self.format_combo = ChevronComboBox(self._format_quality_row)
        self.format_combo.setObjectName("formatCombo")
        self.format_combo.addItems(self._base_formats + [FORMAT_LOAD_LABEL])
        format_quality_layout.addWidget(self.format_combo, 1)

        self.quality_combo = ChevronComboBox(self._format_quality_row)
        self.quality_combo.setObjectName("qualityCombo")
        self.quality_combo.addItems(["BEST QUALITY"])
        format_quality_layout.addWidget(self.quality_combo, 1)
        single_controls_layout.addWidget(self._format_quality_row, 0, 0, 1, 2)
        single_controls_layout.setColumnStretch(0, 1)
        single_controls_layout.setColumnStretch(1, 1)
        single_controls_layout.setRowStretch(0, 0)
        single_controls_layout.setRowStretch(1, 0)
        single_controls_layout.setRowStretch(2, 0)

        action_row_holder = self._build_single_action_row(single_controls_col)
        single_controls_layout.addWidget(action_row_holder, 1, 0, 1, 2)
        return single_controls_col

    def _build_single_action_row(self, parent: QWidget) -> QWidget:
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(7)
        self._action_row_layout = action_row
        self.download_button = QPushButton("DOWNLOAD", parent)
        self.download_button.setObjectName("downloadButton")
        self.pause_resume_button = QPushButton("PAUSE", parent)
        self.pause_resume_button.setObjectName("downloadButton")
        self.pause_resume_button.setEnabled(False)
        self.stop_button = QPushButton("STOP", parent)
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        action_row.addWidget(self.download_button)
        action_row.addWidget(self.pause_resume_button)
        action_row.addWidget(self.stop_button)
        self._action_row_layout.setStretch(0, 12)
        self._action_row_layout.setStretch(1, 13)
        self._action_row_layout.setStretch(2, 6)
        action_row_holder = QWidget(parent)
        action_row_holder.setObjectName("singleActionRow")
        action_row_holder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_row_holder.setLayout(action_row)
        self.single_action_row = action_row_holder
        return action_row_holder

    def _init_single_input_animations(self) -> None:
        self._paste_text_color_anim = QVariantAnimation(self)
        self._paste_text_color_anim.setDuration(520)
        self._paste_text_color_anim.setEasingCurve(QEasingCurve.Linear)
        self._paste_text_color_anim.valueChanged.connect(self._on_paste_text_color_anim_value)
        self._paste_text_color_anim.finished.connect(self._reset_single_url_text_color)

    def _build_single_progress_ui(self, input_card: QFrame, input_layout: QVBoxLayout) -> None:
        self._single_progress_gap = QWidget(input_card)
        self._single_progress_gap.setObjectName("singleProgressGap")
        self._single_progress_gap.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._single_progress_gap.setFixedHeight(10)
        input_layout.addWidget(self._single_progress_gap)

        self.download_progress = QProgressBar(input_card)
        self.download_progress.setObjectName("downloadProgress")
        self.download_progress.setRange(0, 10000)
        self.download_progress.setValue(0)
        self.download_progress.setTextVisible(True)
        self.download_progress.setFormat("0.00%")
        self.download_progress.setAlignment(Qt.AlignCenter)
        self.download_progress.setFixedHeight(24)
        input_layout.addWidget(self.download_progress)



    def _build_main_console_card(self) -> None:
        console_card = QFrame(self.main_column)
        self.console_card = console_card
        console_card.setObjectName("card")
        console_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        console_layout = QVBoxLayout(console_card)
        console_layout.setContentsMargins(10, 5, 10, 5)
        self._console_layout = console_layout
        self.console_output = QPlainTextEdit(console_card)
        self.console_output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.console_output.setReadOnly(True)
        self.console_output.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.console_output.setMaximumBlockCount(1200)
        self.console_output.setMinimumHeight(98)
        self.console_output.setPlaceholderText("Console output")
        console_layout.addWidget(self.console_output, 1)
        self.console_card.setMinimumHeight(112)
        self._main_layout.addWidget(console_card, 1)


    def _build_settings_section(self, root: QWidget, row: QHBoxLayout) -> None:
        settings_content, settings_layout = self._setup_settings_container(root, row)
        self._build_settings_general_card(settings_content, settings_layout)
        self._build_settings_interface_card(settings_content, settings_layout)
        self._build_settings_downloads_card(settings_content, settings_layout)
        self._build_settings_updates_card(settings_content, settings_layout)
        self._build_settings_dependency_card(settings_content, settings_layout)
        self._build_settings_history_card(settings_content, settings_layout)
        settings_layout.addStretch(1)
        self._init_settings_animation()

    def _setup_settings_container(self, root: QWidget, row: QHBoxLayout) -> tuple[QWidget, QVBoxLayout]:
        self.settings_panel = QFrame(root)
        self.settings_panel.setObjectName("settingsPanel")
        self.settings_panel.setMaximumWidth(0)
        self.settings_panel.setMinimumWidth(0)
        settings_root_layout = QVBoxLayout(self.settings_panel)
        settings_root_layout.setContentsMargins(0, 0, 0, 0)
        self._settings_root_layout = settings_root_layout
        row.addWidget(self.settings_panel, 0)

        self.settings_scroll = QScrollArea(self.settings_panel)
        self.settings_scroll.setObjectName("settingsScroll")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_root_layout.addWidget(self.settings_scroll)

        settings_content = QWidget(self.settings_scroll)
        settings_content.setObjectName("settingsBody")
        self.settings_scroll.setWidget(settings_content)
        settings_layout = QVBoxLayout(settings_content)
        settings_layout.setContentsMargins(14, 14, 14, 14)
        settings_layout.setSpacing(7)
        self._settings_layout = settings_layout

        settings_title = QLabel("Settings", settings_content)
        settings_title.setObjectName("title")
        settings_layout.addWidget(settings_title)

        return settings_content, settings_layout

    def _build_settings_general_card(self, settings_content: QWidget, settings_layout: QVBoxLayout) -> None:
        general_card, general_layout = self._create_settings_card("General", settings_content)
        self.settings_general_card = general_card
        mode_label = QLabel("Input mode", general_card)
        mode_label.setObjectName("settingsSubtext")
        general_layout.addWidget(mode_label)
        mode_holder = QFrame(general_card)
        self.mode_holder = mode_holder
        mode_holder.setObjectName("modeHolder")
        mode_holder_layout = QHBoxLayout(mode_holder)
        mode_holder_layout.setContentsMargins(2, 2, 2, 2)
        mode_holder_layout.setSpacing(2)
        self._mode_holder_layout = mode_holder_layout
        self.mode_group = QButtonGroup(general_card)
        self.mode_group.setExclusive(True)
        self.single_mode_button = QPushButton("Single-URL", mode_holder)
        self.single_mode_button.setObjectName("modeButton")
        self.single_mode_button.setCheckable(True)
        self.single_mode_button.setChecked(True)
        self.multi_mode_button = QPushButton("Multi-URL", mode_holder)
        self.multi_mode_button.setObjectName("modeButton")
        self.multi_mode_button.setCheckable(True)
        self.mode_group.addButton(self.single_mode_button)
        self.mode_group.addButton(self.multi_mode_button)
        mode_holder_layout.addWidget(self.single_mode_button, 1)
        mode_holder_layout.addWidget(self.multi_mode_button, 1)
        general_layout.addWidget(mode_holder)
        settings_layout.addWidget(general_card)


    def _build_settings_interface_card(self, settings_content: QWidget, settings_layout: QVBoxLayout) -> None:
        interface_card, interface_layout = self._create_settings_card("Interface", settings_content)
        self.settings_interface_card = interface_card
        scale_row = QHBoxLayout()
        scale_row.setSpacing(6)
        self._ui_size_row_layout = scale_row
        ui_size_label = QLabel("UI size", interface_card)
        ui_size_label.setObjectName("settingsSubtext")
        scale_row.addWidget(ui_size_label)
        self.ui_scale_value_label = QLabel("100%", interface_card)
        self.ui_scale_value_label.setObjectName("muted")
        self.ui_scale_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        scale_row.addStretch(1)
        scale_row.addWidget(self.ui_scale_value_label)
        interface_layout.addLayout(scale_row)
        self.ui_scale_slider = QSlider(Qt.Horizontal, interface_card)
        self.ui_scale_slider.setObjectName("uiScaleSlider")
        self.ui_scale_slider.setMinimum(int(UI_SCALE_MIN))
        self.ui_scale_slider.setMaximum(int(UI_SCALE_MAX))
        self.ui_scale_slider.setSingleStep(max(1, int(UI_SCALE_STEP)))
        self.ui_scale_slider.setPageStep(max(5, int(UI_SCALE_STEP) * 5))
        self.ui_scale_slider.setTickInterval(max(5, int(UI_SCALE_STEP) * 5))
        self.ui_scale_slider.setValue(100)
        interface_layout.addWidget(self.ui_scale_slider)
        settings_layout.addWidget(interface_card)


    def _build_settings_downloads_card(self, settings_content: QWidget, settings_layout: QVBoxLayout) -> None:
        downloads_card, downloads_layout = self._create_settings_card("Downloads", settings_content)
        self.settings_downloads_card = downloads_card
        self._build_download_location_controls(downloads_card, downloads_layout)
        self._build_filename_template_controls(downloads_card, downloads_layout)
        self._build_conflict_policy_controls(downloads_card, downloads_layout)
        self._build_batch_concurrency_controls(downloads_card, downloads_layout)
        self._build_download_behavior_toggles(downloads_card, downloads_layout)
        self._build_retry_controls(downloads_card, downloads_layout)
        self._build_speed_limit_controls(downloads_card, downloads_layout)
        self.adaptive_concurrency_checkbox = QCheckBox("Adaptive concurrency guard", downloads_card)
        self.adaptive_concurrency_checkbox.setChecked(True)
        downloads_layout.addWidget(self.adaptive_concurrency_checkbox)
        settings_layout.addWidget(downloads_card)

    def _build_download_location_controls(self, downloads_card: QFrame, downloads_layout: QVBoxLayout) -> None:
        location_label = QLabel("Location", downloads_card)
        location_label.setObjectName("settingsSubtext")
        downloads_layout.addWidget(location_label)
        location_hint = QLabel("Choose where finished downloads are saved.", downloads_card)
        location_hint.setObjectName("settingsSubtext")
        downloads_layout.addWidget(location_hint)
        self.download_location_edit = QLineEdit(downloads_card)
        self.download_location_edit.setReadOnly(True)
        self.download_path_browse_btn = QPushButton("Change save folder", downloads_card)
        self.download_path_browse_btn.setObjectName("settingsActionButton")
        downloads_layout.addWidget(self.download_location_edit)
        downloads_layout.addWidget(self.download_path_browse_btn)

    def _build_filename_template_controls(self, downloads_card: QFrame, downloads_layout: QVBoxLayout) -> None:
        filename_label = QLabel("Filename template", downloads_card)
        filename_label.setObjectName("settingsSubtext")
        downloads_layout.addWidget(filename_label)
        self.filename_template_combo = ChevronComboBox(downloads_card)
        for label, _template in _FILENAME_TEMPLATE_PRESETS:
            self.filename_template_combo.addItem(label)
        self.filename_template_combo.addItem(_FILENAME_TEMPLATE_CUSTOM_LABEL)
        downloads_layout.addWidget(self.filename_template_combo)
        self.filename_template_custom_edit = QLineEdit(downloads_card)
        self.filename_template_custom_edit.setPlaceholderText(DEFAULT_FILENAME_TEMPLATE)
        self.filename_template_custom_edit.hide()
        downloads_layout.addWidget(self.filename_template_custom_edit)
        self.filename_template_preview_label = QLabel("Preview: Example Title [abc123].mp4", downloads_card)
        self.filename_template_preview_label.setObjectName("muted")
        self.filename_template_preview_label.setWordWrap(True)
        downloads_layout.addWidget(self.filename_template_preview_label)

    def _build_conflict_policy_controls(self, downloads_card: QFrame, downloads_layout: QVBoxLayout) -> None:
        conflict_label = QLabel("When file already exists", downloads_card)
        conflict_label.setObjectName("settingsSubtext")
        downloads_layout.addWidget(conflict_label)
        self.conflict_policy_combo = ChevronComboBox(downloads_card)
        self.conflict_policy_combo.addItems(
            [
                "Skip existing file",
                "Rename output",
                "Overwrite existing file",
            ]
        )
        downloads_layout.addWidget(self.conflict_policy_combo)

    def _build_batch_concurrency_controls(self, downloads_card: QFrame, downloads_layout: QVBoxLayout) -> None:
        concurrency_row = QHBoxLayout()
        concurrency_row.setSpacing(6)
        self._concurrency_row_layout = concurrency_row
        batch_concurrency_label = QLabel("Batch concurrency", downloads_card)
        batch_concurrency_label.setObjectName("settingsSubtext")
        concurrency_row.addWidget(batch_concurrency_label)
        self.batch_concurrency_value_label = QLabel("4", downloads_card)
        self.batch_concurrency_value_label.setObjectName("muted")
        self.batch_concurrency_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        concurrency_row.addStretch(1)
        concurrency_row.addWidget(self.batch_concurrency_value_label)
        downloads_layout.addLayout(concurrency_row)
        self.batch_concurrency_slider = QSlider(Qt.Horizontal, downloads_card)
        self.batch_concurrency_slider.setObjectName("batchConcurrencySlider")
        self.batch_concurrency_slider.setRange(1, 16)
        self.batch_concurrency_slider.setValue(4)
        downloads_layout.addWidget(self.batch_concurrency_slider)

    def _build_download_behavior_toggles(self, downloads_card: QFrame, downloads_layout: QVBoxLayout) -> None:
        self.skip_existing_checkbox = QCheckBox("Skip existing files", downloads_card)
        self.skip_existing_checkbox.setChecked(True)
        downloads_layout.addWidget(self.skip_existing_checkbox)
        self.auto_start_ready_links_checkbox = QCheckBox("Auto-start ready links", downloads_card)
        self.auto_start_ready_links_checkbox.setChecked(False)
        downloads_layout.addWidget(self.auto_start_ready_links_checkbox)
        self.disable_metadata_fetch_checkbox = QCheckBox("Disable metadata fetching", downloads_card)
        self.disable_metadata_fetch_checkbox.setChecked(False)
        downloads_layout.addWidget(self.disable_metadata_fetch_checkbox)
        self.fallback_metadata_checkbox = QCheckBox("Fallback download when metadata fails", downloads_card)
        self.fallback_metadata_checkbox.setChecked(True)
        downloads_layout.addWidget(self.fallback_metadata_checkbox)

    def _build_retry_controls(self, downloads_card: QFrame, downloads_layout: QVBoxLayout) -> None:
        retry_row = QHBoxLayout()
        retry_row.setSpacing(6)
        self._retry_row_layout = retry_row
        retries_label = QLabel("Retries", downloads_card)
        retries_label.setObjectName("settingsSubtext")
        retry_row.addWidget(retries_label)
        retry_row.addStretch(1)
        self.batch_retry_value_label = QLabel("0", downloads_card)
        self.batch_retry_value_label.setObjectName("muted")
        self.batch_retry_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        retry_row.addWidget(self.batch_retry_value_label)
        downloads_layout.addLayout(retry_row)
        self.batch_retry_slider = QSlider(Qt.Horizontal, downloads_card)
        self.batch_retry_slider.setObjectName("batchRetrySlider")
        self.batch_retry_slider.setRange(0, 3)
        self.batch_retry_slider.setSingleStep(1)
        self.batch_retry_slider.setPageStep(1)
        self.batch_retry_slider.setTickInterval(1)
        self.batch_retry_slider.setValue(0)
        downloads_layout.addWidget(self.batch_retry_slider)
        retry_profile_label = QLabel("Retry profile", downloads_card)
        retry_profile_label.setObjectName("settingsSubtext")
        downloads_layout.addWidget(retry_profile_label)
        self.retry_profile_combo = ChevronComboBox(downloads_card)
        for label, profile_value in _RETRY_PROFILE_LABELS:
            self.retry_profile_combo.addItem(label, profile_value)
        downloads_layout.addWidget(self.retry_profile_combo)

    def _build_speed_limit_controls(self, downloads_card: QFrame, downloads_layout: QVBoxLayout) -> None:
        speed_row = QHBoxLayout()
        speed_row.setSpacing(6)
        self._speed_row_layout = speed_row
        speed_label = QLabel("Speed limit", downloads_card)
        speed_label.setObjectName("settingsSubtext")
        speed_row.addWidget(speed_label)
        speed_row.addStretch(1)
        self.speed_limit_value_label = QLabel("No limit", downloads_card)
        self.speed_limit_value_label.setObjectName("muted")
        self.speed_limit_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        speed_row.addWidget(self.speed_limit_value_label)
        downloads_layout.addLayout(speed_row)
        self.speed_limit_slider = QSlider(Qt.Horizontal, downloads_card)
        self.speed_limit_slider.setObjectName("speedLimitSlider")
        self.speed_limit_slider.setRange(0, _SPEED_LIMIT_SLIDER_MAX)
        self.speed_limit_slider.setSingleStep(1)
        self.speed_limit_slider.setPageStep(2)
        self.speed_limit_slider.setTickInterval(max(1, _SPEED_LIMIT_SLIDER_MAX // 8))
        self.speed_limit_slider.setValue(_SPEED_LIMIT_SLIDER_MAX)
        downloads_layout.addWidget(self.speed_limit_slider)


    def _build_settings_updates_card(self, settings_content: QWidget, settings_layout: QVBoxLayout) -> None:
        updates_card, updates_layout = self._create_settings_card("Updates", settings_content)
        self.settings_updates_card = updates_card
        self._build_updates_controls(updates_card, updates_layout)
        settings_layout.addWidget(updates_card)

    def _build_updates_controls(self, updates_card: QFrame, updates_layout: QVBoxLayout) -> None:
        self.auto_updates_checkbox = QCheckBox("Automatic update", updates_card)
        updates_layout.addWidget(self.auto_updates_checkbox)
        self.check_updates_button = QPushButton("Check for updates now", updates_card)
        self.check_updates_button.setObjectName("settingsActionButton")
        updates_layout.addWidget(self.check_updates_button)
        self.reset_settings_button = QPushButton("Reset all settings", updates_card)
        self.reset_settings_button.setObjectName("settingsActionButton")
        updates_layout.addWidget(self.reset_settings_button)


    def _build_settings_dependency_card(self, settings_content: QWidget, settings_layout: QVBoxLayout) -> None:
        dependency_card, dependency_layout = self._create_settings_card("Dependencies", settings_content)
        self.settings_dependency_card = dependency_card
        self._build_ffmpeg_dependency_controls(dependency_card, dependency_layout)
        self._build_node_dependency_controls(dependency_card, dependency_layout)
        settings_layout.addWidget(dependency_card)

    def _build_ffmpeg_dependency_controls(self, dependency_card: QFrame, dependency_layout: QVBoxLayout) -> None:
        self.ffmpeg_status_label = QLabel("FFmpeg: checking...", dependency_card)
        self.ffmpeg_status_label.setObjectName("settingsSubtext")
        self.ffmpeg_status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.ffmpeg_install_button = QPushButton("Install FFmpeg", dependency_card)
        self.ffmpeg_install_button.setObjectName("settingsActionButton")
        dependency_layout.addWidget(self.ffmpeg_status_label)
        dependency_layout.addWidget(self.ffmpeg_install_button)

    def _build_node_dependency_controls(self, dependency_card: QFrame, dependency_layout: QVBoxLayout) -> None:
        self.node_status_label = QLabel("Node.js: checking...", dependency_card)
        self.node_status_label.setObjectName("settingsSubtext")
        self.node_status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.node_install_button = QPushButton("Install Node.js", dependency_card)
        self.node_install_button.setObjectName("settingsActionButton")
        dependency_layout.addWidget(self.node_status_label)
        dependency_layout.addWidget(self.node_install_button)


    def _build_settings_history_card(self, settings_content: QWidget, settings_layout: QVBoxLayout) -> None:
        history_card, history_layout = self._create_settings_card("History", settings_content)
        self.settings_history_card = history_card
        history_hint = QLabel("Recent downloads for quick open/retry.", history_card)
        history_hint.setObjectName("settingsSubtext")
        history_layout.addWidget(history_hint)
        self.disable_history_checkbox = QCheckBox(
            "Disable history and auto-delete unfinished files",
            history_card,
        )
        self.disable_history_checkbox.setChecked(False)
        history_layout.addWidget(self.disable_history_checkbox)
        stale_cleanup_label = QLabel("Stale .part cleanup age", history_card)
        stale_cleanup_label.setObjectName("settingsSubtext")
        history_layout.addWidget(stale_cleanup_label)
        self.stale_part_cleanup_combo = ChevronComboBox(history_card)
        for hours in _STALE_PART_CLEANUP_HOURS_OPTIONS:
            self.stale_part_cleanup_combo.addItem(_format_stale_cleanup_label(hours), int(hours))
        history_layout.addWidget(self.stale_part_cleanup_combo)
        self.history_combo = ChevronComboBox(history_card)
        self.history_combo.setObjectName("historyCombo")
        self.history_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.history_combo.set_popup_horizontal_scroll_enabled(True)
        history_layout.addWidget(self.history_combo)
        history_layout.addLayout(self._build_history_primary_actions(history_card))
        self.history_retry_button = QPushButton("Retry URL", history_card)
        self.history_retry_button.setObjectName("settingsActionButton")
        self.history_clear_button = QPushButton("Clear history", history_card)
        self.history_clear_button.setObjectName("settingsActionButton")
        history_layout.addWidget(self.history_retry_button)
        history_layout.addWidget(self.history_clear_button)
        settings_layout.addWidget(history_card)

    def _build_history_primary_actions(self, history_card: QFrame) -> QHBoxLayout:
        history_actions = QHBoxLayout()
        history_actions.setSpacing(6)
        self.history_open_file_button = QPushButton("Open file", history_card)
        self.history_open_file_button.setObjectName("settingsActionButton")
        self.history_open_folder_button = QPushButton("Open folder", history_card)
        self.history_open_folder_button.setObjectName("settingsActionButton")
        history_actions.addWidget(self.history_open_file_button, 1)
        history_actions.addWidget(self.history_open_folder_button, 1)
        return history_actions

    def _init_settings_animation(self) -> None:
        self.settings_animation = QPropertyAnimation(self.settings_panel, b"maximumWidth", self)
        self.settings_animation.setDuration(150)
        self.settings_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.settings_animation.valueChanged.connect(self._on_settings_animation_value_changed)
        self.settings_animation.finished.connect(self._on_settings_animation_finished)


    def _build_footer_section(self, root: QWidget, outer: QVBoxLayout) -> None:
        footer = QFrame(root)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(2, 0, 2, 1)
        footer_layout.setSpacing(8)
        self._footer_layout = footer_layout

        self.theme_toggle_button = QPushButton("", footer)
        self.theme_toggle_button.setObjectName("footerIcon")
        self.theme_toggle_button.setFlat(True)
        self.theme_toggle_button.setIconSize(QSize(18, 18))
        self.settings_toggle_button = QPushButton("Show settings", footer)
        self.settings_toggle_button.setObjectName("footerLink")
        self.settings_toggle_button.setFlat(True)
        self.tutorial_button = QPushButton("Tutorial", footer)
        self.tutorial_button.setObjectName("footerLink")
        self.tutorial_button.setFlat(True)
        self.downloads_button = QPushButton("Open downloads folder", footer)
        self.downloads_button.setObjectName("footerLink")
        self.downloads_button.setFlat(True)
        self.official_button = QPushButton("Official website", footer)
        self.official_button.setObjectName("footerLink")
        self.official_button.setFlat(True)
        self.version_label = QLabel(f"v{APP_VERSION}", footer)
        self.version_label.setObjectName("footerVersion")

        footer_layout.addWidget(self.theme_toggle_button, 0, Qt.AlignLeft)
        footer_layout.addWidget(self.settings_toggle_button, 0, Qt.AlignLeft)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.downloads_button, 0, Qt.AlignRight)
        footer_layout.addWidget(self.official_button, 0, Qt.AlignRight)
        footer_layout.addWidget(self.tutorial_button, 0, Qt.AlignRight)
        footer_layout.addWidget(self.version_label, 0, Qt.AlignRight)
        outer.addWidget(footer)

    def _build_tutorial_overlay(self, parent: QWidget) -> None:
        self._tutorial_overlay = TutorialOverlay(self.theme, parent)
        self._tutorial_overlay.nextRequested.connect(self.tutorialNextRequested.emit)
        self._tutorial_overlay.backRequested.connect(self.tutorialBackRequested.emit)
        self._tutorial_overlay.skipRequested.connect(self.tutorialSkipRequested.emit)
        self._tutorial_overlay.finishRequested.connect(self.tutorialFinishRequested.emit)
        self._tutorial_overlay.setGeometry(parent.rect())
        self._tutorial_overlay.hide()

    def _create_settings_card(self, title: str, parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(parent)
        card.setObjectName("settingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(6)
        header = QLabel(title, card)
        header.setObjectName("settingsCardTitle")
        layout.addWidget(header)
        self._settings_card_layouts.append(layout)
        return card, layout

    def _connect_signals(self) -> None:
        self._connect_primary_action_signals()
        self._connect_settings_signals()
        self._connect_batch_signals()
        self._connect_history_signals()

    def _connect_primary_action_signals(self) -> None:
        self.download_button.clicked.connect(self._on_download_button_clicked)
        self.pause_resume_button.clicked.connect(self._on_pause_resume_button_clicked)
        self.stop_button.clicked.connect(self.stopRequested.emit)
        self.downloads_button.clicked.connect(self.openDownloadsRequested.emit)
        self.official_button.clicked.connect(self.officialPageRequested.emit)
        self.settings_toggle_button.clicked.connect(self._on_toggle_settings)
        self.tutorial_button.clicked.connect(self.tutorialRequested.emit)
        self.theme_toggle_button.clicked.connect(self._on_toggle_theme)
        self.paste_button.clicked.connect(self._paste_from_clipboard)
        self.single_url_input.textChanged.connect(self._on_single_url_text_changed)
        self.format_combo.currentTextChanged.connect(self._on_format_combo_changed)
        self.quality_combo.currentTextChanged.connect(self._on_quality_combo_changed)
        self.quality_combo.popupAboutToShow.connect(self.qualityDropdownOpened.emit)
        self.quality_combo.disabledClicked.connect(self._on_quality_unavailable_clicked)

    def _connect_settings_signals(self) -> None:
        self.check_updates_button.clicked.connect(self.checkUpdatesRequested.emit)
        self.reset_settings_button.clicked.connect(self.resetSettingsRequested.emit)
        self.ffmpeg_install_button.clicked.connect(self._request_install_ffmpeg)
        self.node_install_button.clicked.connect(self._request_install_node)
        self.ui_scale_slider.valueChanged.connect(self._on_ui_scale_value_changed)
        self.ui_scale_slider.sliderReleased.connect(self._on_ui_scale_slider_released)
        self.batch_concurrency_slider.valueChanged.connect(self._on_batch_concurrency_changed)
        self.skip_existing_checkbox.toggled.connect(self.skipExistingFilesChanged.emit)
        self.auto_start_ready_links_checkbox.toggled.connect(self.autoStartReadyLinksChanged.emit)
        self.disable_metadata_fetch_checkbox.toggled.connect(self.metadataFetchDisabledChanged.emit)
        self.fallback_metadata_checkbox.toggled.connect(self.fallbackDownloadOnMetadataErrorChanged.emit)
        self.batch_retry_slider.valueChanged.connect(self._on_batch_retry_changed)
        self.retry_profile_combo.currentTextChanged.connect(self._on_retry_profile_changed)
        self.filename_template_combo.currentTextChanged.connect(self._on_filename_template_option_changed)
        self.filename_template_custom_edit.editingFinished.connect(self._on_filename_template_committed)
        self.conflict_policy_combo.currentTextChanged.connect(self._on_conflict_policy_changed)
        self.speed_limit_slider.valueChanged.connect(self._on_speed_limit_changed)
        self.adaptive_concurrency_checkbox.toggled.connect(self.adaptiveConcurrencyChanged.emit)
        self.auto_updates_checkbox.toggled.connect(self.autoCheckUpdatesChanged.emit)
        self.disable_history_checkbox.toggled.connect(self.disableHistoryChanged.emit)
        self.stale_part_cleanup_combo.currentIndexChanged.connect(self._on_stale_cleanup_changed)
        self.download_path_browse_btn.clicked.connect(self._browse_download_location)

    def _connect_batch_signals(self) -> None:
        self.multi_mode_button.toggled.connect(self._on_batch_mode_toggled)
        self.multi_add_input.returnPressed.connect(self._emit_multi_add_from_input)
        self.multi_add_button.clicked.connect(self._on_multi_paste_from_clipboard)
        self.multi_bulk_button.clicked.connect(self._on_multi_bulk_paste)
        self.multi_import_button.clicked.connect(self._on_multi_import_file)
        self.multi_export_button.clicked.connect(self._on_multi_export)
        self.multi_search_input.textChanged.connect(self._refresh_batch_entries_display)
        self.multi_status_filter.currentTextChanged.connect(self._refresh_batch_entries_display)

    def _connect_history_signals(self) -> None:
        self.history_open_file_button.clicked.connect(self._emit_history_open_file)
        self.history_open_folder_button.clicked.connect(self._emit_history_open_folder)
        self.history_retry_button.clicked.connect(self._emit_history_retry)
        self.history_clear_button.clicked.connect(self.historyClearRequested.emit)

    def _on_download_button_clicked(self) -> None:
        if self._is_batch_mode_enabled():
            self.multiStartAllRequested.emit()
            return
        self.startDownloadRequested.emit()

    def _on_pause_resume_button_clicked(self) -> None:
        if self._is_batch_mode_enabled():
            self.multiPauseResumeAllRequested.emit()
            return
        self.singlePauseResumeRequested.emit()

    def _emit_multi_add_from_input(self) -> None:
        value = self.multi_add_input.text().strip()
        if not value:
            return
        self.multi_add_input.clear()
        self.multiAddUrlRequested.emit(value)

    def _on_multi_paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self._show_info_dialog("Clipboard empty", "There is no URL in the clipboard.")
            return
        first = ""
        for line in text.splitlines():
            candidate = line.strip()
            if candidate:
                first = candidate
                break
        if not first:
            self._show_info_dialog("Clipboard empty", "There is no URL in the clipboard.")
            return
        self.multiAddUrlRequested.emit(first)

    def _on_multi_bulk_paste(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Bulk paste URLs")
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(dialog)
        editor.setPlaceholderText("Paste one URL per line")
        editor.setPlainText(QApplication.clipboard().text().strip())
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._apply_dialog_theme(dialog)
        if dialog.exec() != QDialog.Accepted:
            return
        text = editor.toPlainText().strip()
        if text:
            self.multiBulkAddRequested.emit(text)

    def _on_multi_import_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Import URL list",
            str(Path.home()),
            "Text files (*.txt *.csv);;All files (*.*)",
        )
        if not selected:
            return
        file_path = Path(selected)
        content = ""
        for encoding in ("utf-8", "utf-8-sig", "cp1252"):
            try:
                content = file_path.read_text(encoding=encoding)
                break
            except UnicodeError:
                continue
            except OSError as exc:
                self._show_info_dialog("Import failed", f"Could not read file:\n{exc}")
                return
        content = content.strip()
        if not content:
            self._show_info_dialog("Import URLs", "The selected file is empty.")
            return
        self.multiBulkAddRequested.emit(content)

    def _on_multi_export(self) -> None:
        default_name = str((Path.home() / "Downloads" / "MediaCrate_urls_export.txt").resolve())
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Export URLs",
            default_name,
            "Text files (*.txt);;All files (*.*)",
        )
        if not selected:
            return
        self.multiExportRequested.emit(str(selected))

    def _selected_history_entry(self) -> DownloadHistoryEntry | None:
        index = int(self.history_combo.currentIndex())
        if index < 0 or index >= len(self._history_entries):
            return None
        return self._history_entries[index]

    def _emit_history_open_file(self) -> None:
        entry = self._selected_history_entry()
        if entry is None:
            return
        self.historyOpenFileRequested.emit(str(entry.output_path or ""))

    def _emit_history_open_folder(self) -> None:
        entry = self._selected_history_entry()
        if entry is None:
            return
        self.historyOpenFolderRequested.emit(str(entry.output_path or ""))

    def _emit_history_retry(self) -> None:
        entry = self._selected_history_entry()
        if entry is None:
            return
        self.historyRetryRequested.emit(str(entry.url or ""))

    def _request_install_ffmpeg(self) -> None:
        self.installDependencyRequested.emit("ffmpeg")

    def _request_install_node(self) -> None:
        self.installDependencyRequested.emit("node")

    def _on_single_url_text_changed(self) -> None:
        self.urlTextChanged.emit(self.single_url_input.text())

    def _install_control_styles(self) -> None:
        self._slider_styles.clear()
        self._checkbox_styles.clear()

        slider_style = RoundHandleSliderStyle(
            handle_color=self.theme.text_primary,
            border_color=self.theme.border,
            groove_color=self.theme.border,
            fill_color=self.theme.accent,
            handle_size=18,
            groove_height=6,
            parent=self.ui_scale_slider,
        )
        self.ui_scale_slider.setStyle(slider_style)
        self._slider_styles.append(slider_style)
        batch_slider_style = RoundHandleSliderStyle(
            handle_color=self.theme.text_primary,
            border_color=self.theme.border,
            groove_color=self.theme.border,
            fill_color=self.theme.accent,
            handle_size=18,
            groove_height=6,
            parent=self.batch_concurrency_slider,
        )
        self.batch_concurrency_slider.setStyle(batch_slider_style)
        self._slider_styles.append(batch_slider_style)
        retry_slider_style = RoundHandleSliderStyle(
            handle_color=self.theme.text_primary,
            border_color=self.theme.border,
            groove_color=self.theme.border,
            fill_color=self.theme.accent,
            handle_size=18,
            groove_height=6,
            parent=self.batch_retry_slider,
        )
        self.batch_retry_slider.setStyle(retry_slider_style)
        self._slider_styles.append(retry_slider_style)
        speed_slider_style = RoundHandleSliderStyle(
            handle_color=self.theme.text_primary,
            border_color=self.theme.border,
            groove_color=self.theme.border,
            fill_color=self.theme.accent,
            handle_size=18,
            groove_height=6,
            parent=self.speed_limit_slider,
        )
        self.speed_limit_slider.setStyle(speed_slider_style)
        self._slider_styles.append(speed_slider_style)

        for checkbox in self.findChildren(QCheckBox):
            style = SquareCheckBoxStyle(
                border_color=self.theme.border,
                fill_color=self.theme.accent,
                check_color=self.theme.text_primary,
                size=20,
                radius=4,
                parent=checkbox,
            )
            checkbox.setStyle(style)
            self._checkbox_styles.append(style)

    def _apply_combo_arrow_palette(self) -> None:
        hover = QColor(self.theme.border)
        if self.theme.mode == "dark":
            hover = hover.lighter(128)
        else:
            hover = hover.darker(108)
        for combo in self.findChildren(ChevronComboBox):
            combo.set_arrow_colors(self.theme.text_secondary, self.theme.text_primary)
            combo.set_popup_colors(
                accent=self.theme.accent,
                text=self.theme.text_primary,
                panel=self.theme.panel_bg,
                hover=hover.name(),
            )

    @staticmethod
    def _set_widget_cursor(widget: QWidget) -> None:
        set_widget_pointer_cursor(widget)

    @staticmethod
    def _is_interactive_control(watched: object) -> bool:
        return isinstance(watched, (QPushButton, QCheckBox, QSlider, ChevronComboBox))

    def _set_interaction_cursors(self) -> None:
        for widget in self.findChildren(QWidget):
            if self._is_interactive_control(widget):
                self._set_widget_cursor(widget)

    def _install_wheel_guards(self) -> None:
        self.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)

    @staticmethod
    def _is_descendant_of(child: object, ancestor: object) -> bool:
        current = child
        while current is not None:
            if current is ancestor:
                return True
            if not hasattr(current, "parent"):
                return False
            try:
                current = current.parent()
            except RuntimeError:
                return False
        return False

    def _is_settings_descendant(self, watched: object) -> bool:
        return self._is_descendant_of(watched, self.settings_scroll)

    def _has_open_settings_combo_popup(self) -> bool:
        if not isinstance(getattr(self, "settings_panel", None), QWidget):
            return False
        for combo in self.settings_panel.findChildren(ChevronComboBox):
            try:
                if combo.is_popup_visible():
                    return True
            except RuntimeError:
                continue
        return False

    def _open_settings_combo_popup_view(self, watched: object):
        if not isinstance(getattr(self, "settings_panel", None), QWidget):
            return None
        for combo in self.settings_panel.findChildren(ChevronComboBox):
            try:
                if not combo.is_popup_visible():
                    continue
                popup_view = combo.view()
                if popup_view is None:
                    continue
                if watched is popup_view or self._is_descendant_of(watched, popup_view):
                    return popup_view
            except RuntimeError:
                continue
        return None

    def _is_multi_entries_descendant(self, watched: object) -> bool:
        return self._is_descendant_of(watched, self.multi_entries_scroll)

    def _is_tutorial_descendant(self, watched: object) -> bool:
        overlay = getattr(self, "_tutorial_overlay", None)
        if overlay is None:
            return False
        return self._is_descendant_of(watched, overlay)

    @staticmethod
    def _scroll_area_by_wheel(scroll_area: QScrollArea, delta_y: int) -> None:
        if delta_y == 0:
            return
        bar: QScrollBar = scroll_area.verticalScrollBar()
        if bar.maximum() <= bar.minimum():
            return
        notches = int(delta_y / 120)
        if notches == 0:
            notches = 1 if delta_y > 0 else -1
        amount = notches * bar.singleStep() * 3
        bar.setValue(bar.value() - amount)

    @staticmethod
    def _scroll_area_horizontally_by_wheel(scroll_area: QScrollArea, delta_y: int) -> None:
        if delta_y == 0:
            return
        bar: QScrollBar = scroll_area.horizontalScrollBar()
        if bar.maximum() <= bar.minimum():
            return
        notches = int(delta_y / 120)
        if notches == 0:
            notches = 1 if delta_y > 0 else -1
        amount = notches * bar.singleStep() * 3
        bar.setValue(bar.value() - amount)

    @staticmethod
    def _is_cursor_refresh_event(event_type: QEvent.Type) -> bool:
        return event_type in {
            QEvent.EnabledChange,
            QEvent.Show,
            QEvent.Hide,
            QEvent.Enter,
            QEvent.HoverEnter,
            QEvent.HoverMove,
            QEvent.StyleChange,
            QEvent.Polish,
        }

    def _handle_cursor_refresh_event(self, watched: object, event_type: QEvent.Type) -> None:
        if self._is_cursor_refresh_event(event_type) and self._is_interactive_control(watched):
            self._set_widget_cursor(watched)

    def _handle_wheel_event(self, watched: object, event) -> bool:                
        if event.type() != QEvent.Wheel:
            return False
        if self._is_multi_entries_descendant(watched) and bool(event.modifiers() & Qt.ShiftModifier):
            self._scroll_area_horizontally_by_wheel(self.multi_entries_scroll, event.angleDelta().y())
            event.accept()
            return True
        if self._is_settings_descendant(watched):
            popup_view = self._open_settings_combo_popup_view(watched)
            if popup_view is not None:
                if bool(event.modifiers() & Qt.ShiftModifier):
                    bar: QScrollBar = popup_view.horizontalScrollBar()
                    if bar.maximum() > bar.minimum():
                        delta_y = int(event.angleDelta().y())
                        notches = int(delta_y / 120)
                        if notches == 0:
                            notches = 1 if delta_y > 0 else -1
                        amount = notches * max(1, int(round(bar.singleStep() * 0.75)))
                        bar.setValue(bar.value() - amount)
                        event.accept()
                        return True
                return False
            if self._has_open_settings_combo_popup():
                event.accept()
                return True
            self._scroll_area_by_wheel(self.settings_scroll, event.angleDelta().y())
            event.accept()
            return True
        if self._is_interactive_control(watched):
            event.accept()
            return True
        return False

    def eventFilter(self, watched, event):                
        try:
            self._handle_cursor_refresh_event(watched, event.type())
            if self._tutorial_mode and not self._is_tutorial_descendant(watched):
                if event.type() in {
                    QEvent.MouseButtonPress,
                    QEvent.MouseButtonRelease,
                    QEvent.MouseButtonDblClick,
                    QEvent.MouseMove,
                    QEvent.Wheel,
                    QEvent.KeyPress,
                    QEvent.KeyRelease,
                    QEvent.Shortcut,
                    QEvent.ShortcutOverride,
                    QEvent.ContextMenu,
                }:
                    event.accept()
                    return True
            if self._handle_wheel_event(watched, event):
                return True
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False

    def _build_theme_icon(self, mode: str) -> QIcon:
        size = max(14, int(self.theme_toggle_button.iconSize().width()))
        screen = QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio()) if screen is not None else 1.0
        px = int(round(size * dpr))
        icon = QPixmap(px, px)
        icon.setDevicePixelRatio(dpr)
        icon.fill(Qt.transparent)
        color = QColor(self.theme.text_primary)
        painter = QPainter(icon)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(color, max(1.1, size * 0.10), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        center = QPointF(size * 0.5, size * 0.5)
        if mode == "sun":
            orbit_radius = size * 0.22
            inner_ray = size * 0.34
            outer_ray = size * 0.46
            painter.drawEllipse(
                QPointF(center.x(), center.y()),
                orbit_radius,
                orbit_radius,
            )
            for direction in (
                QPointF(1.0, 0.0),
                QPointF(-1.0, 0.0),
                QPointF(0.0, 1.0),
                QPointF(0.0, -1.0),
                QPointF(0.707, 0.707),
                QPointF(-0.707, -0.707),
                QPointF(0.707, -0.707),
                QPointF(-0.707, 0.707),
            ):
                start = QPointF(center.x() + (direction.x() * inner_ray), center.y() + (direction.y() * inner_ray))
                end = QPointF(center.x() + (direction.x() * outer_ray), center.y() + (direction.y() * outer_ray))
                painter.drawLine(start, end)
        else:
            moon_radius = size * 0.38
            painter.setBrush(color)
            painter.drawEllipse(center, moon_radius, moon_radius)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.setPen(Qt.NoPen)
            painter.setBrush(Qt.transparent)
            painter.drawEllipse(
                QPointF(center.x() + (size * 0.17), center.y() - (size * 0.10)),
                size * 0.34,
                size * 0.34,
            )
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.end()
        return QIcon(icon)

    def _refresh_theme_toggle_icon(self) -> None:
        if self._theme_mode == "dark":
            self.theme_toggle_button.setIcon(self._build_theme_icon("moon"))
            self.theme_toggle_button.setToolTip("Switch to light mode")
        else:
            self.theme_toggle_button.setIcon(self._build_theme_icon("sun"))
            self.theme_toggle_button.setToolTip("Switch to dark mode")

    def apply_windows_titlebar_theme(self, widget: QWidget | None = None) -> None:
        if os.name != "nt":
            return
        target = widget or self
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = int(target.winId())
            if hwnd == 0:
                return
            value = ctypes.c_int(0 if self._theme_mode == "light" else 1)
            size = ctypes.sizeof(value)
            dwm = ctypes.windll.dwmapi
            for attribute in (20, 19):
                result = dwm.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd),
                    ctypes.c_uint(attribute),
                    ctypes.byref(value),
                    ctypes.c_uint(size),
                )
                if result == 0:
                    break
        except Exception:
            return

    def showEvent(self, event) -> None:                
        super().showEvent(event)
        if not self._post_show_layout_synced:
            self._post_show_layout_synced = True
            QTimer.singleShot(0, self._run_post_show_layout_sync)
        QTimer.singleShot(0, self.apply_windows_titlebar_theme)

    def _run_post_show_layout_sync(self) -> None:
        self._apply_window_layout()
        self._refresh_settings_scroll_metrics()
        self._apply_single_input_lock_state()
        self._sync_tutorial_overlay()

    def _on_settings_animation_finished(self) -> None:
        if self._settings_animation_expected_end_width is None:
            return
        end_width = int(self._settings_animation_expected_end_width)
        self._settings_animation_expected_end_width = None
        if self._settings_visible:
            self._set_settings_container_width(end_width)
        else:
            self._set_settings_container_width(0)
        self._apply_format_quality_width_policy()
        self._content_row_layout.invalidate()
        self._content_row_layout.activate()
        self._refresh_settings_scroll_metrics()
        self._refresh_single_meta_display()
        self._schedule_single_meta_refresh()
        self._sync_tutorial_overlay()

    def _on_settings_animation_value_changed(self, value: object) -> None:
        try:
            width = max(0, int(round(float(value))))
        except Exception:
            width = max(0, int(self.settings_panel.maximumWidth()))
        self.settings_panel.setMinimumWidth(width)
        self._apply_format_quality_width_policy()
        self._refresh_single_meta_display()
        self._sync_tutorial_overlay()

    def _on_toggle_settings(self) -> None:
        self.set_settings_visible(not self._settings_visible)

    def _on_toggle_theme(self) -> None:
        self.themeModeChanged.emit("light" if self._theme_mode == "dark" else "dark")

    def _apply_ui_scale(self, value: int, *, emit: bool) -> None:
        self._ui_scale_percent = self._normalize_ui_scale_percent(value)
        self._pending_ui_scale_percent = None
        self.ui_scale_value_label.setText(f"{self._ui_scale_percent}%")
        self._apply_window_layout()
        self._apply_combo_arrow_palette()
        self._refresh_theme_toggle_icon()
        self._sync_tutorial_overlay()
        if emit:
            self.uiScaleChanged.emit(self._ui_scale_percent)

    def _on_ui_scale_value_changed(self, value: int) -> None:
        normalized = self._normalize_ui_scale_percent(value)
        if value != normalized:
            self.ui_scale_slider.blockSignals(True)
            self.ui_scale_slider.setValue(normalized)
            self.ui_scale_slider.blockSignals(False)
        self.ui_scale_value_label.setText(f"{normalized}%")
        self._pending_ui_scale_percent = normalized

    def _on_ui_scale_slider_released(self) -> None:
        target = self._pending_ui_scale_percent
        self._pending_ui_scale_percent = None
        if target is None:
            return
        if target != self._ui_scale_percent:
            self._apply_ui_scale(target, emit=True)
            return
        self.uiScaleChanged.emit(target)

    def _on_batch_concurrency_changed(self, value: int) -> None:
        level = max(1, min(16, int(value)))
        self.batch_concurrency_value_label.setText(str(level))
        self.batchConcurrencyChanged.emit(level)

    def _on_batch_retry_changed(self, value: int) -> None:
        retries = max(0, min(3, int(value)))
        self.batch_retry_value_label.setText(str(retries))
        self.batchRetryCountChanged.emit(retries)

    def _current_retry_profile(self) -> str:
        value = str(self.retry_profile_combo.currentData(Qt.UserRole) or "").strip().lower()
        valid_values = {item.value for item in RetryProfile}
        if value in valid_values:
            return value
        return RetryProfile.BASIC.value

    def _on_retry_profile_changed(self, _text: str) -> None:
        self.retryProfileChanged.emit(self._current_retry_profile())

    def _on_stale_cleanup_changed(self, _index: int) -> None:
        raw = self.stale_part_cleanup_combo.currentData(Qt.UserRole)
        try:
            hours = max(0, int(raw))
        except (TypeError, ValueError):
            hours = 0
        self.stalePartCleanupHoursChanged.emit(hours)

    @staticmethod
    def _template_preview_sample() -> dict[str, str]:
        return {
            "title": "Example Title",
            "id": "abc123",
            "uploader": "ChannelName",
            "ext": "mp4",
            "playlist_index": "01",
            "upload_date": "2026-02-11",
        }

    def _render_template_preview(self, template: str) -> str:
        value = str(template or "").strip() or DEFAULT_FILENAME_TEMPLATE
        samples = self._template_preview_sample()
        pattern = re.compile(r"%\(([^)]+)\)[^%a-zA-Z]*[a-zA-Z]")

        def _replace(match: re.Match[str]) -> str:
            token = str(match.group(1) or "").strip().lower()
            base = token.split("|", 1)[0].split(",", 1)[0].strip()
            return samples.get(base, f"<{base or 'value'}>")

        preview = pattern.sub(_replace, value)
        return preview if preview else value

    def _effective_filename_template(self) -> str:
        selected = self.filename_template_combo.currentText().strip()
        if selected == _FILENAME_TEMPLATE_CUSTOM_LABEL:
            custom = str(self.filename_template_custom_edit.text() or "").strip()
            return custom or DEFAULT_FILENAME_TEMPLATE
        for label, template in _FILENAME_TEMPLATE_PRESETS:
            if label == selected:
                return template
        return DEFAULT_FILENAME_TEMPLATE

    def _update_filename_template_preview(self, template: str | None = None) -> None:
        resolved = str(template or "").strip() or self._effective_filename_template()
        self.filename_template_preview_label.setText(f"Preview: {self._render_template_preview(resolved)}")

    def _set_filename_template_ui(self, template: str, *, emit: bool) -> None:
        resolved = str(template or "").strip() or DEFAULT_FILENAME_TEMPLATE
        selected_label = _FILENAME_TEMPLATE_CUSTOM_LABEL
        for label, preset_template in _FILENAME_TEMPLATE_PRESETS:
            if preset_template == resolved:
                selected_label = label
                break
        self._filename_template_updating = True
        self.filename_template_combo.setCurrentText(selected_label)
        self.filename_template_custom_edit.setVisible(selected_label == _FILENAME_TEMPLATE_CUSTOM_LABEL)
        self.filename_template_custom_edit.setText(resolved)
        self._filename_template_updating = False
        self._update_filename_template_preview(resolved)
        if emit:
            self.filenameTemplateChanged.emit(resolved)

    def _on_filename_template_option_changed(self, _value: str) -> None:
        if self._filename_template_updating:
            return
        selected = self.filename_template_combo.currentText().strip()
        self.filename_template_custom_edit.setVisible(selected == _FILENAME_TEMPLATE_CUSTOM_LABEL)
        resolved = self._effective_filename_template()
        self._update_filename_template_preview(resolved)
        self.filenameTemplateChanged.emit(resolved)

    def _on_speed_limit_changed(self, value: int) -> None:
        clamped = max(0, min(_SPEED_LIMIT_SLIDER_MAX, int(value)))
        if clamped != int(value):
            self.speed_limit_slider.blockSignals(True)
            self.speed_limit_slider.setValue(clamped)
            self.speed_limit_slider.blockSignals(False)
        kbps = _speed_limit_kbps_from_slider_value(clamped)
        self.speed_limit_value_label.setText(_format_speed_limit_label(kbps))
        self.speedLimitChanged.emit(kbps)

    def _on_filename_template_committed(self) -> None:
        if self._filename_template_updating:
            return
        if self.filename_template_combo.currentText().strip() != _FILENAME_TEMPLATE_CUSTOM_LABEL:
            self._update_filename_template_preview(self._effective_filename_template())
            return
        value = str(self.filename_template_custom_edit.text() or "").strip()
        if not value:
            value = DEFAULT_FILENAME_TEMPLATE
            self.filename_template_custom_edit.setText(value)
        self._update_filename_template_preview(value)
        self.filenameTemplateChanged.emit(value)

    def _on_conflict_policy_changed(self, value: str) -> None:
        text = str(value or "").strip().lower()
        if text.startswith("overwrite"):
            self.conflictPolicyChanged.emit("overwrite")
            return
        if text.startswith("rename"):
            self.conflictPolicyChanged.emit("rename")
            return
        self.conflictPolicyChanged.emit("skip")

    def _is_batch_mode_enabled(self) -> bool:
        return bool(self.multi_mode_button.isChecked())

    def is_batch_mode_enabled(self) -> bool:
        return self._is_batch_mode_enabled()

    def _current_url_text(self) -> str:
        if self._is_batch_mode_enabled():
            return ""
        return self.single_url_input.text().strip()

    def _set_url_text(self, text: str) -> None:
        if self._is_batch_mode_enabled():
            return
        first = ""
        for line in str(text or "").splitlines():
            if line.strip():
                first = line.strip()
                break
        self.single_url_input.setText(first)

    def set_single_url_text(self, text: str) -> None:
        self._set_url_text(text)

    def _start_batch_mode_transition(self, *, enabled: bool) -> None:
        self._apply_scaled_metrics(self._render_scale)
        target_inline_height = self._batch_inline_target_height if enabled else 0
        target_expansion = 1.0 if enabled else 0.0
        target_width, target_window_height = self._compute_dimensions(
            self._render_scale,
            batch_expansion=target_expansion,
        )
        self._set_batch_inline_section_height(target_inline_height, force_visible=enabled)
        self.setFixedSize(max(1, target_width), max(1, target_window_height))
        self.resize(max(1, target_width), max(1, target_window_height))
        self._settings_target_width = self._compute_settings_target_width(self._render_scale, self.width())
        self.settings_animation.stop()
        self._settings_animation_expected_end_width = None
        if self._settings_visible:
            self._set_settings_container_width(self._settings_target_width)
        else:
            self._set_settings_container_width(0)
        self._main_layout.invalidate()
        self._main_layout.activate()
        self._content_row_layout.invalidate()
        self._content_row_layout.activate()
        self._apply_format_quality_width_policy()
        self._refresh_settings_scroll_metrics()
        self._sync_tutorial_overlay()

    def _apply_batch_mode_state(self, enabled: bool, *, emit: bool) -> None:
        normalized = bool(enabled)
        self._rebuild_input_layout_for_mode(batch_mode_enabled=normalized)
        if normalized:
            self._single_url_row.hide()
            self.single_url_input.hide()
            self.single_features_row.show()
            self._single_progress_gap.hide()
            self.paste_button.hide()
            self.multi_add_input.show()
            self.multi_add_button.setVisible(not self._settings_visible)
            self.multi_bulk_button.show()
            self.multi_import_button.show()
            self.multi_export_button.show()
            self.download_button.setText("DOWNLOAD ALL")
            self.pause_resume_button.show()
            self.pause_resume_button.setText("PAUSE ALL")
            self.download_progress.hide()
            self._action_row_layout.setStretch(0, 10)
            self._action_row_layout.setStretch(1, 11)
            self._action_row_layout.setStretch(2, 5)
        else:
            self._single_url_row.show()
            self.paste_button.show()
            self.single_url_input.show()
            self.single_features_row.show()
            self._single_progress_gap.show()
            self.multi_import_button.hide()
            self.multi_export_button.hide()
            self.download_button.setText("DOWNLOAD")
            self.pause_resume_button.show()
            self.pause_resume_button.setText("PAUSE")
            self.download_progress.show()
            self.reset_download_progress()
            if self._settings_visible:
                self._action_row_layout.setStretch(0, 12)
                self._action_row_layout.setStretch(1, 14)
                self._action_row_layout.setStretch(2, 5)
            else:
                self._action_row_layout.setStretch(0, 12)
                self._action_row_layout.setStretch(1, 13)
                self._action_row_layout.setStretch(2, 6)
        self._sync_single_meta_visibility()
        self._start_batch_mode_transition(enabled=normalized)
        self._apply_single_input_lock_state()
        if emit:
            self.batchModeChanged.emit(normalized)
            self.urlTextChanged.emit(self._current_url_text())

    def _on_batch_mode_toggled(self, enabled: bool) -> None:
        self.set_batch_mode_enabled(bool(enabled), emit=True)

    def set_batch_mode_enabled(self, enabled: bool, *, emit: bool = False) -> None:
        normalized = bool(enabled)
        self._run_with_blocked_signals(
            self.single_mode_button,
            lambda: self.single_mode_button.setChecked(not normalized),
        )
        self._run_with_blocked_signals(
            self.multi_mode_button,
            lambda: self.multi_mode_button.setChecked(normalized),
        )
        self._apply_batch_mode_state(normalized, emit=emit)

    def _clear_multi_entries_layout(self) -> None:
        while self._multi_entries_layout.count():
            item = self._multi_entries_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget is self.multi_empty_label:
                    widget.hide()
                    continue
                widget.setParent(None)

    def set_batch_entries(self, entries: list[BatchEntry]) -> None:
        self._all_batch_entries = [item for item in entries if isinstance(item, BatchEntry)]
        layout_rebuilt = self._refresh_batch_entries_display()
        if layout_rebuilt:
            self._set_interaction_cursors()

    def set_batch_entry_thumbnail(self, entry_id: str, image_data: bytes | None, source_url: str = "") -> None:
        key = str(entry_id or "").strip()
        if not key:
            return
        previous_url = self._batch_entry_thumbnail_urls.get(key, "")
        normalized_url = str(source_url or "").strip()
        if normalized_url:
            self._batch_entry_thumbnail_urls[key] = normalized_url
            if image_data:
                self._batch_thumbnail_payload_by_url[normalized_url] = bytes(image_data)
        else:
            self._batch_entry_thumbnail_urls.pop(key, None)
        if previous_url and previous_url != normalized_url and previous_url not in self._batch_entry_thumbnail_urls.values():
            self._batch_thumbnail_payload_by_url.pop(previous_url, None)
        row = self._batch_entry_widgets.get(key)
        if row is None:
            return
        payload = self._batch_thumbnail_payload_by_url.get(normalized_url, b"") if normalized_url else b""
        row.set_thumbnail_bytes(payload if payload else None, normalized_url)

    def _entry_matches_filter(self, entry: BatchEntry) -> bool:
        query = self.multi_search_input.text().strip().lower()
        if query:
            source = f"{entry.url_raw} {entry.title}".lower()
            if query not in source:
                return False

        mode = self.multi_status_filter.currentText().strip().upper() or "ALL"
        status = str(entry.status or "").strip().lower()
        if mode == "ALL":
            return True
        if mode == "READY":
            return status == BatchEntryStatus.VALID.value
        if mode == "ACTIVE":
            return status in {
                BatchEntryStatus.DOWNLOAD_QUEUED.value,
                BatchEntryStatus.DOWNLOADING.value,
            }
        if mode == "PAUSED":
            return status == BatchEntryStatus.PAUSED.value
        if mode == "DONE":
            return status in {BatchEntryStatus.DONE.value, BatchEntryStatus.SKIPPED.value}
        if mode == "FAILED":
            return status in {
                BatchEntryStatus.INVALID.value,
                BatchEntryStatus.FAILED.value,
                BatchEntryStatus.CANCELLED.value,
            }
        return True

    def _is_default_batch_filter(self) -> bool:
        if self.multi_search_input.text().strip():
            return False
        return (self.multi_status_filter.currentText().strip().upper() or "ALL") == "ALL"

    @staticmethod
    def _group_batch_entries_for_display(entries: list[BatchEntry]) -> list[BatchEntry]:
        if not entries:
            return []
        grouped_children: dict[str, list[BatchEntry]] = {}
        ordered_parents: list[BatchEntry] = []
        primary_by_normalized: dict[str, str] = {}
        for entry in entries:
            normalized = str(entry.url_normalized or "").strip()
            if normalized:
                parent_id = primary_by_normalized.get(normalized)
                if parent_id is None:
                    primary_by_normalized[normalized] = str(entry.entry_id)
                    ordered_parents.append(entry)
                elif entry.is_duplicate:
                    grouped_children.setdefault(parent_id, []).append(entry)
                else:
                    ordered_parents.append(entry)
            else:
                ordered_parents.append(entry)

        ordered: list[BatchEntry] = []
        for parent in ordered_parents:
            ordered.append(parent)
            ordered.extend(grouped_children.get(str(parent.entry_id), []))
        return ordered

    def _capture_batch_scroll_state(self) -> tuple[int, int, int, int]:
        vbar = self.multi_entries_scroll.verticalScrollBar()
        hbar = self.multi_entries_scroll.horizontalScrollBar()
        return (
            int(vbar.value()),
            max(0, int(vbar.maximum())),
            int(hbar.value()),
            max(0, int(hbar.maximum())),
        )

    def _restore_batch_scroll_state(
        self,
        *,
        prev_v: int,
        prev_v_max: int,
        prev_h: int,
        prev_h_max: int,
    ) -> None:
        vbar = self.multi_entries_scroll.verticalScrollBar()
        hbar = self.multi_entries_scroll.horizontalScrollBar()

        def _restore_scroll_positions() -> None:
            new_v_max = max(0, int(vbar.maximum()))
            new_h_max = max(0, int(hbar.maximum()))
            was_at_bottom = prev_v_max > 0 and prev_v >= max(0, prev_v_max - 2)
            was_at_right = prev_h_max > 0 and prev_h >= max(0, prev_h_max - 2)
            target_v = new_v_max if was_at_bottom else min(prev_v, new_v_max)
            target_h = new_h_max if was_at_right else min(prev_h, new_h_max)
            vbar.setValue(max(0, min(new_v_max, target_v)))
            hbar.setValue(max(0, min(new_h_max, target_h)))

        QTimer.singleShot(0, _restore_scroll_positions)

    def _remove_stale_batch_entry_widgets(self, entry_ids: set[str]) -> None:
        referenced_urls_before = set(self._batch_entry_thumbnail_urls.values())
        for stale_id in list(self._batch_entry_widgets.keys()):
            if stale_id in entry_ids:
                continue
            stale_widget = self._batch_entry_widgets.pop(stale_id)
            stale_widget.deleteLater()
            self._batch_entry_thumbnail_urls.pop(stale_id, None)
            self._batch_row_render_signatures.pop(stale_id, None)
        if referenced_urls_before:
            referenced_urls_after = set(self._batch_entry_thumbnail_urls.values())
            for source_url in referenced_urls_before:
                if source_url and source_url not in referenced_urls_after:
                    self._batch_thumbnail_payload_by_url.pop(source_url, None)
        if self._displayed_batch_entry_ids:
            self._displayed_batch_entry_ids = [entry_id for entry_id in self._displayed_batch_entry_ids if entry_id in entry_ids]

    def _configure_batch_row_combo_palettes(self, row: BatchEntryRowWidget) -> None:
        row.format_combo.set_arrow_colors(self.theme.text_secondary, self.theme.text_primary)
        row.quality_combo.set_arrow_colors(self.theme.text_secondary, self.theme.text_primary)
        hover = QColor(self.theme.border)
        if self.theme.mode == "dark":
            hover = hover.lighter(128)
        else:
            hover = hover.darker(108)
        row.format_combo.set_popup_colors(
            accent=self.theme.accent,
            text=self.theme.text_primary,
            panel=self.theme.panel_bg,
            hover=hover.name(),
        )
        row.quality_combo.set_popup_colors(
            accent=self.theme.accent,
            text=self.theme.text_primary,
            panel=self.theme.panel_bg,
            hover=hover.name(),
        )

    def _ensure_batch_entry_row_widget(self, entry: BatchEntry) -> BatchEntryRowWidget:
        row = self._batch_entry_widgets.get(entry.entry_id)
        if row is not None:
            return row
        row = BatchEntryRowWidget(entry.entry_id, self.multi_entries_container)
        row.set_ui_scale(self._render_scale)
        row.downloadRequested.connect(self.multiStartEntryRequested.emit)
        row.pauseRequested.connect(self.multiPauseEntryRequested.emit)
        row.resumeRequested.connect(self.multiResumeEntryRequested.emit)
        row.removeRequested.connect(self.multiRemoveEntryRequested.emit)
        row.formatChanged.connect(self.multiEntryFormatChanged.emit)
        row.qualityChanged.connect(self.multiEntryQualityChanged.emit)
        row.qualityUnavailableRequested.connect(self._on_batch_entry_quality_unavailable)
        self._configure_batch_row_combo_palettes(row)
        self._batch_entry_widgets[entry.entry_id] = row
        return row

    def _apply_batch_entry_to_row(self, row: BatchEntryRowWidget, entry: BatchEntry) -> None:
        row.set_entry(entry)
        source_url = self._batch_entry_thumbnail_urls.get(entry.entry_id, "")
        if source_url:
            if row.thumbnail_source_url() != source_url:
                payload = self._batch_thumbnail_payload_by_url.get(source_url, b"")
                row.set_thumbnail_bytes(payload if payload else None, source_url)
        elif row.thumbnail_source_url():
            row.set_thumbnail_bytes(None, "")
        row.set_format_quality_visible(True)
        row.set_detail_visible(True)
        row.set_settings_compact_mode(self._settings_visible)
        row.set_duplicate_visual(bool(entry.is_duplicate))
        row.set_busy(self._controls_locked)

    @staticmethod
    def _batch_entry_render_signature(
        entry: BatchEntry,
        *,
        controls_locked: bool,
        settings_visible: bool,
    ) -> tuple[object, ...]:
        return batch_entry_render_signature(
            entry,
            controls_locked=controls_locked,
            settings_visible=settings_visible,
        )

    def _rebuild_batch_entries_layout(self, ordered_entries: list[BatchEntry]) -> None:
        prev_v, prev_v_max, prev_h, prev_h_max = self._capture_batch_scroll_state()
        self._clear_multi_entries_layout()
        if not ordered_entries:
            self._multi_entries_layout.addWidget(self.multi_empty_label)
            self.multi_empty_label.show()
        else:
            self.multi_empty_label.hide()
            for entry in ordered_entries:
                row = self._batch_entry_widgets.get(entry.entry_id)
                if row is not None:
                    self._multi_entries_layout.addWidget(row)
        self._multi_entries_layout.addStretch(1)
        self.multi_entries_scroll.setMinimumHeight(max(1, int(self._multi_entries_scroll_default_height)))
        self.multi_entries_scroll.setMaximumHeight(16777215)
        self.multi_entries_container.updateGeometry()
        self.multi_entries_container.adjustSize()
        self._restore_batch_scroll_state(
            prev_v=prev_v,
            prev_v_max=prev_v_max,
            prev_h=prev_h,
            prev_h_max=prev_h_max,
        )

    def _refresh_batch_entries_display(self) -> bool:
        filtered_entries = [entry for entry in self._all_batch_entries if self._entry_matches_filter(entry)]
        ordered_entries = self._group_batch_entries_for_display(filtered_entries)
        entry_ids = {entry.entry_id for entry in self._all_batch_entries}
        self._remove_stale_batch_entry_widgets(entry_ids)

        ordered_ids = [str(entry.entry_id or "").strip() for entry in ordered_entries]
        for entry in ordered_entries:
            row = self._ensure_batch_entry_row_widget(entry)
            signature = self._batch_entry_render_signature(
                entry,
                controls_locked=self._controls_locked,
                settings_visible=self._settings_visible,
            )
            if self._batch_row_render_signatures.get(entry.entry_id) == signature:
                continue
            self._apply_batch_entry_to_row(row, entry)
            self._batch_row_render_signatures[entry.entry_id] = signature

        if ordered_ids != self._displayed_batch_entry_ids:
            self._rebuild_batch_entries_layout(ordered_entries)
            self._displayed_batch_entry_ids = ordered_ids
            return True
        elif not ordered_entries:
            self.multi_empty_label.show()
        else:
            self.multi_empty_label.hide()
        return False

    def _update_batch_entry_control_visibility(self) -> None:
        show_format_quality = True
        show_multi_paste = self._is_batch_mode_enabled() and (not self._settings_visible)
        self.multi_add_button.setVisible(show_multi_paste)
        entries_by_id = {str(entry.entry_id): entry for entry in self._all_batch_entries}
        for row in self._batch_entry_widgets.values():
            row.set_format_quality_visible(show_format_quality)
            row.set_detail_visible(True)
            row.set_settings_compact_mode(self._settings_visible)
            source_entry = entries_by_id.get(str(row.entry_id()))
            row.set_duplicate_visual(bool(source_entry.is_duplicate) if source_entry is not None else False)

    def update_batch_entry(self, entry: BatchEntry) -> None:
        if not isinstance(entry, BatchEntry):
            return
        key = str(entry.entry_id or "").strip()
        replaced = False
        for idx, current in enumerate(self._all_batch_entries):
            if str(current.entry_id or "").strip() == key:
                self._all_batch_entries[idx] = entry
                replaced = True
                break
        if not replaced:
            self._all_batch_entries.append(entry)
        row = self._batch_entry_widgets.get(key)
        if self._is_default_batch_filter() and row is not None:
            self._apply_batch_entry_to_row(row, entry)
            return
        self._refresh_batch_entries_display()

    def _show_quality_unavailable_dialog(self) -> None:
        self._show_info_dialog(
            "Quality not applicable",
            "Quality changes affect video resolution. With audio-only formats, MediaCrate uses "
            "the best available audio stream automatically.",
        )

    def _on_quality_unavailable_clicked(self) -> None:
        self._show_quality_unavailable_dialog()

    def _on_batch_entry_quality_unavailable(self, _entry_id: str) -> None:
        self._show_quality_unavailable_dialog()

    def set_batch_stats(
        self,
        *,
        queued: int,
        downloading: int,
        in_progress: int,
        downloaded: int,
        valid: int,
        invalid: int,
        pending: int,
        duplicates: int,
    ) -> None:
        self.batch_preflight_label.setText(
            format_batch_stats_line(
                downloaded=downloaded,
                downloading=downloading,
                in_progress=in_progress,
                queued=queued,
                valid=valid,
                invalid=invalid,
                duplicates=duplicates,
                pending=pending,
            )
        )

    def set_download_history_entries(self, entries: list[DownloadHistoryEntry]) -> None:
        self._history_entries = [item for item in entries if isinstance(item, DownloadHistoryEntry)]
        current_data = self.history_combo.currentData(Qt.UserRole)
        self.history_combo.blockSignals(True)
        self.history_combo.clear()
        for item in self._history_entries:
            title = str(item.title or "").strip() or str(item.url or "").strip()
            state = str(item.state or "").strip().upper()
            stamp = self._format_history_stamp(str(item.timestamp_utc or "").strip())
            label = f"{stamp}  |  {state}  |  {title}"
            self.history_combo.addItem(label, str(item.url or ""))
        restore_index = 0
        if current_data:
            for i in range(self.history_combo.count()):
                if self.history_combo.itemData(i, Qt.UserRole) == current_data:
                    restore_index = i
                    break
        if self.history_combo.count() > 0:
            self.history_combo.setCurrentIndex(restore_index)
        self.history_combo.blockSignals(False)
        has_entries = self.history_combo.count() > 0
        self.history_open_file_button.setEnabled(has_entries)
        self.history_open_folder_button.setEnabled(has_entries)
        self.history_retry_button.setEnabled(has_entries and (not self._controls_locked))
        self.history_clear_button.setEnabled(has_entries and (not self._controls_locked))

    @staticmethod
    def _format_history_stamp(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
            parsed = datetime.fromisoformat(normalized)
            return parsed.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            match = re.search(r"(\d{4}-\d{2}-\d{2}).*?(\d{2}):(\d{2})", raw)
            if match:
                return f"{match.group(1)} {match.group(2)}:{match.group(3)}"
            match = re.search(r"(\d{2}):(\d{2})", raw)
            if match:
                return f"{match.group(1)}:{match.group(2)}"
        return raw

    @staticmethod
    def _dedupe_preserve(values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            item = str(value or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    def _format_items(self) -> list[str]:
        base = self._dedupe_preserve(self._base_formats)
        if self._show_all_formats:
            return self._dedupe_preserve(base + self._other_formats)
        tail_label = FORMAT_LOADING_LABEL if self._formats_loading else FORMAT_LOAD_LABEL
        return [*base, tail_label]

    def _refresh_format_combo(self, preferred: str | None = None) -> None:
        items = self._format_items()
        fallback = preferred or self._last_non_loader_format or "VIDEO"
        if fallback not in items:
            fallback = "VIDEO" if "VIDEO" in items else (items[0] if items else "")
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        self.format_combo.addItems(items)
        if fallback:
            self.format_combo.setCurrentText(fallback)
        self.format_combo.blockSignals(False)
        selected = self.format_combo.currentText().strip()
        if selected and selected not in {FORMAT_LOAD_LABEL, FORMAT_LOADING_LABEL}:
            self._last_non_loader_format = selected

    def set_formats_loading(self, loading: bool) -> None:
        self._formats_loading = bool(loading)
        if self._formats_loading:
            self._show_all_formats = False
            self.format_combo.blockSignals(True)
            self.format_combo.clear()
            self.format_combo.addItems(self._format_items())
            if FORMAT_LOADING_LABEL in [self.format_combo.itemText(i) for i in range(self.format_combo.count())]:
                self.format_combo.setCurrentText(FORMAT_LOADING_LABEL)
            self.format_combo.blockSignals(False)
            return
        self._refresh_format_combo(preferred=self._last_non_loader_format or "VIDEO")

    def _set_quality_stale(self, stale: bool) -> None:
        self._quality_stale = bool(stale)

    def _set_quality_choices(self, qualities: list[str], *, preferred: str = "BEST QUALITY") -> None:
        items = self._dedupe_preserve(qualities) or ["BEST QUALITY"]
        if "BEST QUALITY" not in items:
            items.insert(0, "BEST QUALITY")
        selected = preferred if preferred in items else "BEST QUALITY"
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        self.quality_combo.addItems(items)
        self.quality_combo.setCurrentText(selected)
        self.quality_combo.blockSignals(False)

    def _sync_quality_combo_state(self) -> None:
        selected_format = self.format_combo.currentText().strip().upper()
        is_audio = is_audio_format_choice(selected_format)
        if is_audio and self.quality_combo.currentText().strip() != "BEST QUALITY":
            self._set_quality_choices(["BEST QUALITY"], preferred="BEST QUALITY")
        can_interact = (not self._controls_locked) and (not self._single_url_validating)
        self.quality_combo.setEnabled(can_interact)
        self.quality_combo.setProperty("_mc_block_popup", is_audio)

    def reset_format_quality_for_url_change(self) -> None:
        selected = self.format_combo.currentText().strip()
        selected_was_loaded = selected in self._other_formats
        self._formats_loading = False
        self._show_all_formats = False
        self._other_formats = []
        next_format = "VIDEO" if selected_was_loaded else selected
        if next_format not in self._base_formats:
            next_format = "VIDEO"
        self._last_non_loader_format = next_format
        self._refresh_format_combo(preferred=next_format)
        self._set_quality_choices(["BEST QUALITY"], preferred="BEST QUALITY")
        self._set_quality_stale(True)
        self._sync_quality_combo_state()

    def _on_format_combo_changed(self, value: str) -> None:
        selected = str(value or "").strip()
        if selected == FORMAT_LOADING_LABEL:
            return
        if selected == FORMAT_LOAD_LABEL:
            fallback = self._last_non_loader_format if self._last_non_loader_format else "VIDEO"
            if not self._other_formats:
                self.set_formats_loading(True)
                self.loadOtherFormatsRequested.emit()
            else:
                self._show_all_formats = True
                self._refresh_format_combo(preferred=fallback)
            self._sync_quality_combo_state()
            return
        if selected:
            self._last_non_loader_format = selected
        self._sync_quality_combo_state()
        self.singleFormatChanged.emit(str(selected or "VIDEO").strip().upper() or "VIDEO")

    def _on_quality_combo_changed(self, value: str) -> None:
        selected = str(value or "").strip().upper() or "BEST QUALITY"
        self.singleQualityChanged.emit(selected)

    def _paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self._show_info_dialog("Clipboard empty", "There is no text in the clipboard to paste.")
            return
        if self._is_batch_mode_enabled():
            self.multiBulkAddRequested.emit(text)
            return
        self._set_url_text(text)
        self.single_url_input.setCursorPosition(0)
        self._start_paste_text_color_animation()

    def _start_paste_text_color_animation(self) -> None:
        self._paste_text_color_anim.stop()
        start = QColor(self.theme.accent)
        end = QColor(self.theme.text_primary)
                                                                   
        self.single_url_input.setStyleSheet(f"QLineEdit#singleUrlInput {{ color: {start.name()}; }}")
        self._paste_text_color_anim.setStartValue(start)
        self._paste_text_color_anim.setKeyValueAt(0.14, start)
        self._paste_text_color_anim.setEndValue(end)
        self._paste_text_color_anim.start()

    def _on_paste_text_color_anim_value(self, value: object) -> None:
        color = value if isinstance(value, QColor) else QColor(value)
        self.single_url_input.setStyleSheet(f"QLineEdit#singleUrlInput {{ color: {color.name()}; }}")

    def _reset_single_url_text_color(self) -> None:
        self.single_url_input.setStyleSheet("")

    def _set_single_meta_thumbnail_placeholder(self) -> None:
        self.single_meta_thumbnail_label.clear()
        self.single_meta_thumbnail_label.setText("THUMBNAIL")
        self.single_meta_thumbnail_label.setToolTip(self._single_meta_thumbnail_source)
        self._single_meta_thumbnail_original = None

    def _apply_single_meta_thumbnail_pixmap(self) -> None:
        if self._single_meta_thumbnail_original is None:
            return
        target = self.single_meta_thumbnail_label.size()
        if target.width() <= 0 or target.height() <= 0:
            return
        rounded = rounded_pixmap(
            self._single_meta_thumbnail_original,
            target,
            max(6, int(round(target.height() * 0.16))),
        )
        self.single_meta_thumbnail_label.setPixmap(rounded)
        self.single_meta_thumbnail_label.setText("")
        self.single_meta_thumbnail_label.setToolTip(self._single_meta_thumbnail_source)

    def set_single_url_thumbnail(self, image_data: bytes | None, source_url: str = "") -> None:
        normalized_source = str(source_url or "").strip()
        if normalized_source != self._single_meta_thumbnail_source:
            self._single_meta_thumbnail_source = normalized_source
        if not image_data:
            self._set_single_meta_thumbnail_placeholder()
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(bytes(image_data)):
            self._set_single_meta_thumbnail_placeholder()
            return
        self._single_meta_thumbnail_original = pixmap
        self._apply_single_meta_thumbnail_pixmap()

    def _truncate_single_meta_title(self, title: str) -> str:
        value = str(title or "").strip()
        if not value:
            return ""
        metrics = QFontMetrics(self.single_meta_title_label.font())
        text_col = getattr(self, "_single_meta_text_col", None)
        text_col_width = text_col.width() if isinstance(text_col, QWidget) else 0
        text_col_margins = self._single_meta_text_layout.contentsMargins()
        text_col_available = text_col_width - text_col_margins.left() - text_col_margins.right()
        available = max(
            92,
            self.single_meta_title_label.width(),
            self._single_meta_top_row.geometry().width() - 2,
            text_col_available - 2,
        )
        return metrics.elidedText(value, Qt.ElideRight, available)

    def _refresh_single_meta_title(self) -> None:
        text = self._truncate_single_meta_title(self._single_meta_full_title)
        self.single_meta_title_label.setText(text)
        self.single_meta_title_label.setToolTip(
            self._single_meta_full_title if self._single_meta_full_title and text != self._single_meta_full_title else ""
        )

    def _truncate_single_meta_line(self, label: QLabel, value: str, *, min_width: int = 80) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        metrics = QFontMetrics(label.font())
        text_col = getattr(self, "_single_meta_text_col", None)
        text_col_width = text_col.width() if isinstance(text_col, QWidget) else 0
        text_col_margins = self._single_meta_text_layout.contentsMargins()
        text_col_available = text_col_width - text_col_margins.left() - text_col_margins.right()
        available = max(min_width, label.width(), label.geometry().width(), text_col_available - 2)
        return metrics.elidedText(value, Qt.ElideRight, available)

    def _refresh_single_meta_display(self) -> None:
        self._refresh_single_meta_title()
        self._refresh_single_meta_lines()

    def _flush_single_meta_refresh(self) -> None:
        self._single_meta_refresh_pending = False
        self._refresh_single_meta_display()

    def _schedule_single_meta_refresh(self) -> None:
        if self._single_meta_refresh_pending:
            return
        self._single_meta_refresh_pending = True
        QTimer.singleShot(0, self._flush_single_meta_refresh)

    @staticmethod
    def _sanitize_meta_message(message: str) -> str:
        cleaned = str(message or "")
        cleaned = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", cleaned)
        cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", cleaned)
        cleaned = cleaned.replace("\r", "\n")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _split_meta_message_lines(message: str, *, max_lines: int, max_chars: int) -> list[str]:
        fragments: list[str] = []
        for raw_part in re.split(r"[\n|]+", str(message or "")):
            part = str(raw_part or "").strip()
            if not part:
                continue
            while len(part) > max_chars and len(fragments) < max_lines:
                split_at = part.rfind(" ", 0, max_chars + 1)
                if split_at < (max_chars // 2):
                    split_at = max_chars
                head = part[:split_at].strip()
                if head:
                    fragments.append(head)
                part = part[split_at:].strip()
            if part and len(fragments) < max_lines:
                fragments.append(part)
            if len(fragments) >= max_lines:
                break
        return fragments[:max_lines]

    @staticmethod
    def _parse_single_meta_lines(
        *,
        state: str,
        size_text: str,
        message: str,
    ) -> tuple[str, list[str]]:
        normalized_state = str(state or "").strip().lower()
        resolved_size = str(size_text or "").strip() or "Unknown"
        size_line = f"Size: {resolved_size}"
        extras: list[str] = []

        raw_message = MainWindow._sanitize_meta_message(str(message or ""))
        if raw_message:
            parts = MainWindow._split_meta_message_lines(raw_message, max_lines=8, max_chars=84)
            for part in parts:
                lowered = part.lower()
                if lowered.startswith("title:"):
                    continue
                if lowered.startswith("size:"):
                    size_line = part
                    continue
                extras.append(part)
        else:
            if normalized_state == "validating":
                extras.extend(
                    [
                        "Validating link...",
                        "Loading formats and quality...",
                        "Preparing metadata preview...",
                    ]
                )
            elif normalized_state == "disabled":
                extras.extend(
                    [
                        "Metadata preview is disabled.",
                        "Enable metadata fetching in Settings",
                        "to load title, size, and formats.",
                    ]
                )
            elif normalized_state in {"invalid", "error"}:
                extras.append("Invalid or unsupported URL.")
            elif normalized_state == "ready":
                extras.append("Ready to download.")

        extras = extras[:3]
        while len(extras) < 3:
            extras.append("")
        return size_line, extras

    def _refresh_single_meta_lines(self) -> None:
        size_text = self._truncate_single_meta_line(self.single_meta_size_label, self._single_meta_full_size, min_width=90)
        self.single_meta_size_label.setText(size_text)
        self.single_meta_size_label.setVisible(bool(size_text))
        self.single_meta_size_label.setToolTip(
            self._single_meta_full_size if self._single_meta_full_size and size_text != self._single_meta_full_size else ""
        )

        for index, label in enumerate(self.single_meta_info_labels):
            raw = str(self._single_meta_full_info_lines[index] if index < len(self._single_meta_full_info_lines) else "").strip()
            text = self._truncate_single_meta_line(label, raw, min_width=90)
            label.setText(text if text else "")
            label.setVisible(bool(text))
            label.setToolTip(raw if raw and text != raw else "")

    def _sync_single_meta_visibility(self) -> None:
        single_mode = not self._is_batch_mode_enabled()
        self.single_features_row.setVisible(True)
        show_left_meta = single_mode and (not self._settings_visible)
        self.single_meta_row.setVisible(show_left_meta)
        if show_left_meta:
            self._refresh_single_meta_display()
            self._schedule_single_meta_refresh()

    def set_single_url_analysis_state(
        self,
        state: str,
        *,
        title: str = "",
        size_text: str = "Unknown",
        message: str = "",
    ) -> None:
        normalized = str(state or "").strip().lower()
        self._single_meta_state = normalized or "idle"
        if normalized == "idle":
            self.single_meta_status_label.setText("Idle")
            self.single_meta_status_label.setProperty("state", "idle")
            status_style = self.single_meta_status_label.style()
            status_style.unpolish(self.single_meta_status_label)
            status_style.polish(self.single_meta_status_label)
            self.single_meta_status_label.update()
            self._single_meta_full_title = "Waiting for URL..."
            self._single_meta_full_size = ""
            self._single_meta_full_info_lines = [
                "Paste a link to load metadata.",
                "",
                "",
            ]
            self._refresh_single_meta_display()
            self._schedule_single_meta_refresh()
            self.set_single_url_thumbnail(None, "")
            self._sync_single_meta_visibility()
            return

        state_map = {
            "validating": ("Validating", "validating"),
            "ready": ("Ready", "valid"),
            "invalid": ("Invalid", "invalid"),
            "error": ("Invalid", "failed"),
            "disabled": ("Idle", "idle"),
        }
        status_text, status_state = state_map.get(normalized, ("Invalid", "failed"))
        self.single_meta_status_label.setText(status_text)
        self.single_meta_status_label.setProperty("state", status_state)
        status_style = self.single_meta_status_label.style()
        status_style.unpolish(self.single_meta_status_label)
        status_style.polish(self.single_meta_status_label)
        self.single_meta_status_label.update()

        title_text = str(title or "").strip()
        if normalized == "validating":
            title_text = title_text or "Checking URL metadata..."
        elif normalized == "disabled":
            title_text = title_text or "Metadata preview disabled"
        elif normalized in {"invalid", "error"}:
            title_text = title_text or "Invalid URL"
        elif not title_text:
            title_text = "Untitled media"
        self._single_meta_full_title = title_text

        size_line, info_lines = self._parse_single_meta_lines(
            state=normalized,
            size_text=str(size_text or "Unknown"),
            message=str(message or "").strip(),
        )
        self._single_meta_full_size = size_line
        self._single_meta_full_info_lines = info_lines
        self._refresh_single_meta_display()
        self._schedule_single_meta_refresh()
        if normalized == "disabled":
            self.set_single_url_thumbnail(None, "")
        self._sync_single_meta_visibility()

    def set_single_url_validation_busy(self, validating: bool) -> None:
        self._single_url_validating = bool(validating)
        self._apply_single_input_lock_state()

    def is_quality_stale(self) -> bool:
        return bool(self._quality_stale)

    def _apply_single_input_lock_state(self) -> None:
        single_mode = not self._is_batch_mode_enabled()
        editable = single_mode and (not self._controls_locked)
        self.single_url_input.setEnabled(editable)
        self.single_url_input.setReadOnly(not editable)
        self.single_url_input.setProperty("validating", self._single_url_validating)
        style = self.single_url_input.style()
        style.unpolish(self.single_url_input)
        style.polish(self.single_url_input)
        self.single_url_input.update()
        self.paste_button.setEnabled(editable)

    def refresh_cursor_state(self) -> None:
        self.setCursor(Qt.ArrowCursor)
        root = self.centralWidget()
        if root is not None:
            root.setCursor(Qt.ArrowCursor)
        self._set_interaction_cursors()

    def _apply_dialog_theme(self, widget: QWidget) -> None:
        apply_dialog_theme(
            widget,
            self.theme,
            apply_titlebar_theme=self.apply_windows_titlebar_theme,
            button_setup=lambda button: button.setCursor(
                Qt.PointingHandCursor if button.isEnabled() else Qt.ArrowCursor
            ),
        )

    def _build_message_box(
        self,
        *,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButtons = QMessageBox.Ok,
        default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    ) -> QMessageBox:
        return build_message_box(
            parent=self,
            theme=self.theme,
            app_name=APP_NAME,
            icon=icon,
            title=title,
            text=text,
            window_icon=self.windowIcon(),
            buttons=buttons,
            default_button=default_button,
            apply_titlebar_theme=self.apply_windows_titlebar_theme,
            button_setup=lambda button: button.setCursor(
                Qt.PointingHandCursor if button.isEnabled() else Qt.ArrowCursor
            ),
        )

    def _exec_dialog(self, dialog: QWidget) -> int:
        return exec_dialog(dialog, on_after=self.refresh_cursor_state)

    def _show_info_dialog(self, title: str, text: str) -> int:
        return self._exec_dialog(
            self._build_message_box(icon=QMessageBox.Information, title=title, text=text)
        )

    def _browse_download_location(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose download location",
            self.download_location_edit.text().strip(),
        )
        if not selected:
            return
        self.download_location_edit.setText(selected)
        self.downloadLocationChanged.emit(selected)

    def set_close_handler(self, handler: Callable[[], bool]) -> None:
        self._close_handler = handler

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._close_handler and not self._close_handler():
            event.ignore()
            return
        event.accept()

    def set_settings_visible(self, visible: bool, *, animated: bool = True) -> None:
        self._settings_visible = bool(visible)
        self.settings_toggle_button.setText("Hide settings" if self._settings_visible else "Show settings")
        self._update_batch_entry_control_visibility()
        if self._is_batch_mode_enabled():
            self._action_row_layout.setStretch(0, 1)
            self._action_row_layout.setStretch(1, 0)
            self._action_row_layout.setStretch(2, 1)
        elif self._settings_visible:
            self._action_row_layout.setStretch(0, 12)
            self._action_row_layout.setStretch(1, 14)
            self._action_row_layout.setStretch(2, 5)
        else:
            self._action_row_layout.setStretch(0, 12)
            self._action_row_layout.setStretch(1, 13)
            self._action_row_layout.setStretch(2, 6)
        self._sync_single_meta_visibility()
        self.settings_animation.stop()
        self._settings_animation_expected_end_width = None
        if self._settings_visible:
            self._settings_target_width = self._compute_settings_target_width(self._render_scale, self.width())
        end_width = self._settings_target_width if self._settings_visible else 0
        self.settings_panel.setMinimumWidth(0)
        if animated:
            self.settings_animation.setStartValue(self.settings_panel.maximumWidth())
            self.settings_animation.setEndValue(end_width)
            self._settings_animation_expected_end_width = int(end_width)
            self.settings_animation.start()
        else:
            self._set_settings_container_width(end_width)
            self._content_row_layout.invalidate()
            self._content_row_layout.activate()
            self._sync_tutorial_overlay()

    def is_settings_visible(self) -> bool:
        return bool(self._settings_visible)

    def current_theme_mode(self) -> str:
        return self._theme_mode

    def set_theme(self, theme: ThemePalette, mode: str) -> None:
        self.theme = theme
        self._theme_mode = "light" if mode == "light" else "dark"
        self._paste_text_color_anim.stop()
        self._reset_single_url_text_color()
        self._refresh_control_style_colors()
        self._apply_window_layout()
        self._apply_combo_arrow_palette()
        self._refresh_theme_toggle_icon()
        overlay = getattr(self, "_tutorial_overlay", None)
        if overlay is not None:
            overlay.set_theme(self.theme)
        self._sync_tutorial_overlay()
        self._set_interaction_cursors()
        self.apply_windows_titlebar_theme()

    def tutorialTargets(self) -> dict[str, object]:
        return {
            "main_ui": self.main_column,
            "single_input": self.single_url_input,
            "format_quality": self._format_quality_row,
            "single_actions": self.single_action_row,
            "progress_bar": self.download_progress,
            "console": self.console_card,
            "progress_console": (self.download_progress, self.console_card),
            "settings_panel": self.settings_panel,
            "settings_general": self.settings_general_card,
            "settings_interface": self.settings_interface_card,
            "settings_downloads": self.settings_downloads_card,
            "settings_updates": self.settings_updates_card,
            "settings_dependencies": self.settings_dependency_card,
            "settings_history": self.settings_history_card,
            "multi_url": self.multi_toolbar_row,
            "multi_entries": self.multi_entries_scroll,
            "mode_switch": self.mode_holder,
        }

    def ensure_settings_target_visible(self, target: object) -> None:
        targets = target if isinstance(target, (list, tuple)) else (target,)
        for item in targets:
            if not isinstance(item, QWidget):
                continue
            if not self._is_descendant_of(item, self.settings_scroll):
                continue
            self.settings_scroll.ensureWidgetVisible(item, 0, self._scaled(18, self._render_scale, 10))
        self._sync_tutorial_overlay()

    def ensure_multi_target_visible(self, target: object) -> None:
        targets = target if isinstance(target, (list, tuple)) else (target,)
        scroll_margin = self._scaled(18, self._render_scale, 10)
        for item in targets:
            if not isinstance(item, QWidget):
                continue
            if not self._is_descendant_of(item, self.batch_inline_section):
                continue
            if not self._is_descendant_of(item, self.multi_entries_scroll):
                continue
            if item is self.multi_entries_scroll:
                bar = self.multi_entries_scroll.verticalScrollBar()
                if bar.maximum() > bar.minimum():
                    bar.setValue(bar.minimum())
                continue
            self.multi_entries_scroll.ensureWidgetVisible(item, 0, scroll_margin)
        self._sync_tutorial_overlay()

    def set_tutorial_mode(self, active: bool) -> None:
        self._tutorial_mode = bool(active)
        overlay = getattr(self, "_tutorial_overlay", None)
        if overlay is None:
            return
        if self._tutorial_mode:
            self._sync_tutorial_overlay()
            overlay.show()
            overlay.raise_()
            overlay.setFocus(Qt.ActiveWindowFocusReason)
        else:
            overlay.hide()

    def update_tutorial_step(
        self,
        *,
        title: str,
        body: str,
        index: int,
        total: int,
        target_widget: object,
    ) -> None:
        overlay = getattr(self, "_tutorial_overlay", None)
        if overlay is None:
            return
        target_rect: QRect | None = None
        root = self.centralWidget()
        targets = target_widget if isinstance(target_widget, (list, tuple)) else (target_widget,)
        if root is not None:
            for item in targets:
                if not isinstance(item, QWidget):
                    continue
                if not item.isVisible() or item.width() <= 0 or item.height() <= 0:
                    continue
                top_left = item.mapTo(root, QPoint(0, 0))
                item_rect = QRect(
                    int(top_left.x()),
                    int(top_left.y()),
                    int(item.width()),
                    int(item.height()),
                )
                target_rect = item_rect if target_rect is None else target_rect.united(item_rect)
        overlay.set_step(
            title=title,
            body=body,
            index=index,
            total=total,
            is_first=index <= 0,
            is_last=index >= (total - 1),
        )
        overlay.set_target_rect(target_rect)
        overlay.raise_()
        overlay.setFocus(Qt.ActiveWindowFocusReason)

    def _sync_tutorial_overlay(self) -> None:
        overlay = getattr(self, "_tutorial_overlay", None)
        if overlay is None:
            return
        overlay.sync_to_parent()

    @staticmethod
    def _run_with_blocked_signals(widget: QWidget, action: Callable[[], None]) -> None:
        widget.blockSignals(True)
        try:
            action()
        finally:
            widget.blockSignals(False)

    def _apply_config_ui_scale(self, config: AppConfig) -> None:
        scale_percent = self._normalize_ui_scale_percent(config.ui_scale_percent)
        self._run_with_blocked_signals(
            self.ui_scale_slider,
            lambda: self.ui_scale_slider.setValue(scale_percent),
        )
        self._pending_ui_scale_percent = None
        self._apply_ui_scale(scale_percent, emit=False)

    def _apply_config_mode_and_location(self, config: AppConfig) -> None:
        self.download_location_edit.setText(str(config.download_location))
        batch_enabled = bool(config.batch_enabled)
        self.set_batch_mode_enabled(batch_enabled, emit=False)

    def _apply_config_batch_controls(self, config: AppConfig) -> None:
        self._run_with_blocked_signals(
            self.batch_concurrency_slider,
            lambda: self.batch_concurrency_slider.setValue(max(1, min(16, int(config.batch_concurrency)))),
        )
        self._on_batch_concurrency_changed(self.batch_concurrency_slider.value())

        self._run_with_blocked_signals(
            self.skip_existing_checkbox,
            lambda: self.skip_existing_checkbox.setChecked(bool(config.skip_existing_files)),
        )
        self._run_with_blocked_signals(
            self.auto_start_ready_links_checkbox,
            lambda: self.auto_start_ready_links_checkbox.setChecked(bool(config.auto_start_ready_links)),
        )
        self._run_with_blocked_signals(
            self.disable_metadata_fetch_checkbox,
            lambda: self.disable_metadata_fetch_checkbox.setChecked(bool(config.disable_metadata_fetch)),
        )
        self._run_with_blocked_signals(
            self.fallback_metadata_checkbox,
            lambda: self.fallback_metadata_checkbox.setChecked(bool(config.fallback_download_on_metadata_error)),
        )
        self._run_with_blocked_signals(
            self.batch_retry_slider,
            lambda: self.batch_retry_slider.setValue(max(0, min(3, int(config.batch_retry_count)))),
        )
        self._on_batch_retry_changed(self.batch_retry_slider.value())
        retry_profile = str(config.retry_profile or RetryProfile.BASIC.value).strip().lower()
        retry_index = self.retry_profile_combo.findData(retry_profile, Qt.UserRole)
        if retry_index < 0:
            retry_index = self.retry_profile_combo.findData(RetryProfile.BASIC.value, Qt.UserRole)
        self._run_with_blocked_signals(
            self.retry_profile_combo,
            lambda: self.retry_profile_combo.setCurrentIndex(max(0, retry_index)),
        )

    @staticmethod
    def _conflict_policy_label(policy_value: str | None) -> str:
        policy_map = {
            "skip": "Skip existing file",
            "rename": "Rename output",
            "overwrite": "Overwrite existing file",
        }
        normalized = str(policy_value or "skip").strip().lower()
        return policy_map.get(normalized, "Skip existing file")

    def _apply_config_download_preferences(self, config: AppConfig) -> None:
        template_value = str(config.filename_template or DEFAULT_FILENAME_TEMPLATE)
        self._set_filename_template_ui(template_value, emit=False)

        self._run_with_blocked_signals(
            self.conflict_policy_combo,
            lambda: self.conflict_policy_combo.setCurrentText(self._conflict_policy_label(config.conflict_policy)),
        )
        self._on_conflict_policy_changed(self.conflict_policy_combo.currentText())

        self._run_with_blocked_signals(
            self.speed_limit_slider,
            lambda: self.speed_limit_slider.setValue(_speed_limit_slider_value_from_kbps(int(config.download_speed_limit_kbps))),
        )
        self._on_speed_limit_changed(self.speed_limit_slider.value())

        self._run_with_blocked_signals(
            self.adaptive_concurrency_checkbox,
            lambda: self.adaptive_concurrency_checkbox.setChecked(bool(config.adaptive_batch_concurrency)),
        )

    def _apply_config_update_preferences(self, config: AppConfig) -> None:
        self._run_with_blocked_signals(
            self.auto_updates_checkbox,
            lambda: self.auto_updates_checkbox.setChecked(bool(config.auto_check_updates)),
        )
        self._run_with_blocked_signals(
            self.disable_history_checkbox,
            lambda: self.disable_history_checkbox.setChecked(bool(config.disable_history)),
        )
        cleanup_hours = max(0, int(config.stale_part_cleanup_hours))
        stale_index = self.stale_part_cleanup_combo.findData(cleanup_hours, Qt.UserRole)
        if stale_index < 0:
            stale_index = self.stale_part_cleanup_combo.findData(48, Qt.UserRole)
        self._run_with_blocked_signals(
            self.stale_part_cleanup_combo,
            lambda: self.stale_part_cleanup_combo.setCurrentIndex(max(0, stale_index)),
        )

    def _finalize_config_apply(self) -> None:
        self._single_url_validating = False
        self.set_single_url_analysis_state("idle")
        self._set_quality_stale(True)
        self._sync_quality_combo_state()
        self._apply_single_input_lock_state()

    def set_config(self, config: AppConfig) -> None:
        self._apply_config_ui_scale(config)
        self._apply_config_mode_and_location(config)
        self._apply_config_batch_controls(config)
        self._apply_config_download_preferences(config)
        self._apply_config_update_preferences(config)
        self._finalize_config_apply()

    def download_payload(self) -> dict[str, object]:
        fmt = self.format_combo.currentText().strip() or "VIDEO"
        if fmt in {FORMAT_LOAD_LABEL, FORMAT_LOADING_LABEL}:
            fmt = "VIDEO"
        return {
            "url_text": self._current_url_text(),
            "format_choice": fmt,
            "quality_choice": self.quality_combo.currentText().strip() or "BEST QUALITY",
            "batch_enabled": self._is_batch_mode_enabled(),
            "download_location": self.download_location_edit.text().strip(),
            "batch_concurrency": int(self.batch_concurrency_slider.value()),
            "skip_existing_files": bool(self.skip_existing_checkbox.isChecked()),
            "auto_start_ready_links": bool(self.auto_start_ready_links_checkbox.isChecked()),
            "disable_metadata_fetch": bool(self.disable_metadata_fetch_checkbox.isChecked()),
            "fallback_download_on_metadata_error": bool(self.fallback_metadata_checkbox.isChecked()),
            "batch_retry_count": int(self.batch_retry_slider.value()),
            "retry_profile": self._current_retry_profile(),
            "filename_template": self._effective_filename_template(),
            "conflict_policy": (
                "overwrite"
                if self.conflict_policy_combo.currentText().strip().lower().startswith("overwrite")
                else "rename"
                if self.conflict_policy_combo.currentText().strip().lower().startswith("rename")
                else "skip"
            ),
            "speed_limit_kbps": int(_speed_limit_kbps_from_slider_value(self.speed_limit_slider.value())),
            "adaptive_batch_concurrency": bool(self.adaptive_concurrency_checkbox.isChecked()),
            "stale_part_cleanup_hours": int(self.stale_part_cleanup_combo.currentData(Qt.UserRole) or 0),
        }

    def set_formats_and_qualities(
        self,
        formats: list[str],
        qualities: list[str],
        *,
        other_formats: list[str] | None = None,
        reveal_all_formats: bool = False,
    ) -> None:
        if formats:
            self._base_formats = self._dedupe_preserve([item for item in formats if item != FORMAT_LOAD_LABEL])
        if other_formats is not None:
            self._other_formats = self._dedupe_preserve([item for item in other_formats if item])
        self._formats_loading = False
        if reveal_all_formats:
            self._show_all_formats = True

        current_format = self.format_combo.currentText().strip()
        self._refresh_format_combo(preferred=current_format or self._last_non_loader_format or "VIDEO")

        current_quality = self.quality_combo.currentText().strip() or "BEST QUALITY"
        self._set_quality_choices(qualities or ["BEST QUALITY"], preferred=current_quality)
        self._set_quality_stale(False)
        self._sync_quality_combo_state()

    def append_log(self, text: str) -> None:
        value = str(text or "").strip()
        if not value:
            return
        self.console_output.appendPlainText(value)
        self.console_output.verticalScrollBar().setValue(self.console_output.verticalScrollBar().maximum())

    def clear_log(self) -> None:
        self.console_output.clear()

    def set_download_progress(self, percent: float | int) -> None:
        clamped = max(0.0, min(100.0, float(percent)))
        scaled = int(round(clamped * 100))
        label = "Download complete" if scaled >= 10000 else f"{clamped:.2f}%"
        if self.download_progress.value() == scaled and self.download_progress.format() == label:
            return
        if self.download_progress.value() != scaled:
            self.download_progress.setValue(scaled)
        if self.download_progress.format() != label:
            self.download_progress.setFormat(label)

    def reset_download_progress(self) -> None:
        self.set_download_progress(0.0)

    def set_controls_busy(self, busy: bool) -> None:
        locked = bool(busy)
        batch_mode = self._is_batch_mode_enabled()
        allow_multi_queue_edit = locked and batch_mode
        self._controls_locked = locked
        self.download_button.setEnabled((not locked) or batch_mode)
        self.pause_resume_button.setEnabled(locked)
        if not locked:
            self.pause_resume_button.setText("PAUSE ALL" if batch_mode else "PAUSE")
        self.stop_button.setEnabled(locked)
        self._apply_single_input_lock_state()
        self.format_combo.setEnabled((not locked) and (not self._single_url_validating))
        self.single_mode_button.setEnabled(not locked)
        self.multi_mode_button.setEnabled(not locked)
        self.batch_concurrency_slider.setEnabled(not locked)
        self.skip_existing_checkbox.setEnabled(not locked)
        self.auto_start_ready_links_checkbox.setEnabled(not locked)
        self.disable_metadata_fetch_checkbox.setEnabled(not locked)
        self.fallback_metadata_checkbox.setEnabled(not locked)
        self.batch_retry_slider.setEnabled(not locked)
        self.retry_profile_combo.setEnabled(not locked)
        self.filename_template_combo.setEnabled(not locked)
        self.filename_template_custom_edit.setEnabled(not locked)
        self.conflict_policy_combo.setEnabled(not locked)
        self.speed_limit_slider.setEnabled(not locked)
        self.adaptive_concurrency_checkbox.setEnabled(not locked)
        self.multi_add_input.setEnabled((not locked) or allow_multi_queue_edit)
        self.multi_add_button.setEnabled((not locked) or allow_multi_queue_edit)
        self.multi_bulk_button.setEnabled((not locked) or allow_multi_queue_edit)
        self.multi_import_button.setEnabled((not locked) or allow_multi_queue_edit)
        self.multi_export_button.setEnabled((not locked) or allow_multi_queue_edit)
        self.multi_search_input.setEnabled((not locked) or allow_multi_queue_edit)
        self.multi_status_filter.setEnabled((not locked) or allow_multi_queue_edit)
        self.history_retry_button.setEnabled(not locked)
        self.history_clear_button.setEnabled(not locked)
        self.disable_history_checkbox.setEnabled(not locked)
        self.stale_part_cleanup_combo.setEnabled(not locked)
        self.reset_settings_button.setEnabled(not locked)
        for row in self._batch_entry_widgets.values():
            row.set_busy(locked)
        self._sync_quality_combo_state()
        self._set_interaction_cursors()

    def set_single_pause_resume_state(self, *, paused: bool, enabled: bool) -> None:
        self.pause_resume_button.setText("RESUME" if bool(paused) else "PAUSE")
        self.pause_resume_button.setEnabled(bool(enabled))

    def set_multi_pause_resume_state(self, *, paused: bool, enabled: bool) -> None:
        self.pause_resume_button.setText("RESUME ALL" if bool(paused) else "PAUSE ALL")
        self.pause_resume_button.setEnabled(bool(enabled))

    def set_download_progress_count(self, completed: int, total: int) -> None:
        total_input = int(total)
        if total_input <= 0:
            completed_value = 0
            total_value = 0
            scaled = 0
        else:
            total_value = total_input
            completed_value = max(0, min(total_value, int(completed)))
            scaled = int(round((completed_value / total_value) * 10000))
        if self.download_progress.value() != scaled:
            self.download_progress.setValue(scaled)
        self.download_progress.setFormat(f"{completed_value}/{total_value} downloaded")

    def set_dependency_state(self, name: str, installed: bool, path: str = "") -> None:
        text = f"{name}: installed" if installed else f"{name}: missing"
        tooltip = str(path or "").strip() if installed else ""
        lowered = name.lower()
        self._dependency_installed[lowered] = bool(installed)
        if lowered == "ffmpeg":
            self.ffmpeg_status_label.setText(text)
            self.ffmpeg_status_label.setToolTip(tooltip)
            self.ffmpeg_install_button.setText("Already installed" if installed else "Install FFmpeg")
            self.ffmpeg_install_button.setEnabled(not installed)
        elif lowered == "node":
            self.node_status_label.setText(text)
            self.node_status_label.setToolTip(tooltip)
            self.node_install_button.setText("Already installed" if installed else "Install Node.js")
            self.node_install_button.setEnabled(not installed)
        self._set_interaction_cursors()

    def set_dependency_install_busy(self, name: str, busy: bool) -> None:
        lowered = name.lower()
        if lowered == "ffmpeg":
            if busy:
                self.ffmpeg_install_button.setText("Installing...")
                self.ffmpeg_install_button.setEnabled(False)
            else:
                installed = self._dependency_installed.get("ffmpeg", False)
                self.ffmpeg_install_button.setText("Already installed" if installed else "Install FFmpeg")
                self.ffmpeg_install_button.setEnabled(not installed)
        elif lowered == "node":
            if busy:
                self.node_install_button.setText("Installing...")
                self.node_install_button.setEnabled(False)
            else:
                installed = self._dependency_installed.get("node", False)
                self.node_install_button.setText("Already installed" if installed else "Install Node.js")
                self.node_install_button.setEnabled(not installed)
        self._set_interaction_cursors()

    def set_update_button_busy(self, busy: bool) -> None:
        self.check_updates_button.setEnabled(not bool(busy))
        self._set_interaction_cursors()
