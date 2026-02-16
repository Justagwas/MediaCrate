from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QListView,
    QProxyStyle,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionSlider,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QWidget,
)

class ComboPopupDelegate(QStyledItemDelegate):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        palette = parent.palette() if isinstance(parent, QWidget) else QApplication.palette()
        self._accent = QColor("#D20F39")
        self._text_color = QColor(palette.color(QPalette.Text))
        self._panel_bg = QColor(palette.color(QPalette.Base))
        hover_candidate = QColor(palette.color(QPalette.AlternateBase))
        self._hover_bg = hover_candidate if hover_candidate.isValid() else QColor(self._panel_bg).darker(108)
        self._selected_bg = QColor(self._accent)
        self._selected_bg.setAlpha(42)
        self._elide_text = True
        self._extra_width_px = 0

    def set_colors(self, *, accent: str, text: str, panel: str, hover: str) -> None:
        self._accent = QColor(accent)
        self._text_color = QColor(text)
        self._panel_bg = QColor(panel)
        self._hover_bg = QColor(hover)
        self._selected_bg = QColor(accent)
        self._selected_bg.setAlpha(42)

    def set_elide_text(self, enabled: bool) -> None:
        self._elide_text = bool(enabled)

    def set_extra_width(self, pixels: int) -> None:
        self._extra_width_px = max(0, int(pixels))

    def paint(self, painter, option, index) -> None:                
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        rect = opt.rect
        if not rect.isValid():
            return

        state = opt.state
        is_selected = bool(state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        is_enabled = bool(state & QStyle.StateFlag.State_Enabled)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        if is_selected:
            painter.setBrush(self._selected_bg)
        elif is_hovered:
            painter.setBrush(self._hover_bg)
        else:
            painter.setBrush(self._panel_bg)
        painter.drawRect(rect)

        left_pad = max(2, int(round(rect.height() * 0.25)))
        if is_selected:
            marker_width = max(3, int(round(rect.height() * 0.10)))
            marker_inset = max(2, int(round(rect.height() * 0.24)))
            marker_rect = QRect(
                int(rect.left() + 2),
                int(rect.top() + max(1, int(round(rect.height() * 0.12)))),
                marker_width,
                max(2, int(rect.height() - (marker_inset * 2))),
            )
            painter.setBrush(self._accent)
            painter.drawRoundedRect(QRectF(marker_rect), marker_width / 2.0, marker_width / 2.0)
            left_pad += marker_width + max(2, int(round(rect.height() * 0.14)))

        text_rect = rect.adjusted(left_pad, 0, -max(2, int(round(rect.height() * 0.16))), 0)
        text_color = QColor(self._text_color)
        if not is_enabled:
            text_color.setAlpha(145)
        painter.setPen(text_color)
        font = opt.font
        font.setBold(True)
        painter.setFont(font)
        raw_text = str(opt.text or "")
        if self._elide_text:
            text = opt.fontMetrics.elidedText(raw_text, Qt.TextElideMode.ElideRight, max(1, text_rect.width()))
        else:
            text = raw_text
        painter.drawText(text_rect, int(Qt.AlignVCenter | Qt.AlignLeft), text)
        painter.restore()

    def sizeHint(self, option, index):                
        hint = super().sizeHint(option, index)
        hint.setHeight(max(1, hint.height()))
        if self._extra_width_px > 0:
            hint.setWidth(max(1, int(hint.width()) + self._extra_width_px))
        return hint


class ChevronComboBox(QComboBox):
    popupAboutToShow = Signal()
    disabledClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        palette = self.palette()
        self._arrow_idle = QColor(palette.color(QPalette.Mid))
        self._arrow_active = QColor(palette.color(QPalette.Text))
        self._popup_delegate = ComboPopupDelegate(self)
        popup_view = QListView(self)
        popup_view.setUniformItemSizes(True)
        popup_view.setAutoScroll(False)
        popup_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        popup_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        popup_view.setWordWrap(False)
        popup_view.setTextElideMode(Qt.TextElideMode.ElideRight)
        popup_view.setItemDelegate(self._popup_delegate)
        popup_view.viewport().setAutoFillBackground(True)
        self.setView(popup_view)
        self._popup_horizontal_scroll_enabled = False

    def is_popup_visible(self) -> bool:
        popup_view = self.view()
        return bool(popup_view and popup_view.isVisible())

    def set_popup_horizontal_scroll_enabled(self, enabled: bool) -> None:
        popup_view = self.view()
        if popup_view is None:
            return
        allow_scroll = bool(enabled)
        self._popup_horizontal_scroll_enabled = allow_scroll
        popup_view.setUniformItemSizes(not allow_scroll)
        self._popup_delegate.set_extra_width(64 if allow_scroll else 0)
        hbar = popup_view.horizontalScrollBar()
        hbar.setSingleStep(12 if allow_scroll else 20)
        hbar.setPageStep(56 if allow_scroll else 80)
        vbar = popup_view.verticalScrollBar()
        vbar.setSingleStep(14 if allow_scroll else 20)
        vbar.setPageStep(56 if allow_scroll else 80)
        popup_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if allow_scroll else Qt.ScrollBarAlwaysOff
        )
        popup_view.setTextElideMode(
            Qt.TextElideMode.ElideNone if allow_scroll else Qt.TextElideMode.ElideRight
        )
        self._popup_delegate.set_elide_text(not allow_scroll)
        popup_view.doItemsLayout()
        popup_view.updateGeometries()
        popup_view.update()

    def set_arrow_colors(self, idle: str, active: str) -> None:
        self._arrow_idle = QColor(idle)
        self._arrow_active = QColor(active)
        self.update()

    def set_popup_colors(self, *, accent: str, text: str, panel: str, hover: str) -> None:
        self._popup_delegate.set_colors(accent=accent, text=text, panel=panel, hover=hover)
        view = self.view()
        if view is not None:
            palette = view.palette()
            palette.setColor(QPalette.Base, QColor(panel))
            palette.setColor(QPalette.Text, QColor(text))
            palette.setColor(QPalette.Highlight, QColor(accent))
            palette.setColor(QPalette.HighlightedText, QColor(text))
            view.setPalette(palette)
            if view.viewport() is not None:
                view.viewport().setPalette(palette)
                view.viewport().update()

    def paintEvent(self, event) -> None:                
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        view = self.view()
        is_open = bool(view and view.isVisible())
        color = self._arrow_active if (self.hasFocus() or is_open) else self._arrow_idle
        painter.setPen(
            QPen(
                color,
                max(1, int(round(self.height() / 12))),
                Qt.SolidLine,
                Qt.RoundCap,
                Qt.RoundJoin,
            )
        )

        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxArrow,
            self,
        )
        if not arrow_rect.isValid():
            return

        cx = arrow_rect.center().x()
        cy = arrow_rect.center().y()
        span = max(3, int(round(min(arrow_rect.width(), arrow_rect.height()) * 0.22)))
        if is_open:
            painter.drawLine(cx - span, cy + (span // 2), cx, cy - (span // 2))
            painter.drawLine(cx, cy - (span // 2), cx + span, cy + (span // 2))
        else:
            painter.drawLine(cx - span, cy - (span // 2), cx, cy + (span // 2))
            painter.drawLine(cx, cy + (span // 2), cx + span, cy - (span // 2))

    def showPopup(self) -> None:
        if bool(self.property("_mc_block_popup")):
            self.disabledClicked.emit()
            return
        popup_view = self.view()
        if popup_view is not None:
            if self._popup_horizontal_scroll_enabled:
                self._enforce_popup_width_lock(popup_view)
            else:
                popup_view.setMinimumWidth(0)
                popup_view.setMaximumWidth(16777215)
        self.popupAboutToShow.emit()
        super().showPopup()
        if popup_view is not None and self._popup_horizontal_scroll_enabled:
            self._enforce_popup_width_lock(popup_view)
            QTimer.singleShot(0, lambda view=popup_view: self._enforce_popup_width_lock(view))

    def _enforce_popup_width_lock(self, popup_view: QListView) -> None:
        target_width = max(1, int(self.width()))
        popup_view.setMinimumWidth(target_width)
        popup_view.setMaximumWidth(target_width)
        container = popup_view.window()
        if container is not None:
            try:
                container.setMinimumWidth(target_width)
                container.setMaximumWidth(target_width)
                container.resize(target_width, container.height())
            except RuntimeError:
                return

    def mousePressEvent(self, event) -> None:                
        if not self.isEnabled():
            self.disabledClicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:                
                                                            
        event.ignore()


class RoundHandleSliderStyle(QProxyStyle):
    def __init__(
        self,
        *,
        handle_color: str,
        border_color: str,
        groove_color: str,
        fill_color: str,
        handle_size: int = 18,
        groove_height: int = 6,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent.style() if parent is not None else None)
        self._handle_color = QColor(handle_color)
        self._border_color = QColor(border_color)
        self._groove_color = QColor(groove_color)
        self._fill_color = QColor(fill_color)
        self._handle_size = max(12, int(handle_size))
        self._groove_height = max(4, int(groove_height))

    def set_colors(
        self,
        *,
        handle_color: str,
        border_color: str,
        groove_color: str,
        fill_color: str,
    ) -> None:
        self._handle_color = QColor(handle_color)
        self._border_color = QColor(border_color)
        self._groove_color = QColor(groove_color)
        self._fill_color = QColor(fill_color)

    def set_metrics(self, *, handle_size: int, groove_height: int) -> None:
        self._handle_size = max(12, int(handle_size))
        self._groove_height = max(4, int(groove_height))

    def pixelMetric(self, metric, option=None, widget=None):                
        if metric == QStyle.PixelMetric.PM_SliderLength:
            return self._handle_size
        if metric == QStyle.PixelMetric.PM_SliderThickness:
            return max(self._handle_size + 6, self._groove_height + 10)
        return super().pixelMetric(metric, option, widget)

    def _groove_rect(self, option: QStyleOptionSlider) -> QRect:
        diameter = self._handle_size
        groove_h = self._groove_height
        inset = max(1, diameter // 2)
        width = max(2, int(option.rect.width()) - inset * 2)
        x = int(option.rect.left()) + inset
        y = int(option.rect.center().y() - groove_h // 2)
        return QRect(x, y, width, groove_h)

    def _handle_rect(self, option: QStyleOptionSlider) -> QRect:
        groove = self._groove_rect(option)
        diameter = self._handle_size
        available = max(0, groove.width() - diameter)
        pos = QStyle.sliderPositionFromValue(
            int(option.minimum),
            int(option.maximum),
            int(option.sliderPosition),
            int(available),
            bool(option.upsideDown),
        )
        x = int(groove.left()) + int(pos)
        y = int(groove.center().y() - diameter // 2)
        return QRect(x, y, diameter, diameter)

    def subControlRect(self, control, option, sub_control, widget=None):                
        if control == QStyle.ComplexControl.CC_Slider and isinstance(option, QStyleOptionSlider):
            if option.orientation == Qt.Horizontal:
                if sub_control == QStyle.SubControl.SC_SliderGroove:
                    return self._groove_rect(option)
                if sub_control == QStyle.SubControl.SC_SliderHandle:
                    return self._handle_rect(option)
        return super().subControlRect(control, option, sub_control, widget)

    def drawComplexControl(self, control, option, painter, widget=None):                
        if control != QStyle.ComplexControl.CC_Slider or not isinstance(option, QStyleOptionSlider):
            super().drawComplexControl(control, option, painter, widget)
            return
        if option.orientation != Qt.Horizontal:
            super().drawComplexControl(control, option, painter, widget)
            return

        groove = self._groove_rect(option)
        handle = self._handle_rect(option)
        radius = max(2.0, groove.height() / 2.0)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        groove_color = QColor(self._groove_color)
        fill_color = QColor(self._fill_color)
        handle_color = QColor(self._handle_color)
        border_color = QColor(self._border_color)
        if not enabled:
            groove_color.setAlpha(125)
            fill_color = QColor(self._groove_color).lighter(112)
            fill_color.setAlpha(165)
            handle_color = QColor(self._border_color).lighter(128)
            handle_color.setAlpha(185)
            border_color.setAlpha(165)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(groove_color)
        painter.drawRoundedRect(QRectF(groove), radius, radius)

        if handle.isValid():
            if option.upsideDown:
                fill_left = float(handle.center().x())
                fill_width = float(groove.right() - handle.center().x())
            else:
                fill_left = float(groove.left())
                fill_width = float(handle.center().x() - groove.left())
            if fill_width > 0:
                fill_rect = QRectF(fill_left, float(groove.top()), fill_width, float(groove.height()))
                painter.setBrush(fill_color)
                painter.drawRoundedRect(fill_rect, radius, radius)

        if handle.isValid():
            circle_rect = QRectF(handle)
            painter.setBrush(handle_color)
            painter.setPen(QPen(border_color, max(1, int(round(self._handle_size / 16)))))
            painter.drawEllipse(circle_rect)
        painter.restore()


class SquareCheckBoxStyle(QProxyStyle):
    def __init__(
        self,
        *,
        border_color: str,
        fill_color: str,
        check_color: str,
        size: int = 16,
        radius: int = 4,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent.style() if parent is not None else None)
        self._border_color = QColor(border_color)
        self._fill_color = QColor(fill_color)
        self._check_color = QColor(check_color)
        self._size = max(12, int(size))
        self._radius = max(2, int(radius))

    def set_colors(self, *, border_color: str, fill_color: str, check_color: str) -> None:
        self._border_color = QColor(border_color)
        self._fill_color = QColor(fill_color)
        self._check_color = QColor(check_color)

    def set_metrics(self, *, size: int, radius: int) -> None:
        self._size = max(12, int(size))
        self._radius = max(2, int(radius))

    def pixelMetric(self, metric, option=None, widget=None):                
        if metric in {QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight}:
            return self._size
        return super().pixelMetric(metric, option, widget)

    def _indicator_rect(self, option: QStyleOptionButton) -> QRect:
        left = int(option.rect.left()) + 2
        top = int(option.rect.center().y() - self._size // 2)
        return QRect(left, top, self._size, self._size)

    def subElementRect(self, element, option, widget=None):                
        if isinstance(option, QStyleOptionButton):
            if element == QStyle.SubElement.SE_CheckBoxIndicator:
                return self._indicator_rect(option)
            if element == QStyle.SubElement.SE_CheckBoxContents:
                indicator = self._indicator_rect(option)
                gap = 6
                left = int(indicator.right() + 1 + gap)
                width = max(0, int(option.rect.right()) - left + 1)
                return QRect(left, int(option.rect.top()), width, int(option.rect.height()))
        return super().subElementRect(element, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):                
        if element != QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return

        rect = option.rect.adjusted(1, 1, -2, -2)
        checked = bool(option.state & QStyle.StateFlag.State_On)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        border = QColor(self._border_color)
        fill = QColor(self._fill_color if checked else "transparent")
        check = QColor(self._check_color)
        if not enabled:
            border.setAlpha(130)
            fill.setAlpha(110 if checked else 0)
            check.setAlpha(170)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(rect), float(self._radius), float(self._radius))
        if checked:
            pen_w = max(2, int(round(self._size / 9)))
            pen = QPen(check, pen_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            x = float(rect.x())
            y = float(rect.y())
            w = float(rect.width())
            h = float(rect.height())
            p1 = QPointF(x + w * 0.24, y + h * 0.56)
            p2 = QPointF(x + w * 0.44, y + h * 0.74)
            p3 = QPointF(x + w * 0.78, y + h * 0.34)
            painter.drawLine(p1, p2)
            painter.drawLine(p2, p3)
        painter.restore()

