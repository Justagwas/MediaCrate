from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import dataclasses
import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Any

from .config import DEFAULT_FILENAME_TEMPLATE
from .node_runtime import NODE_MIN_MAJOR_VERSION, is_supported_node_runtime
from .models import (
    DownloadJob,
    DownloadResult,
    DownloadSummary,
    DownloadState,
    FormatChoice,
    FormatProbeResult,
    RetryProfile,
    UrlAnalysisResult,
    CONVERSION_CONTAINER_CHOICES,
    CONVERSION_CONTAINER_ORDER,
    DEFAULT_FORMAT_CHOICES,
    is_audio_format_choice,
)
from .formatting import format_size_human as _format_size_human
from .partial_files import discard_partial_candidates, record_partial_candidates
from .paths import resolve_binary

_PROGRESS_RE = re.compile(r"(?P<percent>\d+(?:\.\d+)?)%")
_DOWNLOAD_LINE_TOTAL_RE = re.compile(r"\bof\s+(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTPE]?i?B|[KMGTPE]?B)\b", re.IGNORECASE)
SingleProgressCallback = Callable[[float, str], None]
BatchProgressCallback = Callable[[str, float, str], None]
StatusCallback = Callable[[str, str], None]
LogCallback = Callable[[str], None]

_IMPORTANT_LOG_TOKENS = (
    "error:",
    "warning:",
    "has already been downloaded",
)
_POST_PROCESSING_LINE_TOKENS = (
    "[merger]",
    "merging formats",
    "[extractaudio]",
    "extracting audio",
    "[ffmpeg]",
    "post-process",
    "post process",
    "fixup",
    "deleting original file",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

_TRACKING_QUERY_KEYS = {
    "feature",
    "si",
    "spm",
    "source",
    "fbclid",
    "gclid",
    "igshid",
    "ref",
    "ref_src",
    "tracking_id",
    "trk",
}

_RETRYABLE_ERROR_TOKENS = (
    "temporary",
    "temporarily",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "connection refused",
    "network is unreachable",
    "name resolution",
    "dns",
    "429",
    "too many requests",
    "rate limit",
    "try again later",
    "service unavailable",
)

_NON_RETRYABLE_ERROR_TOKENS = (
    "unsupported url",
    "unsupported",
    "private video",
    "members-only",
    "sign in",
    "login required",
    "geo",
    "not available in your country",
    "permission denied",
    "access is denied",
    "ffmpeg not found",
    "node not found",
)
_FORMAT_UNAVAILABLE_ERROR_TOKENS = (
    "requested format is not available",
    "requested format not available",
    "format not available",
    "no such format",
)

_CONFLICT_POLICY_VALUES = {"skip", "rename", "overwrite"}
_DEFAULT_OUTPUT_TEMPLATE = DEFAULT_FILENAME_TEMPLATE
_INPROCESS_CANCELLED_SENTINEL = "__MEDIACRATE_CANCELLED__"
_INPROCESS_PAUSED_SENTINEL = "__MEDIACRATE_PAUSED__"
_YTDLP_MODE_ENV = "MEDIACRATE_YTDLP_MODE"
_YTDLP_BINARY_ENV = "MEDIACRATE_YTDLP_BINARY"
_METADATA_WORKER_ENV = "MEDIACRATE_METADATA_WORKER"
_METADATA_MODE_ENV = "MEDIACRATE_METADATA_MODE"
_METADATA_WORKER_GRACE_SECONDS = 1.5
_METADATA_STDERR_TAIL_BYTES = 16 * 1024
_SIZE_ESTIMATE_DEFAULT_KEY = "__DEFAULT__"
_MC_QUALITY_TOKEN_RE = re.compile(r"%\((_?mc_quality)\)[^%a-zA-Z]*[a-zA-Z]", re.IGNORECASE)
_QUALITY_BRACKET_TOKEN_RE = re.compile(r"\[quality\]", re.IGNORECASE)


format_size_human = _format_size_human


def _default_format_choices() -> list[str]:
    return list(DEFAULT_FORMAT_CHOICES)


def _extract_expected_size_bytes(info: dict[str, object]) -> int | None:
    if not isinstance(info, dict):
        return None
    for key in ("filesize", "filesize_approx"):
        value = info.get(key)
        if isinstance(value, (int, float)) and int(value) > 0:
            return int(value)
    requested_downloads = info.get("requested_downloads")
    if isinstance(requested_downloads, list):
        total = 0
        for item in requested_downloads:
            if not isinstance(item, dict):
                continue
            size = item.get("filesize") or item.get("filesize_approx")
            if isinstance(size, (int, float)) and int(size) > 0:
                total += int(size)
        if total > 0:
            return total
    best_size = None
    for fmt in info.get("formats", []) if isinstance(info.get("formats"), list) else []:
        if not isinstance(fmt, dict):
            continue
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if isinstance(size, (int, float)) and int(size) > 0:
            candidate = int(size)
            if best_size is None or candidate > best_size:
                best_size = candidate
    return best_size


def _selection_size_key(format_choice: str, quality_choice: str) -> str:
    normalized_format = str(format_choice or FormatChoice.VIDEO.value).strip().upper() or FormatChoice.VIDEO.value
    if is_audio_format_choice(normalized_format):
        normalized_quality = "BEST QUALITY"
    else:
        normalized_quality = str(quality_choice or "BEST QUALITY").strip().upper() or "BEST QUALITY"
    return f"{normalized_format}|{normalized_quality}"


def _size_from_format_item(fmt: dict[str, object], *, duration_seconds: int | None) -> int | None:
    direct = fmt.get("filesize") or fmt.get("filesize_approx")
    if isinstance(direct, (int, float)) and int(direct) > 0:
        return int(direct)
    if not isinstance(duration_seconds, int) or duration_seconds <= 0:
        return None
    bitrate_kbps = None
    for key in ("tbr", "vbr", "abr"):
        raw = fmt.get(key)
        if isinstance(raw, (int, float)) and float(raw) > 0:
            candidate = float(raw)
            if bitrate_kbps is None or candidate > bitrate_kbps:
                bitrate_kbps = candidate
    if bitrate_kbps is None:
        return None
    estimated = _estimate_size_from_bitrate(duration_seconds, bitrate_kbps)
    return estimated if estimated > 0 else None


def _estimate_size_from_bitrate(duration_seconds: int | None, bitrate_kbps: float | None) -> int | None:
    if not isinstance(duration_seconds, int) or duration_seconds <= 0:
        return None
    if not isinstance(bitrate_kbps, (int, float)) or float(bitrate_kbps) <= 0:
        return None
    estimated = int((float(bitrate_kbps) * 1000.0 / 8.0) * float(duration_seconds))
    return estimated if estimated > 0 else None


def _sum_size_from_format_items(
    formats: list[dict[str, object]],
    *,
    duration_seconds: int | None,
) -> int | None:
    total = 0
    found = False
    for item in formats:
        if not isinstance(item, dict):
            continue
        direct = item.get("filesize") or item.get("filesize_approx")
        if isinstance(direct, (int, float)) and int(direct) > 0:
            total += int(direct)
            found = True
            continue
        estimate = _size_from_format_item(item, duration_seconds=duration_seconds)
        if isinstance(estimate, int) and estimate > 0:
            total += int(estimate)
            found = True
    return total if found else None


def _requested_format_items(info_dict: dict[str, object]) -> list[dict[str, object]]:
    if not isinstance(info_dict, dict):
        return []
    for key in ("requested_downloads", "requested_formats"):
        raw = info_dict.get(key)
        if isinstance(raw, list):
            items = [item for item in raw if isinstance(item, dict)]
            if items:
                return items
    return []


def _enrich_format_sizes(
    formats: list[dict[str, object]],
    *,
    duration_seconds: int | None,
) -> list[dict[str, object]]:
    enriched: list[dict[str, object]] = []
    for item in formats:
        if not isinstance(item, dict):
            continue
        updated = dict(item)
        updated["_size_estimate"] = _size_from_format_item(item, duration_seconds=duration_seconds)
        enriched.append(updated)
    return enriched


def _estimate_mp3_size(
    formats: list[dict[str, object]],
    *,
    duration_seconds: int | None,
) -> int | None:
    target_bitrate_kbps = 245.0  # Approximate V0 average used by --audio-quality 0
    target_estimate = _estimate_size_from_bitrate(duration_seconds, target_bitrate_kbps)
    if target_estimate is None:
        return _best_audio_size(formats)
    source_size = _best_audio_size(formats)
    if source_size is None:
        return target_estimate
    source_bitrate_kbps = (float(source_size) * 8.0) / (float(duration_seconds) * 1000.0)
    if source_bitrate_kbps > target_bitrate_kbps:
        return min(int(source_size), int(target_estimate))
    return target_estimate


def _normalize_height(value: object) -> int:
    if isinstance(value, int):
        return max(0, int(value))
    if isinstance(value, float):
        return max(0, int(round(value)))
    return 0


def _best_audio_size(formats: list[dict[str, object]], *, preferred_ext: str = "") -> int | None:
    requested_ext = str(preferred_ext or "").strip().lower()
    best = None
    fallback = None
    for item in formats:
        acodec = str(item.get("acodec") or "").strip().lower()
        if not acodec or acodec == "none":
            continue
        value = item.get("_size_estimate")
        if not isinstance(value, int) or value <= 0:
            continue
        ext = str(item.get("ext") or "").strip().lower()
        if requested_ext and ext == requested_ext:
            if best is None or value > best:
                best = value
            continue
        if fallback is None or value > fallback:
            fallback = value
    if best is not None:
        return best
    return fallback


def _best_video_size(
    formats: list[dict[str, object]],
    *,
    max_height: int | None,
    preferred_ext: str = "",
) -> int | None:
    requested_ext = str(preferred_ext or "").strip().lower()
    best_tuple = None
    fallback_tuple = None
    for item in formats:
        vcodec = str(item.get("vcodec") or "").strip().lower()
        if not vcodec or vcodec == "none":
            continue
        size_value = item.get("_size_estimate")
        if not isinstance(size_value, int) or size_value <= 0:
            continue
        height = _normalize_height(item.get("height"))
        if max_height is not None and height > max_height:
            continue
        ext = str(item.get("ext") or "").strip().lower()
        rank = (height, size_value)
        if requested_ext and ext == requested_ext:
            if best_tuple is None or rank > best_tuple:
                best_tuple = rank
            continue
        if fallback_tuple is None or rank > fallback_tuple:
            fallback_tuple = rank
    if best_tuple is not None:
        return int(best_tuple[1])
    if fallback_tuple is not None:
        return int(fallback_tuple[1])
    return None


def _best_progressive_size(
    formats: list[dict[str, object]],
    *,
    max_height: int | None,
    preferred_ext: str = "",
) -> int | None:
    requested_ext = str(preferred_ext or "").strip().lower()
    best_tuple = None
    fallback_tuple = None
    for item in formats:
        vcodec = str(item.get("vcodec") or "").strip().lower()
        acodec = str(item.get("acodec") or "").strip().lower()
        if (not vcodec) or vcodec == "none" or (not acodec) or acodec == "none":
            continue
        size_value = item.get("_size_estimate")
        if not isinstance(size_value, int) or size_value <= 0:
            continue
        height = _normalize_height(item.get("height"))
        if max_height is not None and height > max_height:
            continue
        ext = str(item.get("ext") or "").strip().lower()
        rank = (height, size_value)
        if requested_ext and ext == requested_ext:
            if best_tuple is None or rank > best_tuple:
                best_tuple = rank
            continue
        if fallback_tuple is None or rank > fallback_tuple:
            fallback_tuple = rank
    if best_tuple is not None:
        return int(best_tuple[1])
    if fallback_tuple is not None:
        return int(fallback_tuple[1])
    return None


def _estimate_selection_size_bytes_from_info(
    info_dict: dict[str, object],
    *,
    format_choice: str,
    quality_choice: str,
) -> int | None:
    if not isinstance(info_dict, dict):
        return None
    formats_raw = info_dict.get("formats")
    if not isinstance(formats_raw, list) or not formats_raw:
        return _extract_expected_size_bytes(info_dict)

    duration_seconds = _extract_duration_seconds(info_dict)
    formats: list[dict[str, object]] = []
    for item in formats_raw:
        if not isinstance(item, dict):
            continue
        enriched = dict(item)
        enriched["_size_estimate"] = _size_from_format_item(item, duration_seconds=duration_seconds)
        formats.append(enriched)

    if not formats:
        return _extract_expected_size_bytes(info_dict)

    choice = str(format_choice or FormatChoice.VIDEO.value).strip().upper() or FormatChoice.VIDEO.value
    height_limit = _quality_height(quality_choice)

    if is_audio_format_choice(choice):
        preferred_ext = ""
        if choice not in {FormatChoice.AUDIO.value, FormatChoice.MP3.value}:
            preferred_ext = choice.lower()
        if choice == FormatChoice.MP3.value:
            return _estimate_mp3_size(formats, duration_seconds=duration_seconds)
        return _best_audio_size(formats, preferred_ext=preferred_ext)

    audio_size = _best_audio_size(formats)

    if choice == FormatChoice.MP4.value:
        video_size = _best_video_size(formats, max_height=height_limit, preferred_ext="mp4")
        m4a_size = _best_audio_size(formats, preferred_ext="m4a")
        if video_size is not None and m4a_size is not None:
            return int(video_size + m4a_size)
        progressive_mp4 = _best_progressive_size(formats, max_height=height_limit, preferred_ext="mp4")
        if progressive_mp4 is not None:
            return progressive_mp4
        if video_size is not None and audio_size is not None:
            return int(video_size + audio_size)
        return _best_progressive_size(formats, max_height=height_limit)

    if choice == FormatChoice.VIDEO.value or choice in CONVERSION_CONTAINER_CHOICES:
        video_size = _best_video_size(formats, max_height=height_limit)
        if video_size is not None and audio_size is not None:
            return int(video_size + audio_size)
        return _best_progressive_size(formats, max_height=height_limit)

    requested_ext = choice.lower()
    custom_video_size = _best_video_size(formats, max_height=height_limit, preferred_ext=requested_ext)
    if custom_video_size is not None and audio_size is not None:
        return int(custom_video_size + audio_size)
    custom_progressive = _best_progressive_size(formats, max_height=height_limit, preferred_ext=requested_ext)
    if custom_progressive is not None:
        return custom_progressive
    if custom_video_size is not None:
        return custom_video_size
    return _best_progressive_size(formats, max_height=height_limit)


def _build_selection_size_estimates(
    info_dict: dict[str, object],
    *,
    formats: list[str],
    qualities: list[str],
) -> dict[str, int]:
    estimates: dict[str, int] = {}
    default_size = _extract_expected_size_bytes(info_dict)
    if default_size is not None:
        estimates[_SIZE_ESTIMATE_DEFAULT_KEY] = int(default_size)

    normalized_formats: list[str] = []
    seen_formats: set[str] = set()
    for item in formats:
        value = str(item or "").strip().upper()
        if not value or value in seen_formats:
            continue
        seen_formats.add(value)
        normalized_formats.append(value)

    normalized_qualities: list[str] = []
    seen_qualities: set[str] = set()
    for item in ["BEST QUALITY", *qualities]:
        value = str(item or "").strip().upper()
        if not value or value in seen_qualities:
            continue
        seen_qualities.add(value)
        normalized_qualities.append(value)

    for fmt in normalized_formats:
        if is_audio_format_choice(fmt):
            estimate = _estimate_selection_size_bytes_from_info(
                info_dict,
                format_choice=fmt,
                quality_choice="BEST QUALITY",
            )
            if estimate is not None:
                estimates[_selection_size_key(fmt, "BEST QUALITY")] = int(estimate)
            continue
        for quality in normalized_qualities:
            estimate = _estimate_selection_size_bytes_from_info(
                info_dict,
                format_choice=fmt,
                quality_choice=quality,
            )
            if estimate is None:
                continue
            estimates[_selection_size_key(fmt, quality)] = int(estimate)

    return estimates


def estimate_selection_size_bytes(
    result: UrlAnalysisResult,
    format_choice: str,
    quality_choice: str,
) -> int | None:
    estimates = result.selection_size_estimates if isinstance(result.selection_size_estimates, dict) else {}
    normalized_format = str(format_choice or FormatChoice.VIDEO.value).strip().upper() or FormatChoice.VIDEO.value
    if is_audio_format_choice(normalized_format):
        normalized_quality = "BEST QUALITY"
    else:
        normalized_quality = str(quality_choice or "BEST QUALITY").strip().upper() or "BEST QUALITY"
    available_formats = {
        str(item or "").strip().upper()
        for item in (result.formats or [])
        if str(item or "").strip()
    }
    if available_formats and normalized_format not in available_formats:
        return None
    key = _selection_size_key(normalized_format, normalized_quality)
    value = estimates.get(key)
    if isinstance(value, int) and value > 0:
        return int(value)
    if normalized_format == FormatChoice.VIDEO.value and normalized_quality == "BEST QUALITY":
        fallback = estimates.get(_SIZE_ESTIMATE_DEFAULT_KEY)
        if isinstance(fallback, int) and fallback > 0:
            return int(fallback)
        if isinstance(result.expected_size_bytes, int) and result.expected_size_bytes > 0:
            return int(result.expected_size_bytes)
    return None


def _extract_duration_seconds(info: dict[str, object]) -> int | None:
    if not isinstance(info, dict):
        return None
    value = info.get("duration")
    if isinstance(value, (int, float)):
        seconds = int(round(float(value)))
        if seconds > 0:
            return seconds
    return None


def _extract_source_label(info: dict[str, object], fallback_url: str) -> str:
    if isinstance(info, dict):
        explicit_domain = str(info.get("webpage_url_domain") or "").strip().lower()
        if explicit_domain:
            return explicit_domain
        for key in ("webpage_url", "original_url"):
            candidate_url = str(info.get(key) or "").strip()
            if not candidate_url:
                continue
            try:
                parsed = urlparse(candidate_url)
            except Exception:
                continue
            host = str(parsed.netloc or "").strip().lower()
            if host:
                return host[4:] if host.startswith("www.") else host
        for key in ("extractor_key", "extractor"):
            extractor = str(info.get(key) or "").strip()
            if extractor and extractor.lower() != "generic":
                return extractor.replace("_", " ")
    fallback_value = str(fallback_url or "").strip()
    if not fallback_value:
        return ""
    try:
        parsed = urlparse(fallback_value)
    except Exception:
        return ""
    host = str(parsed.netloc or "").strip().lower()
    if not host:
        return ""
    return host[4:] if host.startswith("www.") else host


def coerce_http_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except Exception:
        return value
    if parsed.scheme in {"http", "https"}:
        return value
    if parsed.scheme:
        return value

    candidate = f"https:{value}" if value.startswith("//") else f"https://{value}"
    try:
        reparsed = urlparse(candidate)
    except Exception:
        return value
    host = str(reparsed.netloc or "").strip()
    if (not host) or (" " in host) or ("." not in host):
        return value
    return candidate


def validate_url(url: str) -> bool:
    value = coerce_http_url(url)
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_batch_url(url: str) -> str:
    value = coerce_http_url(url)
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except Exception:
        return value
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
        if not path:
            path = "/"

    retained_pairs: list[tuple[str, str]] = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.strip().lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        retained_pairs.append((key, val))
    retained_pairs.sort(key=lambda pair: (pair[0].lower(), pair[1]))
    query = urlencode(retained_pairs, doseq=True)

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def _collect_format_inventory(info_dict: dict[str, object]) -> tuple[list[str], list[str]]:
    heights: set[int] = set()
    other_extensions: set[str] = set()
    formats = info_dict.get("formats")
    for fmt in formats if isinstance(formats, list) else []:
        if not isinstance(fmt, dict):
            continue
        height = fmt.get("height")
        vcodec = str(fmt.get("vcodec") or "")
        if isinstance(height, int) and height > 0 and vcodec != "none":
            heights.add(height)

    for ext in CONVERSION_CONTAINER_ORDER:
        other_extensions.add(ext)

    qualities = ["BEST QUALITY", *[f"{height}p" for height in sorted(heights, reverse=True)]]
    return qualities, sorted(other_extensions)


def _merge_unique_formats(base_formats: list[str], extra_formats: list[str]) -> list[str]:
    merged_formats: list[str] = []
    seen_formats: set[str] = set()
    for item in [*base_formats, *extra_formats]:
        normalized_item = str(item or "").strip().upper()
        if not normalized_item or normalized_item in seen_formats:
            continue
        seen_formats.add(normalized_item)
        merged_formats.append(normalized_item)
    return merged_formats


def _metadata_extract_options(timeout_seconds: float | None = None) -> dict[str, object]:
    opts: dict[str, object] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "simulate": True,
        "noplaylist": True,
        "retries": 0,
        "extractor_retries": 0,
    }
    if isinstance(timeout_seconds, (int, float)):
        clamped_timeout = max(1.0, float(timeout_seconds))
        opts["socket_timeout"] = clamped_timeout
    return opts


