from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemePalette:
    mode: str
    app_bg: str
    panel_bg: str
    border: str
    text_primary: str
    text_secondary: str
    accent: str
    accent_hover: str
    danger: str
    danger_hover: str
    success: str
    disabled_bg: str
    disabled_fg: str


DARK_THEME = ThemePalette(
    mode="dark",
    app_bg="#0A0A0B",
    panel_bg="#141416",
    border="#2A2A2D",
    text_primary="#F4F4F5",
    text_secondary="#B7B7BC",
    accent="#D20F39",
    accent_hover="#F03A5F",
    danger="#C51E3A",
    danger_hover="#D94A63",
    success="#22C55E",
    disabled_bg="#202024",
    disabled_fg="#8C8C93",
)

LIGHT_THEME = ThemePalette(
    mode="light",
    app_bg="#ECEDEF",
    panel_bg="#FAFAFB",
    border="#D1D3D8",
    text_primary="#1B1F2A",
    text_secondary="#4B5161",
    accent="#C51E3A",
    accent_hover="#D94A63",
    danger="#B71C38",
    danger_hover="#CD4A63",
    success="#1E9A4B",
    disabled_bg="#E6E8ED",
    disabled_fg="#7A8090",
)


def get_theme(mode: str | None) -> ThemePalette:
    if str(mode or "").strip().lower() == "light":
        return LIGHT_THEME
    return DARK_THEME


def _scaled(value: float, scale: float, minimum: int = 1) -> int:
    scaled = int(round(value * scale))
    if scale < 1.0:
        return max(1, scaled)
    return max(minimum, scaled)


def _scaled_pt(value: float, scale: float, minimum: float = 7.0) -> float:
    scaled = round(value * scale, 1)
    if scale < 1.0:
        return max(1.0, scaled)
    return max(minimum, scaled)


def _build_stylesheet_metrics(theme: ThemePalette, ui_scale: float) -> dict[str, float | int | str]:
    scale = max(0.1, min(8.0, float(ui_scale)))
    return {
        "scale": scale,
        "frame_radius": _scaled(8, scale, 4),
        "button_radius": _scaled(6, scale, 3),
        "widget_font": _scaled_pt(9.7, scale, 7.8),
        "title_font": _scaled_pt(10.8, scale, 8.2),
        "subtitle_font": _scaled_pt(8.2, scale, 6.9),
        "button_font": _scaled_pt(9.1, scale, 7.2),
        "cta_button_font": _scaled_pt(10.4, scale, 8.4),
        "footer_font": _scaled_pt(9.4, scale, 7.4),
        "settings_button_font": _scaled_pt(9.4, scale, 7.4),
        "card_title_font": _scaled_pt(9.2, scale, 7.2),
        "url_input_font": _scaled_pt(10.6, scale, 8.5),
        "cta_height": _scaled(34, scale, 20),
        "input_height": _scaled(28, scale, 16),
        "combo_drop_width": _scaled(24, scale, 14),
        "icon_box": _scaled(20, scale, 16),
        "progress_h": _scaled(26, scale, 18),
        "downloading_color": "#38BDF8" if theme.mode == "dark" else "#0EA5E9",
        "duplicate_color": "#E7A33E" if theme.mode == "dark" else "#B96C13",
        "settings_subtext_pad": _scaled(1, scale, 1),
        "button_pad_y": _scaled(2, scale, 2),
        "button_pad_x": _scaled(7, scale, 4),
        "stop_min_width": _scaled(64, scale, 44),
        "settings_action_height": _scaled(32, scale, 24),
        "mode_button_min_height": _scaled(30, scale, 24),
        "mode_button_pad_y": _scaled(4, scale, 2),
        "mode_button_pad_x": _scaled(10, scale, 6),
        "combo_pad_right": _scaled(24, scale, 18),
        "scroll_padding": _scaled(2, scale, 1),
        "single_meta_title_font": _scaled_pt(9.5, scale, 7.5),
        "status_radius": _scaled(4, scale, 2),
        "status_pad_x": _scaled(7, scale, 4),
        "entry_status_pad_y": _scaled(2, scale, 1),
        "entry_status_pad_x": _scaled(6, scale, 3),
        "entry_action_min_h": _scaled(26, scale, 18),
        "entry_combo_min_h": _scaled(24, scale, 16),
        "checkbox_spacing": _scaled(8, scale, 5),
        "progress_font": _scaled_pt(8.8, scale, 6.4),
    }


