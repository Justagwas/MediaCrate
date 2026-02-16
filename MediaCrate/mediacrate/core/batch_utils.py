from __future__ import annotations

from .download_service import normalize_batch_url
from .models import is_audio_format_choice


def build_download_signature(
    *,
    url_normalized: str,
    url_raw: str,
    format_choice: str,
    quality_choice: str = "BEST QUALITY",
) -> tuple[str, str, str]:
    normalized_url = str(url_normalized or "").strip()
    if not normalized_url:
        normalized_url = normalize_batch_url(str(url_raw or "").strip())
    normalized_format = str(format_choice or "VIDEO").strip().upper() or "VIDEO"
    if is_audio_format_choice(normalized_format):
        normalized_quality = "BEST QUALITY"
    else:
        normalized_quality = str(quality_choice or "BEST QUALITY").strip().upper() or "BEST QUALITY"
    return normalized_url, normalized_format, normalized_quality