def _dataclass_payload(value: object) -> dict[str, object]:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _format_probe_from_payload(payload: object, *, fallback_error: str = "") -> FormatProbeResult:
    data = payload if isinstance(payload, dict) else {}
    formats = data.get("formats") if isinstance(data, dict) else None
    other_formats = data.get("other_formats") if isinstance(data, dict) else None
    qualities = data.get("qualities") if isinstance(data, dict) else None
    return FormatProbeResult(
        title=str(data.get("title") or "") if isinstance(data, dict) else "",
        formats=[str(item or "") for item in formats] if isinstance(formats, list) else _default_format_choices(),
        other_formats=[str(item or "") for item in other_formats] if isinstance(other_formats, list) else [],
        qualities=[str(item or "") for item in qualities] if isinstance(qualities, list) else ["BEST QUALITY"],
        error=str(data.get("error") or fallback_error) if isinstance(data, dict) else fallback_error,
    )


def _positive_optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    return None


def _url_analysis_from_payload(payload: object, *, url: str, fallback_error: str = "") -> UrlAnalysisResult:
    data = payload if isinstance(payload, dict) else {}
    formats = data.get("formats") if isinstance(data, dict) else None
    qualities = data.get("qualities") if isinstance(data, dict) else None
    estimates = data.get("selection_size_estimates") if isinstance(data, dict) else None
    return UrlAnalysisResult(
        url_raw=str(data.get("url_raw") or url) if isinstance(data, dict) else str(url or ""),
        url_normalized=str(data.get("url_normalized") or normalize_batch_url(url)) if isinstance(data, dict) else normalize_batch_url(url),
        is_valid=bool(data.get("is_valid")) if isinstance(data, dict) else False,
        title=str(data.get("title") or "") if isinstance(data, dict) else "",
        thumbnail_url=str(data.get("thumbnail_url") or "") if isinstance(data, dict) else "",
        expected_size_bytes=_positive_optional_int(data.get("expected_size_bytes")) if isinstance(data, dict) else None,
        duration_seconds=_positive_optional_int(data.get("duration_seconds")) if isinstance(data, dict) else None,
        source_label=str(data.get("source_label") or "") if isinstance(data, dict) else "",
        formats=[str(item or "") for item in formats] if isinstance(formats, list) else _default_format_choices(),
        qualities=[str(item or "") for item in qualities] if isinstance(qualities, list) else ["BEST QUALITY"],
        selection_size_estimates={
            str(key): int(value)
            for key, value in (estimates.items() if isinstance(estimates, dict) else [])
            if isinstance(value, int)
        },
        error=str(data.get("error") or fallback_error) if isinstance(data, dict) else fallback_error,
    )