def _build_stylesheet_section_base(theme: ThemePalette, m: dict[str, float | int | str]) -> str:
    return f"""
QMainWindow {{
    background: {theme.app_bg};
}}
QWidget#mcRoot, QWidget#mainColumn {{
    background: {theme.app_bg};
}}
QFrame#card {{
    background: {theme.panel_bg};
    border: 1px solid {theme.border};
    border-radius: {m['frame_radius']}px;
}}
QFrame#settingsPanel, QFrame#settingsCard, QFrame#modeHolder {{
    background: {theme.panel_bg};
    border: 2px solid {theme.border};
    border-radius: {m['frame_radius']}px;
}}
QScrollArea#settingsScroll {{
    background: transparent;
    border: none;
}}
QScrollArea#settingsScroll QWidget#qt_scrollarea_viewport {{
    background: transparent;
    border: none;
}}
QWidget#settingsBody {{
    background: transparent;
}}
QLabel#settingsCardTitle {{
    color: {theme.text_primary};
    font: 700 {m['card_title_font']:.1f}pt "Segoe UI";
}}
QLabel {{
    color: {theme.text_primary};
    background: transparent;
    font-family: "Segoe UI";
    font-size: {m['widget_font']:.1f}pt;
}}
QLabel#title {{
    font: 700 {m['title_font']:.1f}pt "Segoe UI";
}}
QLabel#subtitle, QLabel#muted {{
    color: {theme.text_secondary};
    font: 600 {m['subtitle_font']:.1f}pt "Segoe UI";
}}
QLabel#footerVersion {{
    color: {theme.text_secondary};
    font: 650 {m['footer_font']:.1f}pt "Segoe UI";
}}
QLabel#settingsSubtext {{
    color: {theme.text_secondary};
    font: 650 {m['button_font']:.1f}pt "Segoe UI";
    padding-top: {m['settings_subtext_pad']}px;
    padding-bottom: {m['settings_subtext_pad']}px;
}}
QLabel#inputFieldLabel {{
    font: 700 {m['widget_font']:.1f}pt "Segoe UI";
}}
QPushButton {{
    background: {theme.panel_bg};
    color: {theme.text_primary};
    border: 1px solid {theme.border};
    border-radius: {m['button_radius']}px;
    padding: {m['button_pad_y']}px {m['button_pad_x']}px;
    font: 600 {m['button_font']:.1f}pt "Segoe UI";
}}
QPushButton:hover {{
    background: {theme.accent};
}}
QPushButton:disabled {{
    background: {theme.disabled_bg};
    color: {theme.disabled_fg};
    border-color: {theme.border};
}}
QPushButton#downloadButton {{
    min-height: {m['cta_height']}px;
    font: 700 {m['cta_button_font']:.1f}pt "Segoe UI";
}}
QPushButton#stopButton {{
    background: {theme.danger};
    border: 1px solid {theme.danger};
    min-height: {m['cta_height']}px;
    min-width: {m['stop_min_width']}px;
    font: 700 {m['cta_button_font']:.1f}pt "Segoe UI";
}}
QPushButton#stopButton:hover {{
    background: {theme.danger_hover};
    border-color: {theme.danger_hover};
}}
QPushButton#pasteButton {{
    min-height: {m['input_height']}px;
    font: 700 {m['button_font']:.1f}pt "Segoe UI";
}}
QFrame#settingsPanel QPushButton#settingsActionButton {{
    min-height: {m['settings_action_height']}px;
    font: 700 {m['settings_button_font']:.1f}pt "Segoe UI";
}}
QFrame#settingsPanel QPushButton#modeButton {{
    min-height: {m['mode_button_min_height']}px;
}}
QPushButton#footerLink {{
    background: transparent;
    color: {theme.accent};
    border: none;
    padding: 2px;
    font: 700 {m['footer_font']:.1f}pt "Segoe UI";
    text-align: left;
}}
QPushButton#footerLink:hover {{
    color: {theme.text_primary};
    background: transparent;
}}
QPushButton#footerIcon {{
    background: transparent;
    color: {theme.text_primary};
    border: none;
    min-width: {m['icon_box']}px;
    min-height: {m['icon_box']}px;
    max-width: {m['icon_box']}px;
    max-height: {m['icon_box']}px;
    padding: 0;
    margin: 0;
}}
QPushButton#footerIcon:hover {{
    background: transparent;
}}
QPushButton#modeButton {{
    background: transparent;
    color: {theme.text_secondary};
    border: none;
    border-radius: {m['button_radius']}px;
    padding: {m['mode_button_pad_y']}px {m['mode_button_pad_x']}px;
    font: 600 {m['button_font']:.1f}pt "Segoe UI";
}}
QPushButton#modeButton:hover {{
    color: {theme.text_primary};
    background: {theme.panel_bg};
}}
QPushButton#modeButton:checked {{
    color: {theme.text_primary};
    background: {theme.accent};
}}
"""


