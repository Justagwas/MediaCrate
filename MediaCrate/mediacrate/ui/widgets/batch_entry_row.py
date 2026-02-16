from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ...core.models import BatchEntry, is_audio_format_choice
from ..batch_entry_presenter import build_batch_entry_view_state
from ..layout_metrics import normalize_scale_factor as _normalize_scale_factor
from ..widget_utils import rounded_pixmap, set_widget_pointer_cursor
from .controls import ChevronComboBox


class BatchEntryRowWidget(QFrame):
    downloadRequested = Signal(str)
    pauseRequested = Signal(str)
    resumeRequested = Signal(str)
    removeRequested = Signal(str)
    formatChanged = Signal(str, str)
    qualityChanged = Signal(str, str)
    qualityUnavailableRequested = Signal(str)

    def __init__(self, entry_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._url_elide_extra_px_default = 0
        self._url_elide_extra_px_compact = 0
        self._url_elide_extra_px = self._url_elide_extra_px_default
        self._entry_id = str(entry_id or "").strip()
        self._full_url_text = ""
        self._thumbnail_source_url = ""
        self._thumbnail_original: QPixmap | None = None
        self._full_detail_text = ""
        self._busy = False
        self._can_download = False
        self._can_remove = True
        self._quality_allowed = True
        self._show_format_quality = True
        self._show_detail = True
        self._settings_compact_mode = False
        self._primary_action = "download"
        self._is_duplicate_visual = False
        self._ui_scale = 1.0
        self._last_entry_signature: tuple[object, ...] | None = None
        self._last_status_state = ""
        self._last_url_state = ""
        self._last_formats: tuple[str, ...] = ()
        self._last_qualities: tuple[str, ...] = ()
        self._deferred_elide_timer = QTimer(self)
        self._deferred_elide_timer.setSingleShot(True)
        self._deferred_elide_timer.setInterval(40)
        self._deferred_elide_timer.timeout.connect(self._update_text_elide)
        self.setObjectName("batchEntryCard")
        self.setMinimumWidth(760)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        root_layout = QHBoxLayout(self)
        self._root_base_margins = (8, 7, 8, 7)
        root_layout.setContentsMargins(*self._root_base_margins)
        root_layout.setSpacing(8)
        self._root_layout = root_layout

        self.thumbnail_label = QLabel("THUMB\nNAIL", self)
        self.thumbnail_label.setObjectName("batchEntryThumbnail")
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setFixedSize(74, 74)
        root_layout.addWidget(self.thumbnail_label, 0, Qt.AlignTop)

        right_container = QWidget(self)
        self._right_container = right_container
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        self._right_layout = right_layout
        root_layout.addWidget(right_container, 1)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self._top_row_layout = top_row
        self.url_label = QLabel("", right_container)
        self.url_label.setObjectName("batchEntryUrl")
        self.url_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.url_label.setContentsMargins(0, 0, 0, 0)
        self.url_label.setMinimumWidth(0)
        self.url_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.status_label = QLabel("Queued", right_container)
        self.status_label.setObjectName("batchEntryStatus")
        self.status_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.download_button = QPushButton("Download", right_container)
        self.download_button.setObjectName("batchEntryAction")
        self.download_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.remove_button = QPushButton("Remove", right_container)
        self.remove_button.setObjectName("batchEntryAction")
        self.remove_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.format_combo = ChevronComboBox(right_container)
        self.format_combo.setObjectName("batchEntryFormat")
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.format_combo.setMinimumContentsLength(0)
        self.format_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.quality_combo = ChevronComboBox(right_container)
        self.quality_combo.setObjectName("batchEntryQuality")
        self.quality_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.quality_combo.setMinimumContentsLength(0)
        self.quality_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        top_row.addWidget(self.url_label, 1, Qt.AlignVCenter)
        top_row.addWidget(self.status_label, 0, Qt.AlignRight)
        top_row.addWidget(self.download_button, 0)
        top_row.addWidget(self.remove_button, 0)
        right_layout.addLayout(top_row)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(6)
        self._detail_row_layout = detail_row
        self.detail_label = QLabel("", right_container)
        self.detail_label.setObjectName("muted")
        self.detail_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.detail_label.setContentsMargins(0, 0, 0, 0)
        self.detail_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        detail_row.addWidget(self.detail_label, 1)
        detail_row.addWidget(self.format_combo, 0, Qt.AlignRight)
        detail_row.addWidget(self.quality_combo, 0, Qt.AlignRight)
        right_layout.addLayout(detail_row)

        self.download_button.clicked.connect(self._on_primary_action_clicked)
        self.remove_button.clicked.connect(self._on_secondary_action_clicked)
        self.format_combo.currentTextChanged.connect(self._on_format_changed)
        self.quality_combo.currentTextChanged.connect(self._on_quality_changed)
        self.quality_combo.disabledClicked.connect(self._on_quality_disabled_clicked)
        self.url_label.installEventFilter(self)
        self.format_combo.installEventFilter(self)
        self.quality_combo.installEventFilter(self)
        self.download_button.installEventFilter(self)
        self.remove_button.installEventFilter(self)

        self._update_compact_layout()
        self._apply_control_cursors()

    @staticmethod
    def _scaled(value: int, scale: float, minimum: int = 1) -> int:
        normalized = _normalize_scale_factor(scale)
        computed = int(round(value * normalized))
        if normalized < 1.0:
            return max(1, computed)
        return max(minimum, computed)

    def set_ui_scale(self, scale: float) -> None:
        normalized = _normalize_scale_factor(scale)
        if abs(normalized - self._ui_scale) < 0.001:
            return
        self._ui_scale = normalized
        self._update_compact_layout()

    def _set_thumbnail_placeholder(self) -> None:
        self.thumbnail_label.clear()
        self.thumbnail_label.setText("THUMB\nNAIL")
        self.thumbnail_label.setToolTip(self._thumbnail_source_url)
        self._thumbnail_original = None

    def _apply_thumbnail_pixmap(self) -> None:
        if self._thumbnail_original is None:
            return
        target_size = self.thumbnail_label.size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            return
        rounded = rounded_pixmap(
            self._thumbnail_original,
            target_size,
            max(5, int(round(target_size.height() * 0.12))),
        )
        self.thumbnail_label.setPixmap(rounded)
        self.thumbnail_label.setText("")
        self.thumbnail_label.setToolTip(self._thumbnail_source_url)

    def set_thumbnail_bytes(self, image_data: bytes | None, source_url: str = "") -> None:
        url = str(source_url or "").strip()
        if url and url != self._thumbnail_source_url:
            self._thumbnail_source_url = url
            self._set_thumbnail_placeholder()
        elif url and self._thumbnail_original is not None and image_data:
            # Avoid re-decoding unchanged thumbnail payload for hot row updates.
            return
        if not image_data:
            self._set_thumbnail_placeholder()
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(bytes(image_data)):
            self._set_thumbnail_placeholder()
            return
        self._thumbnail_original = pixmap
        self._apply_thumbnail_pixmap()

    @staticmethod
    def _truncate(text: str, max_len: int = 86) -> str:
        raw = str(text or "").strip()
        if len(raw) <= max_len:
            return raw
        return f"{raw[: max_len - 1]}..."

    def set_busy(self, busy: bool) -> None:
        normalized = bool(busy)
        if normalized == self._busy:
            return
        self._busy = normalized
        self._apply_enabled_state()

    def _apply_enabled_state(self) -> None:
        locked = bool(self._busy)
        combos_enabled = (not locked) and self._show_format_quality
        self.format_combo.setEnabled(combos_enabled)
        self.quality_combo.setEnabled(combos_enabled)
        self.quality_combo.setProperty("_mc_block_popup", not self._quality_allowed)
        primary_enabled = self._can_download
        secondary_enabled = self._can_remove
        self.download_button.setEnabled(primary_enabled)
        self.remove_button.setEnabled(secondary_enabled)
        self._apply_control_cursors()

    @staticmethod
    def _set_cursor_for_control(widget: QWidget) -> None:
        set_widget_pointer_cursor(widget)

    def _apply_control_cursors(self) -> None:
        self._set_cursor_for_control(self.url_label)
        self._set_cursor_for_control(self.format_combo)
        self._set_cursor_for_control(self.quality_combo)
        self._set_cursor_for_control(self.download_button)
        self._set_cursor_for_control(self.remove_button)

    def eventFilter(self, watched, event):                
        try:
            if watched is self.url_label:
                if event.type() in {QEvent.Enter, QEvent.HoverEnter, QEvent.HoverMove}:
                    self.url_label.setProperty("hovered", True)
                    style = self.url_label.style()
                    style.unpolish(self.url_label)
                    style.polish(self.url_label)
                    self.url_label.update()
                    self._set_cursor_for_control(self.url_label)
                elif event.type() in {QEvent.Leave, QEvent.HoverLeave}:
                    self.url_label.setProperty("hovered", False)
                    style = self.url_label.style()
                    style.unpolish(self.url_label)
                    style.polish(self.url_label)
                    self.url_label.update()
                    self._set_cursor_for_control(self.url_label)
                elif event.type() == QEvent.MouseButtonRelease:
                    text = str(self._full_url_text or "").strip()
                    if text:
                        QApplication.clipboard().setText(text)
                        QToolTip.showText(QCursor.pos(), "Link copied to clipboard.", self.url_label)
                        event.accept()
                        return True
            if watched in {self.format_combo, self.quality_combo, self.download_button, self.remove_button}:
                if event.type() in {
                    QEvent.EnabledChange,
                    QEvent.Show,
                    QEvent.Hide,
                    QEvent.Enter,
                    QEvent.HoverEnter,
                    QEvent.HoverMove,
                    QEvent.StyleChange,
                    QEvent.Polish,
                }:
                    self._set_cursor_for_control(watched)
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False

    def _update_url_elide(self) -> None:
        source_text = str(self._full_url_text or "").strip()
        if not source_text:
            self.url_label.setText("")
            self.url_label.setToolTip("")
            return
        available = self._url_text_available_width()
        metrics = QFontMetrics(self.url_label.font())
        elided = metrics.elidedText(source_text, Qt.ElideRight, available)
        self.url_label.setText(elided)
        self.url_label.setToolTip("")

    def _url_text_available_width(self) -> int:
        spacing = max(0, self._top_row_layout.spacing())
        status_w = max(self.status_label.width(), self.status_label.sizeHint().width())
        download_w = max(self.download_button.width(), self.download_button.sizeHint().width())
        remove_w = max(self.remove_button.width(), self.remove_button.sizeHint().width())
        right_controls = status_w + download_w + remove_w + (spacing * 3)
        top_row_width = self._top_row_layout.geometry().width()
        if top_row_width <= right_controls:
            margins = self._right_layout.contentsMargins()
            top_row_width = max(
                top_row_width,
                self._right_container.width() - margins.left() - margins.right(),
            )
        if top_row_width <= right_controls:
            root_margins = self._root_layout.contentsMargins()
            top_row_width = max(
                top_row_width,
                self.width()
                - self.thumbnail_label.width()
                - self._root_layout.spacing()
                - root_margins.left()
                - root_margins.right(),
            )
        if top_row_width > right_controls:
            available = top_row_width - right_controls - 2
        else:
            available = max(self.url_label.width(), self.url_label.sizeHint().width())
        available -= max(0, int(self._url_elide_extra_px))
        return max(40, available)

    def _update_detail_elide(self) -> None:
        source_text = str(self._full_detail_text or "").strip()
        if not source_text:
            self.detail_label.setText("")
            self.detail_label.setToolTip("")
            return
        available = self._detail_text_available_width()
        metrics = QFontMetrics(self.detail_label.font())
        elided = metrics.elidedText(source_text, Qt.ElideRight, available)
        self.detail_label.setText(elided)
        self.detail_label.setToolTip(source_text if elided != source_text else "")

    def _detail_text_available_width(self) -> int:
        spacing = max(0, self._detail_row_layout.spacing())
        format_w = max(self.format_combo.width(), self.format_combo.sizeHint().width())
        quality_w = max(self.quality_combo.width(), self.quality_combo.sizeHint().width())
        right_controls = format_w + quality_w + (spacing * 2)
        detail_row_width = self._detail_row_layout.geometry().width()
        if detail_row_width <= right_controls:
            margins = self._right_layout.contentsMargins()
            detail_row_width = max(
                detail_row_width,
                self._right_container.width() - margins.left() - margins.right(),
            )
        if detail_row_width > right_controls:
            available = detail_row_width - right_controls - 2
        else:
            available = max(self.detail_label.width(), self.detail_label.sizeHint().width())
        return max(48, available)

    def _update_text_elide(self) -> None:
        self._update_url_elide()
        self._update_detail_elide()

    def _apply_compact_control_metrics(self) -> None:
        scale = self._ui_scale
        status_width = self._scaled(108, scale, 64)
        download_min = self._scaled(110, scale, 68)
        download_max = self._scaled(130, scale, 82)
        remove_min = self._scaled(100, scale, 64)
        remove_max = self._scaled(120, scale, 78)
        self.status_label.setMinimumWidth(status_width)
        self.status_label.setMaximumWidth(status_width)
        self.download_button.setMinimumWidth(download_min)
        self.download_button.setMaximumWidth(download_max)
        self.remove_button.setMinimumWidth(remove_min)
        self.remove_button.setMaximumWidth(remove_max)

        button_h = max(self._scaled(24, scale, 16), int(self.download_button.sizeHint().height()))
        self.download_button.setFixedHeight(button_h)
        self.remove_button.setFixedHeight(button_h)
        status_h = max(self._scaled(22, scale, 14), button_h - self._scaled(2, scale, 1))
        self.status_label.setFixedHeight(status_h)

    def _update_compact_layout(self) -> None:
        scale = self._ui_scale
        self.setMinimumWidth(self._scaled(760, scale, 420))
        self._root_base_margins = (
            self._scaled(8, scale, 4),
            self._scaled(7, scale, 3),
            self._scaled(8, scale, 4),
            self._scaled(7, scale, 3),
        )
        self._root_layout.setSpacing(self._scaled(8, scale, 4))
        self._right_layout.setSpacing(self._scaled(4, scale, 2))
        self._top_row_layout.setSpacing(self._scaled(6, scale, 3))
        self._detail_row_layout.setSpacing(self._scaled(6, scale, 3))
        thumb_size = self._scaled(74, scale, 46)
        self.thumbnail_label.setFixedWidth(thumb_size)
        self.thumbnail_label.setFixedHeight(thumb_size)
        control_width = self._scaled(146, scale, 84)
        self.format_combo.setMinimumWidth(control_width)
        self.format_combo.setMaximumWidth(control_width)
        self.quality_combo.setMinimumWidth(control_width)
        self.quality_combo.setMaximumWidth(control_width)
        self._url_elide_extra_px_default = self._scaled(0, scale, 0)
        self._url_elide_extra_px_compact = self._scaled(0, scale, 0)
        self._url_elide_extra_px = self._url_elide_extra_px_compact if self._settings_compact_mode else self._url_elide_extra_px_default
        self._root_layout.setContentsMargins(*self._root_base_margins)
        self._apply_duplicate_margin()
        self._apply_compact_control_metrics()
        self._refresh_action_button_texts()
        self._update_text_elide()
        self._apply_thumbnail_pixmap()
        self._apply_enabled_state()
        self._schedule_deferred_elide_refresh()

    def set_format_quality_visible(self, visible: bool) -> None:
        normalized = bool(visible)
        if normalized == self._show_format_quality:
            return
        self._show_format_quality = normalized
        self.format_combo.setVisible(self._show_format_quality)
        self.quality_combo.setVisible(self._show_format_quality)
        self._update_compact_layout()

    def set_detail_visible(self, visible: bool) -> None:
        normalized = bool(visible)
        if normalized == self._show_detail:
            return
        self._show_detail = normalized
        self.detail_label.setVisible(self._show_detail)
        self._update_compact_layout()

    def set_settings_compact_mode(self, compact: bool) -> None:
        normalized = bool(compact)
        if normalized == self._settings_compact_mode:
            return
        self._settings_compact_mode = normalized
        self._url_elide_extra_px = self._url_elide_extra_px_compact if normalized else self._url_elide_extra_px_default
        self._update_compact_layout()

    def entry_id(self) -> str:
        return str(self._entry_id)

    def thumbnail_source_url(self) -> str:
        return str(self._thumbnail_source_url or "")

    def set_duplicate_visual(self, enabled: bool) -> None:
        is_duplicate = bool(enabled)
        if self._is_duplicate_visual == is_duplicate:
            return
        self._is_duplicate_visual = is_duplicate
        self._apply_duplicate_margin()
        self.setProperty("duplicateRow", is_duplicate)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()
        self._schedule_deferred_elide_refresh()

    def _apply_duplicate_margin(self) -> None:
        left, top, right, bottom = self._root_base_margins
        indent = self._scaled(18, self._ui_scale, 10) if self._is_duplicate_visual else 0
        self._root_layout.setContentsMargins(left + indent, top, right, bottom)

    def _refresh_action_button_texts(self) -> None:
        action = str(self._primary_action or "download").strip().lower()
        if action == "pause":
            self.download_button.setText("Pause")
        elif action == "resume":
            self.download_button.setText("Resume")
        else:
            download_retry = self.download_button.text().strip().lower() == "retry"
            self.download_button.setText("Retry" if download_retry else "Download")
        self.remove_button.setText("Remove")

    @staticmethod
    def _entry_signature(entry: BatchEntry) -> tuple[object, ...]:
        return build_batch_entry_view_state(entry).signature

    def set_entry(self, entry: BatchEntry) -> None:
        view = build_batch_entry_view_state(entry)
        signature = view.signature
        if signature == self._last_entry_signature:
            return
        self._last_entry_signature = signature
        self._entry_id = view.entry_id
        self._full_url_text = view.full_url_text
        next_thumb_url = view.thumbnail_url
        if next_thumb_url != self._thumbnail_source_url:
            self._thumbnail_source_url = next_thumb_url
            self._set_thumbnail_placeholder()
        if self.status_label.text() != view.status_label:
            self.status_label.setText(view.status_label)
        if view.status_state != self._last_status_state:
            self._last_status_state = view.status_state
            self.status_label.setProperty("state", view.status_state)
            status_style = self.status_label.style()
            status_style.unpolish(self.status_label)
            status_style.polish(self.status_label)
            self.status_label.update()
        if view.url_state != self._last_url_state:
            self._last_url_state = view.url_state
            self.url_label.setProperty("state", view.url_state)
            self.url_label.setProperty("hovered", False)
            url_style = self.url_label.style()
            url_style.unpolish(self.url_label)
            url_style.polish(self.url_label)
            self.url_label.update()
        if view.formats != self._last_formats:
            self._last_formats = view.formats
            self.format_combo.blockSignals(True)
            self.format_combo.clear()
            self.format_combo.addItems(list(view.formats))
            self.format_combo.setCurrentText(view.selected_format)
            self.format_combo.blockSignals(False)
        elif self.format_combo.currentText() != view.selected_format:
            self.format_combo.blockSignals(True)
            self.format_combo.setCurrentText(view.selected_format)
            self.format_combo.blockSignals(False)
        if view.qualities != self._last_qualities:
            self._last_qualities = view.qualities
            self.quality_combo.blockSignals(True)
            self.quality_combo.clear()
            self.quality_combo.addItems(list(view.qualities))
            self.quality_combo.setCurrentText(view.selected_quality)
            self.quality_combo.blockSignals(False)
        elif self.quality_combo.currentText() != view.selected_quality:
            self.quality_combo.blockSignals(True)
            self.quality_combo.setCurrentText(view.selected_quality)
            self.quality_combo.blockSignals(False)
        self._quality_allowed = bool(view.quality_allowed)

        self._full_detail_text = view.detail_text

        self._can_download = bool(view.can_download)
        self._can_remove = bool(view.can_remove)
        self._primary_action = view.primary_action
        self.download_button.setText(view.primary_button_text)
        self.remove_button.setText("Remove")
        self._refresh_action_button_texts()
        self._update_text_elide()
        self._apply_enabled_state()

    def _schedule_deferred_elide_refresh(self) -> None:
        self._update_text_elide()
        self._deferred_elide_timer.start()

    def _on_format_changed(self, value: str) -> None:
        selected = str(value or "").strip().upper()
        if is_audio_format_choice(selected):
            self.quality_combo.blockSignals(True)
            if self.quality_combo.findText("BEST QUALITY") >= 0:
                self.quality_combo.setCurrentText("BEST QUALITY")
            self.quality_combo.blockSignals(False)
            self._quality_allowed = False
        else:
            self._quality_allowed = True
        self._apply_enabled_state()
        self.formatChanged.emit(self._entry_id, selected)

    def _on_quality_changed(self, value: str) -> None:
        self.qualityChanged.emit(self._entry_id, str(value or "").strip().upper())

    def _on_quality_disabled_clicked(self) -> None:
        self.qualityUnavailableRequested.emit(self._entry_id)

    def _on_primary_action_clicked(self) -> None:
        if not self.download_button.isEnabled():
            return
        action = str(self._primary_action or "download").strip().lower()
        if action == "pause":
            self.pauseRequested.emit(self._entry_id)
            return
        if action == "resume":
            self.resumeRequested.emit(self._entry_id)
            return
        self.downloadRequested.emit(self._entry_id)

    def _on_secondary_action_clicked(self) -> None:
        if not self.remove_button.isEnabled():
            return
        self.removeRequested.emit(self._entry_id)

    def resizeEvent(self, event) -> None:                
        super().resizeEvent(event)
        self._update_text_elide()
        self._apply_thumbnail_pixmap()

    def showEvent(self, event) -> None:                
        super().showEvent(event)
        self._schedule_deferred_elide_refresh()