def _read_metadata_stderr_tail(stream: object) -> str:
    seek = getattr(stream, "seek", None)
    read = getattr(stream, "read", None)
    if not callable(seek) or not callable(read):
        return ""
    try:
        seek(0, os.SEEK_END)
        end_position = int(getattr(stream, "tell")())
        seek(max(0, end_position - _METADATA_STDERR_TAIL_BYTES), os.SEEK_SET)
        return str(read() or "").strip()
    except Exception:
        return ""


def _quality_height(value: str) -> int | None:
    cleaned = str(value or "").strip().lower()
    if not cleaned or cleaned in {"best", "best quality"}:
        return None
    if cleaned.endswith("p"):
        cleaned = cleaned[:-1]
    try:
        return int(cleaned)
    except ValueError:
        return None


def _format_selector(format_choice: str, quality_choice: str) -> tuple[str, list[str]]:
    raw_choice = str(format_choice or FormatChoice.VIDEO.value).strip()
    choice = raw_choice.upper()
    height = _quality_height(quality_choice)
    post_args: list[str] = []

    if choice == FormatChoice.AUDIO.value:
        return "bestaudio", post_args
    if choice == FormatChoice.MP3.value:
        post_args.extend(["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"])
        return "bestaudio", post_args
    if is_audio_format_choice(choice):
        post_args.extend(["--extract-audio", "--audio-format", raw_choice.lower()])
        return "bestaudio", post_args

    if height is None:
        height_selector = ""
    else:
        height_selector = f"[height<={height}]"

    if choice == FormatChoice.MP4.value:
        selector = (
            f"bestvideo[ext=mp4]{height_selector}+bestaudio[ext=m4a]/"
            f"best[ext=mp4]{height_selector}/best{height_selector}"
        )
        post_args.extend(["--merge-output-format", "mp4"])
        return selector, post_args

    if choice not in {
        FormatChoice.VIDEO.value,
        FormatChoice.AUDIO.value,
        FormatChoice.MP4.value,
        FormatChoice.MP3.value,
    }:
        ext = raw_choice.lower()
        if choice in CONVERSION_CONTAINER_CHOICES:
            selector = (
                f"bestvideo{height_selector}+bestaudio/"
                f"best{height_selector}/best"
            )
            post_args.extend(["--merge-output-format", ext])
            return selector, post_args
        selector = (
            f"bestvideo[ext={ext}]{height_selector}+bestaudio/"
            f"best[ext={ext}]{height_selector}"
        )
        return selector, post_args

    selector = (
        f"bestvideo{height_selector}+bestaudio/"
        f"best{height_selector}/best"
    )
    return selector, post_args


def _fixed_output_extension(format_choice: str) -> str | None:
    raw_choice = str(format_choice or "").strip()
    if not raw_choice:
        return None
    choice = raw_choice.upper()
    if choice == FormatChoice.MP3.value:
        return "mp3"
    if choice == FormatChoice.MP4.value:
        return "mp4"
    if choice in CONVERSION_CONTAINER_CHOICES:
        return choice.lower()
    if choice in {
        FormatChoice.VIDEO.value,
        FormatChoice.AUDIO.value,
    }:
        return None
    candidate = re.sub(r"[^a-z0-9]+", "", raw_choice.lower())
    return candidate or None


def _with_forced_extension(output_template: str, extension: str | None) -> str:
    template = str(output_template or "").strip()
    ext = str(extension or "").strip().lower()
    if not template or not ext:
        return template
    if "%(ext)s" in template:
        return template.replace("%(ext)s", ext)
    suffix = f".{ext}"
    if template.lower().endswith(suffix):
        return template
    return f"{template}{suffix}"


def _with_rename_number(output_template: str, number: int) -> str:
    template = str(output_template or "").strip()
    index = max(0, int(number))
    if not template or index <= 0:
        return template
    token = f" [{index:03d}]"
    ext_token = ".%(ext)s"
    if ext_token in template:
        return template.replace(ext_token, f"{token}{ext_token}", 1)
    path = Path(template)
    suffix = path.suffix
    if suffix:
        return str(path.with_name(f"{path.stem}{token}{suffix}"))
    return f"{template}{token}"