def _build_stylesheet_section_inputs_and_rows(theme: ThemePalette, m: dict[str, float | int | str]) -> str:
    return f"""
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background: {theme.app_bg};
    color: {theme.text_primary};
    border: 1px solid {theme.border};
    border-radius: {m['button_radius']}px;
    min-height: {m['input_height']}px;
    padding: {m['button_pad_y']}px {m['button_pad_x']}px;
    font: 600 {m['button_font']:.1f}pt "Segoe UI";
    selection-background-color: {theme.accent};
}}
QLineEdit#singleUrlInput {{
    font: 600 {m['url_input_font']:.1f}pt "Segoe UI";
}}
QLineEdit#singleUrlInput[validating="true"] {{
    color: {theme.text_secondary};
    background: {theme.disabled_bg};
    border-color: {theme.border};
}}
QPlainTextEdit#batchUrlInput {{
    font: 600 {m['url_input_font']:.1f}pt "Segoe UI";
}}
QComboBox {{
    padding-right: {m['combo_pad_right']}px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: {m['combo_drop_width']}px;
    border: none;
    border-left: 1px solid {theme.border};
    border-top-right-radius: {m['button_radius']}px;
    border-bottom-right-radius: {m['button_radius']}px;
    background: {theme.panel_bg};
}}
QComboBox::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border: none;
}}
QComboBox QAbstractItemView {{
    background: {theme.panel_bg};
    color: {theme.text_primary};
    border: 1px solid {theme.border};
    selection-background-color: {theme.accent};
}}
QComboBox#formatCombo, QComboBox#qualityCombo {{
    font: 700 {m['button_font']:.1f}pt "Segoe UI";
}}
QComboBox#formatCombo QAbstractItemView, QComboBox#qualityCombo QAbstractItemView {{
    font: 700 {m['button_font']:.1f}pt "Segoe UI";
}}
QWidget#multiToolbarRow {{
    background: transparent;
}}
QLineEdit#multiAddInput {{
    font: 600 {m['url_input_font']:.1f}pt "Segoe UI";
}}
QScrollArea#multiEntriesScroll {{
    background: {theme.app_bg};
    border: 1px solid {theme.border};
    border-radius: {m['button_radius']}px;
    padding: {m['scroll_padding']}px;
}}
QScrollArea#multiEntriesScroll QWidget#qt_scrollarea_viewport {{
    background: transparent;
    border: none;
    border-radius: {m['button_radius']}px;
}}
QWidget#multiEntriesContainer {{
    background: transparent;
}}
QFrame#batchEntryCard {{
    background: {theme.panel_bg};
    border: 1px solid {theme.border};
    border-radius: {m['button_radius']}px;
}}
QFrame#batchEntryCard[duplicateRow="true"] {{
    border-color: {m['duplicate_color']};
}}
QFrame#singleMetaPanel {{
    background: transparent;
    border: none;
}}
QLabel#batchEntryThumbnail {{
    color: {theme.text_secondary};
    background: {theme.app_bg};
    border: 1px solid {theme.border};
    border-radius: {m['button_radius']}px;
    font: 700 {m['subtitle_font']:.1f}pt "Segoe UI";
}}
QLabel#singleMetaTitle {{
    color: {theme.text_primary};
    font: 650 {m['single_meta_title_font']:.1f}pt "Segoe UI";
}}
QLabel#singleMetaInfoLine {{
    color: {theme.text_secondary};
    font: 600 {m['subtitle_font']:.1f}pt "Segoe UI";
}}
QLabel#singleMetaStatus {{
    color: {theme.text_primary};
    border: 1px solid {theme.border};
    border-radius: {m['status_radius']}px;
    padding: 0px {m['status_pad_x']}px;
    font: 700 {m['subtitle_font']:.1f}pt "Segoe UI";
    background: {theme.panel_bg};
}}
QLabel#singleMetaStatus[state="valid"] {{
    border-color: {theme.success};
}}
QLabel#singleMetaStatus[state="idle"] {{
    color: {theme.text_secondary};
    border-color: {theme.border};
}}
QLabel#singleMetaStatus[state="invalid"], QLabel#singleMetaStatus[state="failed"] {{
    color: {theme.danger};
    border-color: {theme.danger};
}}
QLabel#singleMetaStatus[state="validating"], QLabel#singleMetaStatus[state="download_queued"], QLabel#singleMetaStatus[state="downloading"] {{
    border-color: {theme.accent};
}}
QLabel#singleMetaStatus[state="downloading"] {{
    color: {m['downloading_color']};
    border-color: {m['downloading_color']};
}}
QLabel#singleMetaStatus[state="paused"] {{
    color: #D9A441;
    border-color: #D9A441;
}}
QLabel#singleMetaStatus[state="done"], QLabel#singleMetaStatus[state="skipped"] {{
    color: {theme.success};
    border-color: {theme.success};
}}
QLabel#batchEntryUrl {{
    color: {theme.text_primary};
    font: 600 {m['button_font']:.1f}pt "Segoe UI";
    padding: 0px;
    border-radius: {m['status_radius']}px;
}}
QLabel#batchEntryUrl[hovered="true"] {{
    color: {theme.accent};
    background: transparent;
    border: none;
}}
QLabel#batchEntryUrl[state="done"] {{
    color: {theme.success};
}}
QLabel#batchEntryUrl[state="invalid"] {{
    color: {theme.danger};
}}
QLabel#batchEntryUrl[state="paused"] {{
    color: #D9A441;
}}
QLabel#batchEntryUrl[state="duplicate"] {{
    color: {m['duplicate_color']};
}}
QLabel#batchEntryStatus {{
    color: {theme.text_primary};
    border: 1px solid {theme.border};
    border-radius: {m['status_radius']}px;
    padding: {m['entry_status_pad_y']}px {m['entry_status_pad_x']}px;
    font: 700 {m['subtitle_font']:.1f}pt "Segoe UI";
    background: {theme.panel_bg};
}}
QLabel#batchEntryStatus[state="valid"] {{
    border-color: {theme.success};
}}
QLabel#batchEntryStatus[state="invalid"], QLabel#batchEntryStatus[state="failed"] {{
    color: {theme.danger};
    border-color: {theme.danger};
}}
QLabel#batchEntryStatus[state="validating"], QLabel#batchEntryStatus[state="download_queued"], QLabel#batchEntryStatus[state="downloading"] {{
    border-color: {theme.accent};
}}
QLabel#batchEntryStatus[state="downloading"] {{
    color: {m['downloading_color']};
    border-color: {m['downloading_color']};
}}
QLabel#batchEntryStatus[state="paused"] {{
    color: #D9A441;
    border-color: #D9A441;
}}
QLabel#batchEntryStatus[state="done"], QLabel#batchEntryStatus[state="skipped"] {{
    color: {theme.success};
    border-color: {theme.success};
}}
QLabel#batchEntryStatus[state="duplicate"] {{
    color: {m['duplicate_color']};
    border-color: {m['duplicate_color']};
}}
QPushButton#batchEntryAction {{
    min-height: {m['entry_action_min_h']}px;
    font: 700 {m['subtitle_font']:.1f}pt "Segoe UI";
}}
QComboBox#batchEntryFormat, QComboBox#batchEntryQuality {{
    background: {theme.app_bg};
    color: {theme.text_primary};
    border: 1px solid {theme.border};
    border-radius: {m['button_radius']}px;
    padding: {m['button_pad_y']}px {m['button_pad_x']}px;
    padding-right: {m['combo_pad_right']}px;
    min-height: {m['entry_combo_min_h']}px;
    font: 700 {m['subtitle_font']:.1f}pt "Segoe UI";
}}
QComboBox#batchEntryFormat::drop-down, QComboBox#batchEntryQuality::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: {m['combo_drop_width']}px;
    border: none;
    border-left: 1px solid {theme.border};
    border-top-right-radius: {m['button_radius']}px;
    border-bottom-right-radius: {m['button_radius']}px;
    background: {theme.panel_bg};
}}
QComboBox#batchEntryFormat::down-arrow, QComboBox#batchEntryQuality::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border: none;
}}
QComboBox#batchEntryFormat QAbstractItemView, QComboBox#batchEntryQuality QAbstractItemView {{
    background: {theme.panel_bg};
    color: {theme.text_primary};
    border: 1px solid {theme.border};
    selection-background-color: {theme.accent};
    font: 700 {m['subtitle_font']:.1f}pt "Segoe UI";
}}
"""


def _build_stylesheet_section_progress(theme: ThemePalette, m: dict[str, float | int | str]) -> str:
    return f"""
QCheckBox {{
    color: {theme.text_primary};
    font: 600 {m['button_font']:.1f}pt "Segoe UI";
    spacing: {m['checkbox_spacing']}px;
}}
QProgressBar#downloadProgress {{
    background: {theme.app_bg};
    border: 1px solid {theme.border};
    border-radius: {m['status_radius']}px;
    min-height: {m['progress_h']}px;
    max-height: {m['progress_h']}px;
    text-align: center;
    color: {theme.text_primary};
    font: 700 {m['progress_font']:.1f}pt "Segoe UI";
}}
QProgressBar#downloadProgress::chunk {{
    background: {theme.accent};
    border-radius: {m['status_radius']}px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
"""


def build_stylesheet(theme: ThemePalette, ui_scale: float = 1.0) -> str:
    metrics = _build_stylesheet_metrics(theme, ui_scale)
    return "".join(
        (
            _build_stylesheet_section_base(theme, metrics),
            _build_stylesheet_section_inputs_and_rows(theme, metrics),
            _build_stylesheet_section_progress(theme, metrics),
        )
    )
