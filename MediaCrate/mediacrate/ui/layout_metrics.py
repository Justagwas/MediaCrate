from __future__ import annotations


def normalize_scale_factor(scale: float) -> float:
    try:
        parsed = float(scale)
    except Exception:
        parsed = 1.0
    return max(0.1, min(8.0, parsed))


def single_url_baseline_metrics(scale: float) -> dict[str, int]:
    normalized_scale = normalize_scale_factor(scale)

    def scaled(value: int, minimum: int) -> int:
        computed = int(round(value * normalized_scale))
        if normalized_scale < 1.0:
            return max(1, computed)
        return max(minimum, computed)

    return {
        "progress_bar_height": scaled(24, 16),
        "features_left_margin": scaled(7, 5),
        "features_top_margin": scaled(4, 2),
        "features_right_margin": scaled(7, 5),
        "features_bottom_margin": scaled(6, 2),
        "features_spacing": scaled(6, 3),
        "meta_spacing": scaled(6, 3),
        "meta_text_spacing": scaled(1, 0),
        "meta_top_row_spacing": scaled(4, 2),
        "meta_bottom_row_spacing": scaled(3, 2),
        "thumb_top_offset": scaled(2, 1),
        "thumb_bottom_offset": scaled(2, 1),
        "status_height": scaled(22, 18),
        "status_bottom_offset": scaled(3, 0),
        "controls_mid_gap": scaled(14, 5),
        "combo_height_bump": scaled(2, 1),
        "button_height_bump": scaled(2, 1),
        "row_base_min": scaled(114, 74),
        "row_min": scaled(72, 52),
        "compact_delta": scaled(16, 16),
        "thumb_width_max": scaled(112, 70),
        "thumb_width_min": scaled(82, 50),
    }