def sanitize_filename_template(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return _DEFAULT_OUTPUT_TEMPLATE
    normalized = text.replace("\\", "/")
    if normalized.startswith("/"):
        return _DEFAULT_OUTPUT_TEMPLATE
    if re.match(r"^[A-Za-z]:/", normalized):
        return _DEFAULT_OUTPUT_TEMPLATE
    segments = [segment for segment in normalized.split("/") if segment not in {"", "."}]
    if any(segment == ".." for segment in segments):
        return _DEFAULT_OUTPUT_TEMPLATE
    return text


def _sanitize_template_token(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "UNKNOWN"
    text = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "UNKNOWN"


def _quality_template_value(format_choice: str, quality_choice: str) -> str:
    normalized_format = str(format_choice or FormatChoice.VIDEO.value).strip().upper() or FormatChoice.VIDEO.value
    normalized_quality = str(quality_choice or "").strip()
    if is_audio_format_choice(normalized_format):
        if normalized_format == FormatChoice.AUDIO.value:
            return "AUDIO"
        if normalized_format == FormatChoice.MP3.value:
            return "MP3"
        return _sanitize_template_token(normalized_format)
    if not normalized_quality:
        return "BEST QUALITY"
    return _sanitize_template_token(normalized_quality)


def _apply_runtime_template_tokens(
    template: str,
    *,
    format_choice: str,
    quality_choice: str,
) -> str:
    resolved = str(template or "").strip()
    if not resolved:
        return resolved
    quality_value = _quality_template_value(format_choice, quality_choice)
    resolved = _MC_QUALITY_TOKEN_RE.sub(quality_value, resolved)
    resolved = _QUALITY_BRACKET_TOKEN_RE.sub(quality_value, resolved)
    return resolved


def _resolve_output_template(
    *,
    output_dir: Path,
    filename_template: str,
    job: DownloadJob,
    conflict_policy: str = "skip",
) -> str:
    sanitized_template = sanitize_filename_template(filename_template)
    runtime_template = _apply_runtime_template_tokens(
        sanitized_template,
        format_choice=job.format_choice,
        quality_choice=job.quality_choice,
    )
    return _with_forced_extension(
        str(output_dir / runtime_template),
        _fixed_output_extension(job.format_choice),
    )


def normalize_conflict_policy(value: str) -> str:
    policy = str(value or "").strip().lower()
    if policy not in _CONFLICT_POLICY_VALUES:
        return "skip"
    return policy


def _is_important_log_line(line: str) -> bool:
    value = str(line or "").strip()
    if not value:
        return False
    lowered = value.lower()
    return any(token in lowered for token in _IMPORTANT_LOG_TOKENS)


def _is_post_processing_line(line: str) -> bool:
    value = str(line or "").strip()
    if not value:
        return False
    lowered = value.lower()
    return any(token in lowered for token in _POST_PROCESSING_LINE_TOKENS)


def sanitize_error_text(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    no_ansi = _ANSI_ESCAPE_RE.sub("", text)
    no_ctrl = _CONTROL_CHAR_RE.sub("", no_ansi)
    collapsed = no_ctrl.replace("\r", "\n")
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    return collapsed.strip()


def _progress_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _progress_percent_from_payload(payload: dict[str, Any]) -> float | None:
    explicit_percent = _progress_number(payload.get("_percent"))
    if explicit_percent is not None:
        return explicit_percent

    downloaded = _progress_number(payload.get("downloaded_bytes"))
    for key in ("total_bytes", "total_bytes_estimate"):
        total = _progress_number(payload.get(key))
        if downloaded is not None and total is not None and total > 0:
            return (downloaded / total) * 100.0

    fragment_index = _progress_number(payload.get("fragment_index"))
    fragment_count = _progress_number(payload.get("fragment_count"))
    if fragment_index is not None and fragment_count is not None and fragment_count > 0:
        return (fragment_index / fragment_count) * 100.0

    for key in ("_percent_str", "_progress_str"):
        text = sanitize_error_text(payload.get(key))
        match = _PROGRESS_RE.search(text)
        if match:
            try:
                return float(match.group("percent"))
            except ValueError:
                return None

    if downloaded is not None and downloaded > 0:
        mib = downloaded / (1024.0 * 1024.0)
        return min(95.0, max(1.0, math.log2(mib + 1.0) * 4.0))
    return None


def _format_eta_seconds(value: object) -> str:
    seconds = _progress_number(value)
    if seconds is None or seconds < 0:
        return ""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_speed_bytes_per_second(value: object) -> str:
    speed = _progress_number(value)
    if speed is None or speed <= 0:
        return ""
    return f"{_format_size_human(int(speed))}/s"


def _parse_size_bytes(value: str, unit: str) -> int | None:
    try:
        number = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    normalized_unit = str(unit or "").strip().lower()
    powers = {
        "b": 0,
        "kb": 1,
        "kib": 1,
        "mb": 2,
        "mib": 2,
        "gb": 3,
        "gib": 3,
        "tb": 4,
        "tib": 4,
        "pb": 5,
        "pib": 5,
        "eb": 6,
        "eib": 6,
    }
    power = powers.get(normalized_unit)
    if power is None:
        return None
    return int(number * (1024 ** power))


class _TransferRateEstimator:
    def __init__(self) -> None:
        self._samples: deque[tuple[float, float]] = deque()
        self._last_detail = ("", "")
        self._last_detail_at = 0.0

    def update(self, *, downloaded_bytes: float | None, total_bytes: float | None) -> tuple[str, str]:
        if downloaded_bytes is None or downloaded_bytes < 0:
            return self._last_detail
        now = time.monotonic()
        if self._samples and downloaded_bytes < self._samples[-1][1]:
            self._samples.clear()
            self._last_detail = ("", "")
            self._last_detail_at = 0.0
        if not self._samples or (now - self._samples[-1][0]) >= 0.35 or downloaded_bytes > self._samples[-1][1]:
            self._samples.append((now, float(downloaded_bytes)))
        while len(self._samples) > 2 and (now - self._samples[0][0]) > 12.0:
            self._samples.popleft()
        if (now - self._last_detail_at) < 1.5 and any(self._last_detail):
            return self._last_detail
        if len(self._samples) < 2:
            return self._last_detail
        oldest_time, oldest_bytes = self._samples[0]
        newest_time, newest_bytes = self._samples[-1]
        elapsed = newest_time - oldest_time
        transferred = newest_bytes - oldest_bytes
        if elapsed < 1.0 or transferred <= 0:
            return self._last_detail
        speed = transferred / elapsed
        speed_text = _format_speed_bytes_per_second(speed)
        eta_text = ""
        if total_bytes is not None and total_bytes > newest_bytes and speed > 0:
            eta_text = _format_eta_seconds((total_bytes - newest_bytes) / speed)
        self._last_detail = (eta_text, speed_text)
        self._last_detail_at = now
        return self._last_detail


def _progress_message_from_payload(payload: dict[str, Any], estimator: _TransferRateEstimator | None = None) -> str:
    parts = ["Downloading..."]
    percent_text = sanitize_error_text(payload.get("_percent_str"))
    if percent_text:
        parts.append(percent_text)
    else:
        percent = _progress_percent_from_payload(payload)
        if percent is not None:
            parts.append(f"{max(0.0, min(99.0, percent)):.2f}%")
    downloaded = _progress_number(payload.get("downloaded_bytes"))
    total = _progress_number(payload.get("total_bytes")) or _progress_number(payload.get("total_bytes_estimate"))
    eta = ""
    speed = ""
    if estimator is not None:
        eta, speed = estimator.update(downloaded_bytes=downloaded, total_bytes=total)
    if eta:
        parts.append(f"ETA {eta}")
    if speed:
        parts.append(speed)
    return " | ".join(parts)


def _progress_message_from_download_line(line: str, estimator: _TransferRateEstimator | None = None) -> str:
    clean = sanitize_error_text(line)
    if clean.lower().startswith("[download]"):
        detail = clean[len("[download]") :].strip()
        if detail:
            parts = ["Downloading..."]
            percent_match = _PROGRESS_RE.search(detail)
            total_match = _DOWNLOAD_LINE_TOTAL_RE.search(detail)
            percent = str(percent_match.group("percent") if percent_match else "").strip()
            eta = ""
            speed = ""
            if estimator is not None and percent_match and total_match:
                total = _parse_size_bytes(total_match.group("size"), total_match.group("unit"))
                downloaded = None
                if total is not None:
                    try:
                        downloaded = (float(percent) / 100.0) * float(total)
                    except ValueError:
                        downloaded = None
                eta, speed = estimator.update(downloaded_bytes=downloaded, total_bytes=float(total) if total else None)
            if percent:
                parts.append(f"{percent}%")
            if eta:
                parts.append(f"ETA {eta}")
            if speed:
                parts.append(speed)
            if len(parts) > 1:
                return " | ".join(parts)
            return f"Downloading... | {detail}"
    return clean


def _resolved_node_js_runtime_path() -> str:
    node_path = str(resolve_binary("node") or "").strip()
    if not node_path:
        return ""
    if not is_supported_node_runtime(node_path, minimum_major=NODE_MIN_MAJOR_VERSION):
        return ""
    return node_path


def _yt_dlp_js_runtime_cli_args() -> list[str]:
    node_path = _resolved_node_js_runtime_path()
    if not node_path:
        return []
    return ["--js-runtimes", f"node:{node_path}"]


def _yt_dlp_js_runtime_api_options() -> dict[str, dict[str, str]]:
    node_path = _resolved_node_js_runtime_path()
    if not node_path:
        return {}
    return {"node": {"path": node_path}}


def is_retryable_error(error_text: str) -> bool:
    value = str(error_text or "").strip().lower()
    if not value:
        return False
    if any(token in value for token in _NON_RETRYABLE_ERROR_TOKENS):
        return False
    return any(token in value for token in _RETRYABLE_ERROR_TOKENS)


def _friendly_format_error(job: DownloadJob, error_text: str) -> str:
    value = sanitize_error_text(error_text)
    lowered = value.lower()
    if not any(token in lowered for token in _FORMAT_UNAVAILABLE_ERROR_TOKENS):
        return value
    format_choice = str(job.format_choice or "VIDEO").strip().upper() or "VIDEO"
    quality_choice = str(job.quality_choice or "").strip().upper()
    quality_suffix = ""
    if quality_choice and quality_choice not in {"BEST", "BEST QUALITY"}:
        quality_suffix = f" at {quality_choice}"
    return (
        f"Selected format {format_choice}{quality_suffix} is not available for this URL. "
        "Try BEST QUALITY or VIDEO/MP4."
    )


def normalize_download_state(value: str) -> str:
    candidate = str(value or "").strip().lower()
    try:
        return DownloadState(candidate).value
    except ValueError:
        return DownloadState.ERROR.value


def normalize_retry_profile(value: str) -> str:
    candidate = str(value or "").strip().lower()
    try:
        return RetryProfile(candidate).value
    except ValueError:
        return RetryProfile.BASIC.value


def retry_limit_for_profile(*, retry_count: int, retry_profile: str) -> int:
    configured = max(0, int(retry_count))
    normalized_profile = normalize_retry_profile(retry_profile)
    if normalized_profile == RetryProfile.OFF.value:
        return 0
    if normalized_profile == RetryProfile.AGGRESSIVE.value:
        return max(configured, 5)
    return configured


def retry_backoff_seconds(*, attempt_index: int, retry_profile: str) -> float:
                                           
    attempt = max(1, int(attempt_index))
    normalized_profile = normalize_retry_profile(retry_profile)
    if normalized_profile == RetryProfile.OFF.value:
        return 0.0
    if normalized_profile == RetryProfile.AGGRESSIVE.value:
        base = min(8.0, 0.60 * (2 ** (attempt - 1)))
        jitter = random.uniform(0.0, 0.45)
        return base + jitter
    base = min(2.5, 0.35 * (2 ** (attempt - 1)))
    jitter = random.uniform(0.0, 0.20)
    return base + jitter


class _InProcessCancelled(RuntimeError):
    pass


class _InProcessPaused(RuntimeError):
    pass


class DownloadService:
    def __init__(self) -> None:
        self._active_processes: set[subprocess.Popen[str]] = set()
        self._active_metadata_processes: set[subprocess.Popen[str]] = set()
        self._active_lock = threading.Lock()
        self._control_condition = threading.Condition()
        self._control_change_counter = 0
        self._active_job_processes: dict[str, subprocess.Popen[str]] = {}
        self._paused_job_ids: set[str] = set()
        self._stopped_job_ids: set[str] = set()
        self._batch_lock = threading.Lock()
        self._active_batch_queue: queue.Queue[object] | None = None
        self._active_batch_pending_jobs = 0
        self._active_batch_status_cb: StatusCallback | None = None
        self._active_batch_accepting = False

    def enqueue_batch_job(self, job: DownloadJob) -> bool:
        job_id = str(job.job_id or "").strip()
        if not job_id:
            return False
        with self._batch_lock:
            jobs_queue = self._active_batch_queue
            if (jobs_queue is None) or (not self._active_batch_accepting):
                return False
            self._active_batch_pending_jobs += 1
            jobs_queue.put(job)
            status_cb = self._active_batch_status_cb
        with self._active_lock:
            self._stopped_job_ids.discard(job_id)
            self._paused_job_ids.discard(job_id)
        if status_cb:
            try:
                status_cb(job_id, DownloadState.QUEUED.value)
            except Exception:
                pass
        self._notify_control_changed()
        return True

    def _notify_control_changed(self) -> None:
        with self._control_condition:
            self._control_change_counter += 1
            self._control_condition.notify_all()

    def _wait_for_retry_window(
        self,
        *,
        delay_seconds: float,
        cancel_token: threading.Event,
        job_id: str,
    ) -> str | None:
        deadline = time.time() + max(0.0, float(delay_seconds))
        while True:
            interrupt_state = self._resolve_interrupt_state(job_id, cancel_token)
            if interrupt_state:
                return interrupt_state
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            with self._control_condition:
                self._control_condition.wait(timeout=min(remaining, 0.5))
        return None

    @classmethod
    def _should_use_inprocess_runner(cls) -> bool:
        mode = str(os.environ.get(_YTDLP_MODE_ENV, "")).strip().lower()
        if mode == "subprocess":
            if getattr(sys, "frozen", False) and not cls._can_run_subprocess_runner():
                return True
            return False
        if mode == "inprocess":
            return True
        return bool(getattr(sys, "frozen", False))

    @staticmethod
    def _resolve_explicit_yt_dlp_binary() -> str | None:
        explicit = str(os.environ.get(_YTDLP_BINARY_ENV, "")).strip()
        if not explicit:
            return None
        candidate = Path(explicit).expanduser()
        if candidate.exists():
            return str(candidate)
        return None

    @classmethod
    def _resolve_yt_dlp_subprocess_prefix(cls) -> list[str]:
        explicit_binary = cls._resolve_explicit_yt_dlp_binary()
        if explicit_binary:
            return [explicit_binary]
        if getattr(sys, "frozen", False):
            binary = resolve_binary("yt-dlp")
            if binary:
                return [binary]
            raise FileNotFoundError(
                "yt-dlp executable was not found. Place yt-dlp.exe next to the app or in PATH."
            )
        return [sys.executable, "-m", "yt_dlp"]

    @classmethod
    def _can_run_subprocess_runner(cls) -> bool:
        try:
            cls._resolve_yt_dlp_subprocess_prefix()
            return True
        except Exception:
            return False

    @staticmethod
    def _metadata_worker_command() -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--metadata-worker"]
        script = Path(__file__).resolve().parents[2] / "MediaCrate.py"
        return [sys.executable, str(script), "--metadata-worker"]

    @staticmethod
    def _metadata_subprocess_timeout(timeout_seconds: float | None) -> float:
        if isinstance(timeout_seconds, (int, float)) and float(timeout_seconds) > 0:
            return max(1.0, float(timeout_seconds)) + _METADATA_WORKER_GRACE_SECONDS
        return 30.0

    @staticmethod
    def _metadata_worker_enabled() -> bool:
        if os.environ.get(_METADATA_WORKER_ENV) == "1":
            return False
        mode = str(os.environ.get(_METADATA_MODE_ENV, "subprocess")).strip().lower()
        return mode != "inprocess"

    def _run_metadata_subprocess(
        self,
        action: str,
        url: str,
        *,
        timeout_seconds: float | None = None,
        format_choice: str = "",
        quality_choice: str = "",
        cancel_token: threading.Event | None = None,
    ) -> tuple[bool, object | None, str]:
        command = [
            *self._metadata_worker_command(),
            str(action or "").strip(),
            "--url",
            str(url or "").strip(),
        ]
        if isinstance(timeout_seconds, (int, float)):
            command.extend(["--timeout", str(float(timeout_seconds))])
        if format_choice:
            command.extend(["--format-choice", str(format_choice)])
        if quality_choice:
            command.extend(["--quality-choice", str(quality_choice)])

        env = dict(os.environ)
        env[_METADATA_WORKER_ENV] = "1"
        env.setdefault("PYTHONIOENCODING", "utf-8")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        stderr_stream = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=stderr_stream,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
            )
        except Exception as exc:
            try:
                stderr_stream.close()
            except Exception:
                pass
            return False, None, f"Unable to start metadata worker: {exc}"
        self._register_metadata_process(process)

        try:
            deadline = time.monotonic() + self._metadata_subprocess_timeout(timeout_seconds)
            while process.poll() is None:
                if cancel_token is not None and cancel_token.is_set():
                    self._kill_process_tree(process)
                    try:
                        process.communicate(timeout=1.0)
                    except Exception:
                        pass
                    return False, None, "Metadata worker cancelled."
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill_process_tree(process)
                    try:
                        process.communicate(timeout=1.0)
                    except Exception:
                        pass
                    return False, None, "Metadata worker timed out."
                time.sleep(min(0.1, remaining))

            try:
                stdout, _stderr = process.communicate(timeout=1.0)
            except Exception:
                stdout = ""
            stderr = _read_metadata_stderr_tail(stderr_stream)

            output = str(stdout or "").strip()
            if process.returncode != 0 and not output:
                detail = sanitize_error_text(stderr or f"Metadata worker exited with {process.returncode}")
                return False, None, detail
            try:
                envelope = json.loads(output)
            except json.JSONDecodeError:
                detail = sanitize_error_text(stderr or output or "Metadata worker returned invalid JSON.")
                return False, None, detail or "Metadata worker returned invalid JSON."
            if not isinstance(envelope, dict):
                return False, None, "Metadata worker returned an invalid response."
            ok = bool(envelope.get("ok"))
            result = envelope.get("result")
            error = sanitize_error_text(envelope.get("error") or stderr or "")
            return ok, result, error
        finally:
            self._unregister_metadata_process(process)
            try:
                stderr_stream.close()
            except Exception:
                pass

    @staticmethod
    def _should_fallback_from_inprocess(error_text: str) -> bool:
        cleaned = sanitize_error_text(error_text).lower()
        if not cleaned:
            return False
        if "no module named" in cleaned and "yt_dlp" in cleaned:
            return True
        if "yt-dlp import failed" in cleaned:
            return True
        return False

    def probe_formats_cancellable(
        self,
        url: str,
        *,
        timeout_seconds: float | None = None,
        cancel_token: threading.Event | None = None,
    ) -> FormatProbeResult:
        if not self._metadata_worker_enabled():
            return self._probe_formats_inprocess(url, timeout_seconds=timeout_seconds)
        value = coerce_http_url(url)
        if not validate_url(value):
            return FormatProbeResult(title="", formats=_default_format_choices(), qualities=["BEST QUALITY"], error="Invalid URL")
        ok, payload, error = self._run_metadata_subprocess(
            "probe",
            value,
            timeout_seconds=timeout_seconds,
            cancel_token=cancel_token,
        )
        if not ok:
            return FormatProbeResult(
                title="",
                formats=_default_format_choices(),
                qualities=["BEST QUALITY"],
                error=error or "Metadata probe failed.",
            )
        return _format_probe_from_payload(payload)

    def analyze_url_cancellable(
        self,
        url: str,
        *,
        timeout_seconds: float | None = None,
        cancel_token: threading.Event | None = None,
    ) -> UrlAnalysisResult:
        value = coerce_http_url(url)
        normalized = normalize_batch_url(value)
        if not validate_url(value):
            return UrlAnalysisResult(
                url_raw=value,
                url_normalized=normalized,
                is_valid=False,
                formats=_default_format_choices(),
                qualities=["BEST QUALITY"],
                error="Invalid URL",
            )
        if not self._metadata_worker_enabled():
            return self._analyze_url_inprocess(value, timeout_seconds=timeout_seconds)
        ok, payload, error = self._run_metadata_subprocess(
            "analyze",
            value,
            timeout_seconds=timeout_seconds,
            cancel_token=cancel_token,
        )
        if not ok:
            return UrlAnalysisResult(
                url_raw=value,
                url_normalized=normalized,
                is_valid=False,
                formats=_default_format_choices(),
                qualities=["BEST QUALITY"],
                error=error or "Metadata analysis failed.",
            )
        return _url_analysis_from_payload(payload, url=value)

    def resolve_selection_size_bytes_cancellable(
        self,
        url: str,
        format_choice: str,
        quality_choice: str,
        *,
        timeout_seconds: float | None = None,
        cancel_token: threading.Event | None = None,
    ) -> int | None:
        if not self._metadata_worker_enabled():
            return self._resolve_selection_size_bytes_inprocess(
                url,
                format_choice,
                quality_choice,
                timeout_seconds=timeout_seconds,
            )
        value = coerce_http_url(url)
        if not validate_url(value):
            return None
        ok, payload, _error = self._run_metadata_subprocess(
            "selection-size",
            value,
            timeout_seconds=timeout_seconds,
            format_choice=format_choice,
            quality_choice=quality_choice,
            cancel_token=cancel_token,
        )
        if not ok or not isinstance(payload, int):
            return None
        return int(payload) if payload > 0 else None

    def probe_formats(self, url: str, *, timeout_seconds: float | None = None) -> FormatProbeResult:
        return self.probe_formats_cancellable(url, timeout_seconds=timeout_seconds)

    def _probe_formats_inprocess(self, url: str, *, timeout_seconds: float | None = None) -> FormatProbeResult:
        value = coerce_http_url(url)
        default_formats = _default_format_choices()
        if not validate_url(value):
            return FormatProbeResult(
                title="",
                formats=default_formats,
                qualities=["BEST QUALITY"],
                error="Invalid URL",
            )
        try:
            from yt_dlp import YoutubeDL
        except Exception as exc:
            return FormatProbeResult(
                title="",
                formats=default_formats,
                qualities=["BEST QUALITY"],
                error=f"yt-dlp import failed: {exc}",
            )

        try:
            opts = _metadata_extract_options(timeout_seconds)
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(value, download=False)
        except Exception as exc:
            return FormatProbeResult(
                title="",
                formats=default_formats,
                qualities=["BEST QUALITY"],
                error=sanitize_error_text(exc),
            )

        info_dict = info if isinstance(info, dict) else {}
        qualities, other_formats = _collect_format_inventory(info_dict)
        return FormatProbeResult(
            title=str(info_dict.get("title") or ""),
            formats=default_formats,
            other_formats=other_formats,
            qualities=qualities,
            error="",
        )

    def analyze_url(self, url: str, *, timeout_seconds: float | None = None) -> UrlAnalysisResult:
        return self.analyze_url_cancellable(url, timeout_seconds=timeout_seconds)

    def _analyze_url_inprocess(self, url: str, *, timeout_seconds: float | None = None) -> UrlAnalysisResult:
        value = coerce_http_url(url)
        normalized = normalize_batch_url(value)
        default_formats = _default_format_choices()
        if not validate_url(value):
            return UrlAnalysisResult(
                url_raw=value,
                url_normalized=normalized,
                is_valid=False,
                formats=default_formats,
                qualities=["BEST QUALITY"],
                error="Invalid URL",
            )

        try:
            from yt_dlp import YoutubeDL
        except Exception as exc:
            return UrlAnalysisResult(
                url_raw=value,
                url_normalized=normalized,
                is_valid=False,
                formats=default_formats,
                qualities=["BEST QUALITY"],
                error=f"yt-dlp import failed: {exc}",
            )

        try:
            opts = _metadata_extract_options(timeout_seconds)
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(value, download=False)
        except Exception as exc:
            return UrlAnalysisResult(
                url_raw=value,
                url_normalized=normalized,
                is_valid=False,
                formats=default_formats,
                qualities=["BEST QUALITY"],
                error=sanitize_error_text(exc),
            )

        info_dict = info if isinstance(info, dict) else {}
        qualities, other_formats = _collect_format_inventory(info_dict)
        merged_formats = _merge_unique_formats(default_formats, other_formats)
        expected_size = _extract_expected_size_bytes(info_dict)
        selection_estimates = _build_selection_size_estimates(
            info_dict,
            formats=merged_formats,
            qualities=qualities,
        )
        duration_seconds = _extract_duration_seconds(info_dict)
        source_label = _extract_source_label(info_dict, value)
        return UrlAnalysisResult(
            url_raw=value,
            url_normalized=normalized,
            is_valid=True,
            title=str(info_dict.get("title") or ""),
            thumbnail_url=str(info_dict.get("thumbnail") or ""),
            expected_size_bytes=expected_size,
            duration_seconds=duration_seconds,
            source_label=source_label,
            formats=merged_formats,
            qualities=qualities,
            selection_size_estimates=selection_estimates,
            error="",
        )

    def resolve_selection_size_bytes(
        self,
        url: str,
        format_choice: str,
        quality_choice: str,
        *,
        timeout_seconds: float | None = None,
    ) -> int | None:
        return self.resolve_selection_size_bytes_cancellable(
            url,
            format_choice,
            quality_choice,
            timeout_seconds=timeout_seconds,
        )

    def _resolve_selection_size_bytes_inprocess(
        self,
        url: str,
        format_choice: str,
        quality_choice: str,
        *,
        timeout_seconds: float | None = None,
    ) -> int | None:
        value = coerce_http_url(url)
        if not validate_url(value):
            return None
        try:
            from yt_dlp import YoutubeDL
        except Exception:
            return None

        selector, _post_args = _format_selector(format_choice, quality_choice)
        opts = _metadata_extract_options(timeout_seconds)
        opts["format"] = selector
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(value, download=False)
        except Exception:
            return None

        info_dict = info if isinstance(info, dict) else {}
        duration_seconds = _extract_duration_seconds(info_dict)
        choice = str(format_choice or FormatChoice.VIDEO.value).strip().upper() or FormatChoice.VIDEO.value
        requested_items = _requested_format_items(info_dict)
        if requested_items:
            selection_size = _sum_size_from_format_items(
                requested_items,
                duration_seconds=duration_seconds,
            )
        else:
            selection_size = None

        if is_audio_format_choice(choice):
            formats_raw = info_dict.get("formats")
            full_formats = formats_raw if isinstance(formats_raw, list) else []
            enriched = _enrich_format_sizes(requested_items or full_formats, duration_seconds=duration_seconds)
            if choice == FormatChoice.MP3.value:
                return _estimate_mp3_size(enriched, duration_seconds=duration_seconds)
            if selection_size is not None:
                return int(selection_size)
            return _best_audio_size(enriched)

        if selection_size is not None:
            return int(selection_size)
        return None

    def _build_command(
        self,
        job: DownloadJob,
        *,
        skip_existing_files: bool = False,
        filename_template: str = _DEFAULT_OUTPUT_TEMPLATE,
        conflict_policy: str = "skip",
        rename_number: int = 0,
        save_metadata_to_file: bool = False,
        speed_limit_kbps: int = 0,
    ) -> tuple[list[str], str]:
        output_dir = Path(job.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        selector, post_args = _format_selector(job.format_choice, job.quality_choice)
        output_template = _resolve_output_template(
            output_dir=output_dir,
            filename_template=filename_template,
            job=job,
            conflict_policy=conflict_policy,
        )
        normalized_conflict_policy = normalize_conflict_policy(conflict_policy)
        if normalized_conflict_policy == "rename" and int(rename_number) > 0:
            output_template = _with_rename_number(output_template, int(rename_number))
        command = [
            *self._resolve_yt_dlp_subprocess_prefix(),
            "--newline",
            "--progress",
            "--no-playlist",
            "--no-warnings",
            "-f",
            selector,
            "-o",
            output_template,
            "--print",
            "after_move:filepath",
        ]

        ffmpeg_path = resolve_binary("ffmpeg")
        choice = str(job.format_choice or "").strip().upper()
        needs_conversion = (
            (is_audio_format_choice(choice) and choice != FormatChoice.AUDIO.value)
            or choice in CONVERSION_CONTAINER_CHOICES
        )
        if needs_conversion and not ffmpeg_path:
            raise FileNotFoundError(
                "ffmpeg is required for the selected format conversion. Install ffmpeg and retry."
            )
        if save_metadata_to_file and not ffmpeg_path:
            raise FileNotFoundError(
                "ffmpeg is required to embed metadata into the downloaded file. Install ffmpeg and retry."
            )
        if ffmpeg_path:
            command.extend(["--ffmpeg-location", ffmpeg_path])
        command.extend(_yt_dlp_js_runtime_cli_args())

        if normalized_conflict_policy in {"skip", "rename"}:
            command.append("--no-overwrites")
        elif normalized_conflict_policy == "overwrite":
            command.append("--force-overwrites")

        if skip_existing_files and normalized_conflict_policy != "rename":
                                                                                    
                                                                                      
            if "--force-overwrites" in command:
                command = [item for item in command if item != "--force-overwrites"]
            if "--no-overwrites" not in command:
                command.append("--no-overwrites")

        rate_limit = max(0, int(speed_limit_kbps))
        if rate_limit > 0:
            command.extend(["--limit-rate", f"{rate_limit}K"])

        if save_metadata_to_file:
            command.append("--add-metadata")

        command.extend(post_args)
        command.append(coerce_http_url(job.url))
        return command, output_template

    @staticmethod
    def _kill_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                process.terminate()
                process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _register_process(self, process: subprocess.Popen[str], job_id: str) -> None:
        with self._active_lock:
            self._active_processes.add(process)
            self._active_job_processes[str(job_id or "").strip()] = process

    def _unregister_process(self, process: subprocess.Popen[str], job_id: str) -> None:
        with self._active_lock:
            self._active_processes.discard(process)
            self._active_job_processes.pop(str(job_id or "").strip(), None)

    def _register_metadata_process(self, process: subprocess.Popen[str]) -> None:
        with self._active_lock:
            self._active_metadata_processes.add(process)

    def _unregister_metadata_process(self, process: subprocess.Popen[str]) -> None:
        with self._active_lock:
            self._active_metadata_processes.discard(process)

    def pause_job(self, job_id: str) -> None:
        key = str(job_id or "").strip()
        if not key:
            return
        with self._active_lock:
            self._paused_job_ids.add(key)
            process = self._active_job_processes.get(key)
        if process is not None:
            self._kill_process_tree(process)
        self._notify_control_changed()

    def resume_job(self, job_id: str) -> None:
        key = str(job_id or "").strip()
        if not key:
            return
        with self._active_lock:
            self._paused_job_ids.discard(key)
        self._notify_control_changed()

    def stop_job(self, job_id: str) -> None:
        key = str(job_id or "").strip()
        if not key:
            return
        with self._active_lock:
            self._stopped_job_ids.add(key)
            self._paused_job_ids.discard(key)
            process = self._active_job_processes.get(key)
        if process is not None:
            self._kill_process_tree(process)
        self._notify_control_changed()

    def _is_job_paused(self, job_id: str) -> bool:
        key = str(job_id or "").strip()
        if not key:
            return False
        with self._active_lock:
            return key in self._paused_job_ids

    def _is_job_stopped(self, job_id: str) -> bool:
        key = str(job_id or "").strip()
        if not key:
            return False
        with self._active_lock:
            return key in self._stopped_job_ids

    def cancel_all(self) -> None:
        with self._active_lock:
            running = list(self._active_processes)
            self._active_processes.clear()
            self._active_job_processes.clear()
            self._paused_job_ids.clear()
            self._stopped_job_ids.clear()
        for process in running:
            self._kill_process_tree(process)
        self._notify_control_changed()

    def cancel_metadata_workers(self) -> None:
        with self._active_lock:
            running = list(self._active_metadata_processes)
            self._active_metadata_processes.clear()
        for process in running:
            self._kill_process_tree(process)
        self._notify_control_changed()

    def _resolve_interrupt_state(self, job_id: str, cancel_token: threading.Event) -> str | None:
        if cancel_token.is_set() or self._is_job_stopped(job_id):
            return DownloadState.CANCELLED.value
        if self._is_job_paused(job_id):
            return DownloadState.PAUSED.value
        return None

    @staticmethod
    def _make_result(
        job: DownloadJob,
        *,
        state: str,
        output_path: str = "",
        error: str = "",
    ) -> DownloadResult:
        return DownloadResult(
            job_id=job.job_id,
            url=job.url,
            state=normalize_download_state(state),
            output_path=str(output_path or ""),
            error=sanitize_error_text(error),
        )

    @staticmethod
    def _parse_output_path_from_line(clean_line: str) -> str:
        if "Destination:" in clean_line:
            return clean_line.split("Destination:", 1)[1].strip()
        if "Merging formats into" in clean_line:
            return clean_line.split("Merging formats into", 1)[1].strip().strip('"')
        candidate = str(clean_line or "").strip().strip('"')
        lowered = candidate.lower()
        if (
            candidate
            and not lowered.startswith(("http://", "https://"))
            and not lowered.startswith(("[", "error:", "warning:"))
        ):
            try:
                path = Path(candidate)
                suffix = str(path.suffix or "")
                if (
                    suffix
                    and re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix)
                    and (path.is_absolute() or "\\" in candidate or "/" in candidate)
                ):
                    return candidate
            except Exception:
                return ""
        return ""

    def _consume_download_line(
        self,
        line: object,
        *,
        log_cb: LogCallback | None,
        last_error: str,
        output_path: str,
        saw_already_downloaded: bool,
    ) -> tuple[str, str, bool, str]:
        clean = sanitize_error_text(line)
        if not clean:
            return last_error, output_path, saw_already_downloaded, ""
        if log_cb and _is_important_log_line(clean):
            log_cb(clean)
        if "has already been downloaded" in clean.lower():
            saw_already_downloaded = True
        candidate_path = self._parse_output_path_from_line(clean)
        if candidate_path:
            output_path = candidate_path
            record_partial_candidates(output_path)
            return last_error, output_path, saw_already_downloaded, clean
        last_error = clean
        return last_error, output_path, saw_already_downloaded, clean

    def _resolve_terminal_result(
        self,
        job: DownloadJob,
        *,
        return_code: int,
        progress_cb: SingleProgressCallback | None,
        saw_already_downloaded: bool,
        output_path: str,
        last_error: str,
    ) -> DownloadResult:
        if return_code == 0:
            if progress_cb:
                progress_cb(100.0, "Done")
            discard_partial_candidates(output_path)
            if saw_already_downloaded:
                return self._make_result(job, state=DownloadState.SKIPPED.value, output_path=output_path)
            return self._make_result(job, state=DownloadState.DONE.value, output_path=output_path)
        return self._make_result(
            job,
            state=DownloadState.ERROR.value,
            output_path=output_path,
            error=_friendly_format_error(job, last_error or f"yt-dlp exited with {return_code}"),
        )

    @staticmethod
    def _is_rename_collision_result(result: DownloadResult) -> bool:
        if result.state == DownloadState.SKIPPED.value:
            return True
        error = sanitize_error_text(result.error).lower()
        return bool(error and ("already been downloaded" in error or "already exists" in error))

    def _run_with_rename_retries(
        self,
        *,
        job: DownloadJob,
        cancel_token: threading.Event,
        log_cb: LogCallback | None,
        run_attempt: Callable[[int], DownloadResult],
    ) -> DownloadResult:
        last_result: DownloadResult | None = None
        for rename_number in range(0, 1000):
            interrupt_state = self._resolve_interrupt_state(job.job_id, cancel_token)
            if interrupt_state:
                return self._make_result(job, state=interrupt_state)
            result = run_attempt(rename_number)
            last_result = result
            if result.state in {DownloadState.CANCELLED.value, DownloadState.PAUSED.value}:
                return result
            if not self._is_rename_collision_result(result):
                return result
            if log_cb:
                label = "original name" if rename_number == 0 else f"number {rename_number:03d}"
                log_cb(f"Rename policy: {label} already exists; trying next number.")
        return last_result or self._make_result(
            job,
            state=DownloadState.ERROR.value,
            error="Rename policy could not find an available numbered filename.",
        )

    def _run_single_subprocess_once(
        self,
        job: DownloadJob,
        cancel_token: threading.Event,
        *,
        progress_cb: SingleProgressCallback | None = None,
        log_cb: LogCallback | None = None,
        skip_existing_files: bool = False,
        filename_template: str = _DEFAULT_OUTPUT_TEMPLATE,
        conflict_policy: str = "skip",
        rename_number: int = 0,
        save_metadata_to_file: bool = False,
        speed_limit_kbps: int = 0,
    ) -> DownloadResult:
        output_template_for_part_tracking = ""
        normalized_conflict_policy = normalize_conflict_policy(conflict_policy)
        force_no_overwrite = normalized_conflict_policy == "rename"
        try:
            command, output_template_for_part_tracking = self._build_command(
                job,
                skip_existing_files=skip_existing_files or force_no_overwrite,
                filename_template=filename_template,
                conflict_policy=conflict_policy,
                rename_number=rename_number,
                save_metadata_to_file=save_metadata_to_file,
                speed_limit_kbps=speed_limit_kbps,
            )
        except Exception as exc:
            return self._make_result(job, state=DownloadState.ERROR.value, error=str(exc))
        record_partial_candidates(output_template_for_part_tracking)

        output_path = ""
        last_error = ""
        saw_already_downloaded = False
        post_processing_notified = False
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            discard_partial_candidates(output_template_for_part_tracking)
            return self._make_result(job, state=DownloadState.ERROR.value, error=str(exc))
        self._register_process(process, job.job_id)

        try:
            stream = process.stdout
            if stream is None:
                return self._make_result(job, state=DownloadState.ERROR.value, error="No output stream")

            progress_estimator = _TransferRateEstimator()
            for line in iter(stream.readline, ""):
                interrupt_state = self._resolve_interrupt_state(job.job_id, cancel_token)
                if interrupt_state == DownloadState.CANCELLED.value:
                    self._kill_process_tree(process)
                    return self._make_result(job, state=DownloadState.CANCELLED.value, output_path=output_path)
                if interrupt_state == DownloadState.PAUSED.value:
                    self._kill_process_tree(process)
                    return self._make_result(job, state=DownloadState.PAUSED.value, output_path=output_path)

                last_error, output_path, saw_already_downloaded, clean = self._consume_download_line(
                    line,
                    log_cb=log_cb,
                    last_error=last_error,
                    output_path=output_path,
                    saw_already_downloaded=saw_already_downloaded,
                )
                if (not post_processing_notified) and _is_post_processing_line(clean):
                    post_processing_notified = True
                    if progress_cb:
                        progress_cb(99.0, "Post-processing...")
                match = _PROGRESS_RE.search(clean)
                if match and progress_cb:
                    try:
                        percent = float(match.group("percent"))
                    except ValueError:
                        percent = 0.0
                    progress_cb(
                        max(0.0, min(99.0, percent)),
                        _progress_message_from_download_line(clean, progress_estimator),
                    )

            return_code = process.wait()
            interrupt_state = self._resolve_interrupt_state(job.job_id, cancel_token)
            if interrupt_state:
                return self._make_result(job, state=interrupt_state, output_path=output_path)
            return self._resolve_terminal_result(
                job,
                return_code=return_code,
                progress_cb=progress_cb,
                saw_already_downloaded=saw_already_downloaded,
                output_path=output_path,
                last_error=last_error,
            )
        finally:
            self._unregister_process(process, job.job_id)
            if output_template_for_part_tracking and process.poll() == 0:
                discard_partial_candidates(output_template_for_part_tracking)
            try:
                if process.stdout:
                    process.stdout.close()
            except Exception:
                pass

    def _run_single_subprocess(
        self,
        job: DownloadJob,
        cancel_token: threading.Event,
        *,
        progress_cb: SingleProgressCallback | None = None,
        log_cb: LogCallback | None = None,
        skip_existing_files: bool = False,
        filename_template: str = _DEFAULT_OUTPUT_TEMPLATE,
        conflict_policy: str = "skip",
        save_metadata_to_file: bool = False,
        speed_limit_kbps: int = 0,
    ) -> DownloadResult:
        normalized_conflict_policy = normalize_conflict_policy(conflict_policy)
        if normalized_conflict_policy != "rename":
            return self._run_single_subprocess_once(
                job,
                cancel_token,
                progress_cb=progress_cb,
                log_cb=log_cb,
                skip_existing_files=skip_existing_files,
                filename_template=filename_template,
                conflict_policy=conflict_policy,
                save_metadata_to_file=save_metadata_to_file,
                speed_limit_kbps=speed_limit_kbps,
            )
        return self._run_with_rename_retries(
            job=job,
            cancel_token=cancel_token,
            log_cb=log_cb,
            run_attempt=lambda rename_number: self._run_single_subprocess_once(
                job,
                cancel_token,
                progress_cb=progress_cb,
                log_cb=log_cb,
                skip_existing_files=True,
                filename_template=filename_template,
                conflict_policy=conflict_policy,
                rename_number=rename_number,
                save_metadata_to_file=save_metadata_to_file,
                speed_limit_kbps=speed_limit_kbps,
            ),
        )

    def _run_single_inprocess_once(
        self,
        job: DownloadJob,
        cancel_token: threading.Event,
        *,
        progress_cb: SingleProgressCallback | None = None,
        log_cb: LogCallback | None = None,
        skip_existing_files: bool = False,
        filename_template: str = _DEFAULT_OUTPUT_TEMPLATE,
        conflict_policy: str = "skip",
        rename_number: int = 0,
        save_metadata_to_file: bool = False,
        speed_limit_kbps: int = 0,
    ) -> DownloadResult:
        try:
            from yt_dlp import YoutubeDL
        except Exception as exc:
            return self._make_result(job, state=DownloadState.ERROR.value, error=str(exc))

        output_dir = Path(job.output_dir).expanduser().resolve()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return self._make_result(job, state=DownloadState.ERROR.value, error=str(exc))

        selector, _post_args = _format_selector(job.format_choice, job.quality_choice)
        output_template = _resolve_output_template(
            output_dir=output_dir,
            filename_template=filename_template,
            job=job,
            conflict_policy=conflict_policy,
        )
        normalized_conflict_policy = normalize_conflict_policy(conflict_policy)
        if normalized_conflict_policy == "rename" and int(rename_number) > 0:
            output_template = _with_rename_number(output_template, int(rename_number))
        record_partial_candidates(output_template)
        rate_limit = max(0, int(speed_limit_kbps))

        output_path = ""
        last_error = ""
        saw_already_downloaded = False

        def capture_output_candidate(payload: object) -> None:
            nonlocal output_path
            if not isinstance(payload, dict):
                return
            for key in ("filename", "filepath", "tmpfilename", "_filename"):
                value = str(payload.get(key) or "").strip()
                if value:
                    output_path = value
                    record_partial_candidates(output_path)
                    break

        def consume_message(message: object) -> str:
            nonlocal last_error, saw_already_downloaded, output_path
            last_error, output_path, saw_already_downloaded, clean = self._consume_download_line(
                message,
                log_cb=log_cb,
                last_error=last_error,
                output_path=output_path,
                saw_already_downloaded=saw_already_downloaded,
            )
            return clean

        class _YdlLogger:
            def __init__(self) -> None:
                self.last_error_text = ""

            def _consume(self, message: object) -> None:
                consume_message(message)

            def debug(self, message: object) -> None:
                self._consume(message)

            def warning(self, message: object) -> None:
                self._consume(message)

            def error(self, message: object) -> None:
                clean = consume_message(message)
                if clean:
                    self.last_error_text = clean

        logger = _YdlLogger()
        progress_estimator = _TransferRateEstimator()

        def progress_hook(payload: dict[str, Any]) -> None:
            interrupt_state = self._resolve_interrupt_state(job.job_id, cancel_token)
            if interrupt_state == DownloadState.CANCELLED.value:
                raise _InProcessCancelled(_INPROCESS_CANCELLED_SENTINEL)
            if interrupt_state == DownloadState.PAUSED.value:
                raise _InProcessPaused(_INPROCESS_PAUSED_SENTINEL)

            capture_output_candidate(payload)
            status = str(payload.get("status") or "").strip().lower()
            if status == "finished":
                if progress_cb:
                    progress_cb(99.0, "Post-processing...")
                return
            if status != "downloading" or not progress_cb:
                return

            percent = _progress_percent_from_payload(payload)
            line = _progress_message_from_payload(payload, progress_estimator)
            progress_cb(max(0.0, min(99.0, percent or 0.0)), line)

        ydl_opts: dict[str, Any] = {
            "newline": True,
            "noplaylist": True,
            "no_warnings": True,
            "quiet": True,
            "format": selector,
            "outtmpl": output_template,
            "progress_hooks": [progress_hook],
            "logger": logger,
        }

        ffmpeg_path = resolve_binary("ffmpeg")
        choice = str(job.format_choice or "").strip().upper()
        needs_conversion = (
            (is_audio_format_choice(choice) and choice != FormatChoice.AUDIO.value)
            or choice in CONVERSION_CONTAINER_CHOICES
        )
        if needs_conversion and not ffmpeg_path:
            return self._make_result(
                job,
                state=DownloadState.ERROR.value,
                error="ffmpeg is required for the selected format conversion. Install ffmpeg and retry.",
            )
        if save_metadata_to_file and not ffmpeg_path:
            return self._make_result(
                job,
                state=DownloadState.ERROR.value,
                error="ffmpeg is required to embed metadata into the downloaded file. Install ffmpeg and retry.",
            )
        if ffmpeg_path:
            ydl_opts["ffmpeg_location"] = ffmpeg_path
        js_runtimes = _yt_dlp_js_runtime_api_options()
        if js_runtimes:
            ydl_opts["js_runtimes"] = js_runtimes

        if normalized_conflict_policy == "overwrite":
            ydl_opts["overwrites"] = True
        if normalized_conflict_policy in {"skip", "rename"} or (skip_existing_files and normalized_conflict_policy != "rename"):
            ydl_opts["nooverwrites"] = True
            ydl_opts.pop("overwrites", None)

        if rate_limit > 0:
            ydl_opts["ratelimit"] = rate_limit * 1024

        postprocessors: list[dict[str, Any]] = []
        if choice == FormatChoice.MP3.value:
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }
            )
        if save_metadata_to_file:
            postprocessors.append({"key": "FFmpegMetadata"})
        if postprocessors:
            ydl_opts["postprocessors"] = postprocessors

        if choice == FormatChoice.MP4.value:
            ydl_opts["merge_output_format"] = "mp4"
        elif choice in CONVERSION_CONTAINER_CHOICES:
            ydl_opts["merge_output_format"] = choice.lower()

        try:
            with YoutubeDL(ydl_opts) as ydl:
                result_code = int(ydl.download([coerce_http_url(job.url)]))
        except _InProcessCancelled:
            return self._make_result(job, state=DownloadState.CANCELLED.value, output_path=output_path)
        except _InProcessPaused:
            return self._make_result(job, state=DownloadState.PAUSED.value, output_path=output_path)
        except Exception as exc:
            error_text = sanitize_error_text(exc)
            if _INPROCESS_CANCELLED_SENTINEL in error_text:
                return self._make_result(job, state=DownloadState.CANCELLED.value, output_path=output_path)
            if _INPROCESS_PAUSED_SENTINEL in error_text:
                return self._make_result(job, state=DownloadState.PAUSED.value, output_path=output_path)
            fallback_error = logger.last_error_text or last_error or error_text
            return self._make_result(
                job,
                state=DownloadState.ERROR.value,
                output_path=output_path,
                error=_friendly_format_error(job, fallback_error),
            )

        interrupt_state = self._resolve_interrupt_state(job.job_id, cancel_token)
        if interrupt_state:
            return self._make_result(job, state=interrupt_state, output_path=output_path)

        fallback_error = logger.last_error_text or last_error or f"yt-dlp exited with {result_code}"
        result = self._resolve_terminal_result(
            job,
            return_code=result_code,
            progress_cb=progress_cb,
            saw_already_downloaded=saw_already_downloaded,
            output_path=output_path,
            last_error=fallback_error,
        )
        if result.state in {DownloadState.DONE.value, DownloadState.SKIPPED.value}:
            discard_partial_candidates(output_template)
        return result

    def _run_single_inprocess(
        self,
        job: DownloadJob,
        cancel_token: threading.Event,
        *,
        progress_cb: SingleProgressCallback | None = None,
        log_cb: LogCallback | None = None,
        skip_existing_files: bool = False,
        filename_template: str = _DEFAULT_OUTPUT_TEMPLATE,
        conflict_policy: str = "skip",
        save_metadata_to_file: bool = False,
        speed_limit_kbps: int = 0,
    ) -> DownloadResult:
        normalized_conflict_policy = normalize_conflict_policy(conflict_policy)
        if normalized_conflict_policy != "rename":
            return self._run_single_inprocess_once(
                job,
                cancel_token,
                progress_cb=progress_cb,
                log_cb=log_cb,
                skip_existing_files=skip_existing_files,
                filename_template=filename_template,
                conflict_policy=conflict_policy,
                save_metadata_to_file=save_metadata_to_file,
                speed_limit_kbps=speed_limit_kbps,
            )
        return self._run_with_rename_retries(
            job=job,
            cancel_token=cancel_token,
            log_cb=log_cb,
            run_attempt=lambda rename_number: self._run_single_inprocess_once(
                job,
                cancel_token,
                progress_cb=progress_cb,
                log_cb=log_cb,
                skip_existing_files=True,
                filename_template=filename_template,
                conflict_policy=conflict_policy,
                rename_number=rename_number,
                save_metadata_to_file=save_metadata_to_file,
                speed_limit_kbps=speed_limit_kbps,
            ),
        )

    def run_single(
        self,
        job: DownloadJob,
        cancel_token: threading.Event,
        *,
        progress_cb: SingleProgressCallback | None = None,
        log_cb: LogCallback | None = None,
        skip_existing_files: bool = False,
        filename_template: str = _DEFAULT_OUTPUT_TEMPLATE,
        conflict_policy: str = "skip",
        save_metadata_to_file: bool = False,
        speed_limit_kbps: int = 0,
    ) -> DownloadResult:
        interrupt_state = self._resolve_interrupt_state(job.job_id, cancel_token)
        if interrupt_state:
            return self._make_result(job, state=interrupt_state)

        prefer_inprocess = self._should_use_inprocess_runner()
        if prefer_inprocess:
            result = self._run_single_inprocess(
                job,
                cancel_token,
                progress_cb=progress_cb,
                log_cb=log_cb,
                skip_existing_files=skip_existing_files,
                filename_template=filename_template,
                conflict_policy=conflict_policy,
                save_metadata_to_file=save_metadata_to_file,
                speed_limit_kbps=speed_limit_kbps,
            )
            if (
                result.state == DownloadState.ERROR.value
                and self._should_fallback_from_inprocess(result.error)
                and self._can_run_subprocess_runner()
            ):
                return self._run_single_subprocess(
                    job,
                    cancel_token,
                    progress_cb=progress_cb,
                    log_cb=log_cb,
                    skip_existing_files=skip_existing_files,
                    filename_template=filename_template,
                    conflict_policy=conflict_policy,
                    save_metadata_to_file=save_metadata_to_file,
                    speed_limit_kbps=speed_limit_kbps,
                )
            return result
        return self._run_single_subprocess(
            job,
            cancel_token,
            progress_cb=progress_cb,
            log_cb=log_cb,
            skip_existing_files=skip_existing_files,
            filename_template=filename_template,
            conflict_policy=conflict_policy,
            save_metadata_to_file=save_metadata_to_file,
            speed_limit_kbps=speed_limit_kbps,
        )

    @staticmethod
    def _batch_take_next_item(
        jobs_queue: queue.Queue[object],
        *,
        stop_sentinel: object,
    ) -> tuple[DownloadJob | None, bool]:
        current_item = jobs_queue.get()
        if current_item is stop_sentinel:
            jobs_queue.task_done()
            return None, True
        if not isinstance(current_item, DownloadJob):
            jobs_queue.task_done()
            return None, False
        return current_item, False

    def _batch_interrupt_or_requeue(
        self,
        *,
        job: DownloadJob,
        cancel_token: threading.Event,
        status_cb: StatusCallback | None,
        jobs_queue: queue.Queue[object],
    ) -> tuple[DownloadResult | None, bool]:
        interrupt_state = self._resolve_interrupt_state(job.job_id, cancel_token)
        if interrupt_state == DownloadState.CANCELLED.value:
            return (
                DownloadResult(
                    job_id=job.job_id,
                    url=job.url,
                    state=DownloadState.CANCELLED.value,
                ),
                True,
            )
        if interrupt_state == DownloadState.PAUSED.value:
            if status_cb:
                status_cb(job.job_id, DownloadState.PAUSED.value)
            jobs_queue.put(job)
            return (
                DownloadResult(
                    job_id=job.job_id,
                    url=job.url,
                    state=DownloadState.PAUSED.value,
                ),
                False,
            )
        return None, True

    def _batch_run_single_attempt(
        self,
        *,
        job: DownloadJob,
        cancel_token: threading.Event,
        progress_cb: BatchProgressCallback | None,
        log_cb: LogCallback | None,
        skip_existing_files: bool,
        filename_template: str,
        conflict_policy: str,
        save_metadata_to_file: bool,
        speed_limit_kbps: int,
    ) -> DownloadResult:
        return self.run_single(
            job,
            cancel_token,
            progress_cb=(
                (
                    lambda percent, message, jid=job.job_id: progress_cb(
                        jid,
                        percent,
                        message,
                    )
                )
                if progress_cb
                else None
            ),
            log_cb=((lambda message, jid=job.job_id: log_cb(f"[{jid}] {message}")) if log_cb else None),
            skip_existing_files=skip_existing_files,
            filename_template=filename_template,
            conflict_policy=conflict_policy,
            save_metadata_to_file=save_metadata_to_file,
            speed_limit_kbps=speed_limit_kbps,
        )

    def _batch_run_with_retries(
        self,
        *,
        job: DownloadJob,
        jobs_queue: queue.Queue[object],
        cancel_token: threading.Event,
        progress_cb: BatchProgressCallback | None,
        status_cb: StatusCallback | None,
        log_cb: LogCallback | None,
        max_retries: int,
        normalized_retry_profile: str,
        skip_existing_files: bool,
        filename_template: str,
        conflict_policy: str,
        save_metadata_to_file: bool,
        speed_limit_kbps: int,
    ) -> tuple[DownloadResult, bool, int]:
        result = DownloadResult(job_id=job.job_id, url=job.url, state=DownloadState.ERROR.value)
        attempt = 0
        retried_attempts_increment = 0
        while True:
            interrupt_result, count_as_complete = self._batch_interrupt_or_requeue(
                job=job,
                cancel_token=cancel_token,
                status_cb=status_cb,
                jobs_queue=jobs_queue,
            )
            if interrupt_result is not None:
                return interrupt_result, count_as_complete, retried_attempts_increment
            if status_cb:
                status_cb(job.job_id, DownloadState.DOWNLOADING.value)
            result = self._batch_run_single_attempt(
                job=job,
                cancel_token=cancel_token,
                progress_cb=progress_cb,
                log_cb=log_cb,
                skip_existing_files=skip_existing_files,
                filename_template=filename_template,
                conflict_policy=conflict_policy,
                save_metadata_to_file=save_metadata_to_file,
                speed_limit_kbps=speed_limit_kbps,
            )
            if result.state == DownloadState.PAUSED.value:
                if status_cb:
                    status_cb(job.job_id, DownloadState.PAUSED.value)
                jobs_queue.put(job)
                return (
                    DownloadResult(
                        job_id=job.job_id,
                        url=job.url,
                        state=DownloadState.PAUSED.value,
                    ),
                    False,
                    retried_attempts_increment,
                )
            should_retry = (
                (not cancel_token.is_set())
                and (not self._is_job_stopped(job.job_id))
                and result.state == DownloadState.ERROR.value
                and is_retryable_error(result.error)
                and attempt < max_retries
            )
            if not should_retry:
                return result, True, retried_attempts_increment
            attempt += 1
            if status_cb:
                status_cb(job.job_id, DownloadState.RETRYING.value)
            retry_delay = retry_backoff_seconds(
                attempt_index=attempt,
                retry_profile=normalized_retry_profile,
            )
            if log_cb:
                log_cb(f"[{job.job_id}] retry {attempt}/{max_retries} in {retry_delay:.2f}s")
            interrupt_state = self._wait_for_retry_window(
                delay_seconds=retry_delay,
                cancel_token=cancel_token,
                job_id=job.job_id,
            )
            if interrupt_state == DownloadState.CANCELLED.value:
                return (
                    DownloadResult(
                        job_id=job.job_id,
                        url=job.url,
                        state=DownloadState.CANCELLED.value,
                    ),
                    True,
                    retried_attempts_increment,
                )
            if interrupt_state == DownloadState.PAUSED.value:
                if status_cb:
                    status_cb(job.job_id, DownloadState.PAUSED.value)
                jobs_queue.put(job)
                return (
                    DownloadResult(
                        job_id=job.job_id,
                        url=job.url,
                        state=DownloadState.PAUSED.value,
                    ),
                    False,
                    retried_attempts_increment,
                )
            retried_attempts_increment += 1

    def _batch_complete_job(
        self,
        *,
        count_as_complete: bool,
        jobs_queue: queue.Queue[object],
        stop_sentinel: object,
        max_workers: int,
    ) -> int:
        pending_after_complete = -1
        if count_as_complete:
            with self._batch_lock:
                self._active_batch_pending_jobs = max(0, self._active_batch_pending_jobs - 1)
                pending_after_complete = self._active_batch_pending_jobs
        if pending_after_complete == 0:
            with self._batch_lock:
                self._active_batch_accepting = False
            for _ in range(max_workers):
                jobs_queue.put(stop_sentinel)
            self._notify_control_changed()
        return pending_after_complete

    @staticmethod
    def _batch_store_result(
        *,
        ordered_results: dict[str, DownloadResult],
        ordered_results_lock: threading.Lock,
        job_id: str,
        result: DownloadResult,
    ) -> None:
        with ordered_results_lock:
            ordered_results[job_id] = result

    def _batch_wait_for_control_change(self, *, cancel_token: threading.Event) -> None:
        with self._control_condition:
            last_seen = self._control_change_counter
            while not cancel_token.is_set() and self._control_change_counter == last_seen:
                self._control_condition.wait(timeout=5.0)

    def run_batch(
        self,
        jobs: list[DownloadJob],
        concurrency: int,
        cancel_token: threading.Event,
        *,
        progress_cb: BatchProgressCallback | None = None,
        status_cb: StatusCallback | None = None,
        log_cb: LogCallback | None = None,
        retry_count: int = 0,
        retry_profile: str = RetryProfile.BASIC.value,
        skip_existing_files: bool = False,
        filename_template: str = _DEFAULT_OUTPUT_TEMPLATE,
        conflict_policy: str = "skip",
        save_metadata_to_file: bool = False,
        speed_limit_kbps: int = 0,
    ) -> DownloadSummary:
        if not jobs:
            return DownloadSummary(total=0, completed=0, failed=0, skipped=0, cancelled=0, retried=0, results=[])

        job_ids = {str(job.job_id or "").strip() for job in jobs if str(job.job_id or "").strip()}
        with self._active_lock:
            self._stopped_job_ids.difference_update(job_ids)
            self._paused_job_ids.difference_update(job_ids)

        max_workers = max(1, min(int(concurrency), len(jobs)))
        normalized_retry_profile = normalize_retry_profile(retry_profile)
        max_retries = retry_limit_for_profile(
            retry_count=max(0, int(retry_count)),
            retry_profile=normalized_retry_profile,
        )
        ordered_results: dict[str, DownloadResult] = {}
        ordered_results_lock = threading.Lock()
        retried_attempts_counter = [0]
        retried_attempts_lock = threading.Lock()
        stop_sentinel = object()
        jobs_queue: queue.Queue[object] = queue.Queue()
        for job in jobs:
            jobs_queue.put(job)
            if status_cb:
                status_cb(job.job_id, DownloadState.QUEUED.value)

        with self._batch_lock:
            self._active_batch_queue = jobs_queue
            self._active_batch_pending_jobs = len(jobs)
            self._active_batch_status_cb = status_cb
            self._active_batch_accepting = True

        def worker_loop() -> None:
            while True:
                current, should_stop = self._batch_take_next_item(jobs_queue, stop_sentinel=stop_sentinel)
                if should_stop:
                    return
                if current is None:
                    continue
                result = DownloadResult(job_id="", url="", state=DownloadState.ERROR.value)
                count_as_complete = True
                try:
                    result, count_as_complete, retried_increment = self._batch_run_with_retries(
                        job=current,
                        jobs_queue=jobs_queue,
                        cancel_token=cancel_token,
                        progress_cb=progress_cb,
                        status_cb=status_cb,
                        log_cb=log_cb,
                        max_retries=max_retries,
                        normalized_retry_profile=normalized_retry_profile,
                        skip_existing_files=skip_existing_files,
                        filename_template=filename_template,
                        conflict_policy=conflict_policy,
                        save_metadata_to_file=save_metadata_to_file,
                        speed_limit_kbps=speed_limit_kbps,
                    )
                    if retried_increment > 0:
                        with retried_attempts_lock:
                            retried_attempts_counter[0] += retried_increment
                except Exception as exc:
                    safe_job_id = current.job_id
                    safe_job_url = current.url
                    safe_error = sanitize_error_text(exc)
                    result = DownloadResult(
                        job_id=safe_job_id,
                        url=safe_job_url,
                        state=DownloadState.ERROR.value,
                        error=safe_error,
                    )
                    if log_cb:
                        prefix = f"[{safe_job_id}] " if safe_job_id else ""
                        log_cb(f"{prefix}ERROR: {safe_error}")
                finally:
                    jobs_queue.task_done()
                    self._batch_complete_job(
                        count_as_complete=count_as_complete,
                        jobs_queue=jobs_queue,
                        stop_sentinel=stop_sentinel,
                        max_workers=max_workers,
                    )

                if not count_as_complete:
                    if self._resolve_interrupt_state(current.job_id, cancel_token) == DownloadState.PAUSED.value:
                        self._batch_wait_for_control_change(cancel_token=cancel_token)
                    continue

                self._batch_store_result(
                    ordered_results=ordered_results,
                    ordered_results_lock=ordered_results_lock,
                    job_id=current.job_id,
                    result=result,
                )
                if status_cb:
                    status_cb(current.job_id, normalize_download_state(result.state))

        workers: list[concurrent.futures.Future[None]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for _ in range(max_workers):
                workers.append(executor.submit(worker_loop))
            concurrent.futures.wait(workers)

        with self._batch_lock:
            self._active_batch_accepting = False
            self._active_batch_queue = None
            self._active_batch_pending_jobs = 0
            self._active_batch_status_cb = None

        with self._active_lock:
            self._stopped_job_ids.difference_update(job_ids)
            self._paused_job_ids.difference_update(job_ids)

        results: list[DownloadResult] = []
        seen_job_ids: set[str] = set()
        for job in jobs:
            job_id = str(job.job_id or "").strip()
            if (not job_id) or (job_id in seen_job_ids):
                continue
            item = ordered_results.get(job_id)
            if item is None:
                continue
            seen_job_ids.add(job_id)
            results.append(item)
        for job in jobs:
            job_id = str(job.job_id or "").strip()
            if (not job_id) or (job_id in seen_job_ids):
                continue
            seen_job_ids.add(job_id)
            fallback_state = DownloadState.CANCELLED.value if (cancel_token.is_set() or self._is_job_stopped(job_id)) else DownloadState.ERROR.value
            fallback_error = "" if fallback_state == DownloadState.CANCELLED.value else "Internal error: result missing for job."
            results.append(
                DownloadResult(
                    job_id=job_id,
                    url=str(job.url or ""),
                    state=fallback_state,
                    error=fallback_error,
                )
            )
        for job_id, item in ordered_results.items():
            if job_id in seen_job_ids:
                continue
            seen_job_ids.add(job_id)
            results.append(item)
        completed = sum(1 for item in results if item.state == DownloadState.DONE.value)
        skipped = sum(1 for item in results if item.state == DownloadState.SKIPPED.value)
        cancelled = sum(1 for item in results if item.state == DownloadState.CANCELLED.value)
        failed = sum(1 for item in results if item.state == DownloadState.ERROR.value)
        return DownloadSummary(
            total=len(results),
            completed=completed,
            failed=failed,
            skipped=skipped,
            cancelled=cancelled,
            retried=retried_attempts_counter[0],
            results=results,
        )


def run_metadata_worker_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action", choices=["analyze", "probe", "selection-size"])
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--format-choice", default="")
    parser.add_argument("--quality-choice", default="")
    try:
        args = parser.parse_args(list(argv or []))
    except SystemExit:
        envelope = {"ok": False, "result": None, "error": "Invalid metadata worker arguments."}
        sys.stdout.write(json.dumps(envelope, separators=(",", ":")))
        return 2

    os.environ[_METADATA_WORKER_ENV] = "1"
    service = DownloadService()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if args.action == "analyze":
                result = service._analyze_url_inprocess(str(args.url), timeout_seconds=args.timeout)
                payload: object = _dataclass_payload(result)
                error = result.error or ("" if result.is_valid else "Metadata analysis failed.")
            elif args.action == "probe":
                result = service._probe_formats_inprocess(str(args.url), timeout_seconds=args.timeout)
                payload = _dataclass_payload(result)
                error = result.error
            else:
                payload = service._resolve_selection_size_bytes_inprocess(
                    str(args.url),
                    str(args.format_choice or ""),
                    str(args.quality_choice or ""),
                    timeout_seconds=args.timeout,
                )
                error = "" if payload is not None else "Selection size unavailable."
        if error:
            envelope = {"ok": False, "result": None, "error": sanitize_error_text(error)}
            sys.stdout.write(json.dumps(envelope, separators=(",", ":")))
            return 0
        envelope = {"ok": True, "result": payload, "error": ""}
        sys.stdout.write(json.dumps(envelope, separators=(",", ":")))
        return 0
    except Exception as exc:
        envelope = {"ok": False, "result": None, "error": sanitize_error_text(exc)}
        sys.stdout.write(json.dumps(envelope, separators=(",", ":")))
        return 1
