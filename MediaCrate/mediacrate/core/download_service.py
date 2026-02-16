from __future__ import annotations

import concurrent.futures
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Any

from .config_service import DEFAULT_FILENAME_TEMPLATE
from .models import (
    DownloadJob,
    DownloadResult,
    DownloadSummary,
    DownloadState,
    FormatChoice,
    FormatProbeResult,
    RetryProfile,
    UrlAnalysisResult,
    is_audio_format_choice,
)
from .formatting import format_size_human as _format_size_human
from .paths import resolve_binary

_PROGRESS_RE = re.compile(r"(?P<percent>\d+(?:\.\d+)?)%")
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
_CONVERSION_CONTAINER_CHOICES = {"WEBM", "MKV", "MOV", "AVI", "FLV"}
_SIZE_ESTIMATE_DEFAULT_KEY = "__DEFAULT__"


format_size_human = _format_size_human


def _default_format_choices() -> list[str]:
    return [choice.value for choice in FormatChoice]


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
    estimated = int((bitrate_kbps * 1000.0 / 8.0) * float(duration_seconds))
    return estimated if estimated > 0 else None


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

    if choice == FormatChoice.VIDEO.value or choice in _CONVERSION_CONTAINER_CHOICES:
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
    key = _selection_size_key(format_choice, quality_choice)
    value = estimates.get(key)
    if isinstance(value, int) and value > 0:
        return int(value)
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

    for ext in sorted(_CONVERSION_CONTAINER_CHOICES):
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
        "LOAD OTHERS",
    }:
        ext = raw_choice.lower()
        if choice in _CONVERSION_CONTAINER_CHOICES:
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
    if choice in _CONVERSION_CONTAINER_CHOICES:
        return choice.lower()
    if choice in {
        FormatChoice.VIDEO.value,
        FormatChoice.AUDIO.value,
        "LOAD OTHERS",
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
        "Try BEST QUALITY, VIDEO/MP4, or Load others."
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
        self._active_lock = threading.Lock()
        self._control_condition = threading.Condition()
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
    def _should_fallback_from_inprocess(error_text: str) -> bool:
        cleaned = sanitize_error_text(error_text).lower()
        if not cleaned:
            return False
        if "no module named" in cleaned and "yt_dlp" in cleaned:
            return True
        if "yt-dlp import failed" in cleaned:
            return True
        return False

    def probe_formats(self, url: str, *, timeout_seconds: float | None = None) -> FormatProbeResult:
        value = coerce_http_url(url)
        default_formats = [choice.value for choice in FormatChoice] + ["Load others"]
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

    def _build_command(
        self,
        job: DownloadJob,
        *,
        skip_existing_files: bool = False,
        filename_template: str = _DEFAULT_OUTPUT_TEMPLATE,
        conflict_policy: str = "skip",
        speed_limit_kbps: int = 0,
    ) -> tuple[list[str], str]:
        output_dir = Path(job.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        selector, post_args = _format_selector(job.format_choice, job.quality_choice)
        output_template = _with_forced_extension(
            str(output_dir / sanitize_filename_template(filename_template)),
            _fixed_output_extension(job.format_choice),
        )
        normalized_conflict_policy = normalize_conflict_policy(conflict_policy)
        command = [
            *self._resolve_yt_dlp_subprocess_prefix(),
            "--newline",
            "--no-playlist",
            "--no-warnings",
            "-f",
            selector,
            "-o",
            output_template,
        ]

        ffmpeg_path = resolve_binary("ffmpeg")
        choice = str(job.format_choice or "").strip().upper()
        if (choice == FormatChoice.MP3.value or choice in _CONVERSION_CONTAINER_CHOICES) and not ffmpeg_path:
            raise FileNotFoundError(
                "ffmpeg is required for the selected format conversion. Install ffmpeg and retry."
            )
        if ffmpeg_path:
            command.extend(["--ffmpeg-location", ffmpeg_path])

        if normalized_conflict_policy == "skip":
            command.append("--no-overwrites")
        elif normalized_conflict_policy == "overwrite":
            command.append("--force-overwrites")

        if skip_existing_files:
                                                                                    
                                                                                      
            if "--force-overwrites" in command:
                command = [item for item in command if item != "--force-overwrites"]
            if "--no-overwrites" not in command:
                command.append("--no-overwrites")

        rate_limit = max(0, int(speed_limit_kbps))
        if rate_limit > 0:
            command.extend(["--limit-rate", f"{rate_limit}K"])

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
        last_error = clean
        if log_cb and _is_important_log_line(clean):
            log_cb(clean)
        if "has already been downloaded" in clean.lower():
            saw_already_downloaded = True
        candidate_path = self._parse_output_path_from_line(clean)
        if candidate_path:
            output_path = candidate_path
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
            if saw_already_downloaded:
                return self._make_result(job, state=DownloadState.SKIPPED.value, output_path=output_path)
            return self._make_result(job, state=DownloadState.DONE.value, output_path=output_path)
        return self._make_result(
            job,
            state=DownloadState.ERROR.value,
            output_path=output_path,
            error=_friendly_format_error(job, last_error or f"yt-dlp exited with {return_code}"),
        )

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
        speed_limit_kbps: int = 0,
    ) -> DownloadResult:
        try:
            command, _ = self._build_command(
                job,
                skip_existing_files=skip_existing_files,
                filename_template=filename_template,
                conflict_policy=conflict_policy,
                speed_limit_kbps=speed_limit_kbps,
            )
        except Exception as exc:
            return self._make_result(job, state=DownloadState.ERROR.value, error=str(exc))

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
            return self._make_result(job, state=DownloadState.ERROR.value, error=str(exc))
        self._register_process(process, job.job_id)

        try:
            stream = process.stdout
            if stream is None:
                return self._make_result(job, state=DownloadState.ERROR.value, error="No output stream")

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
                    progress_cb(max(0.0, min(99.0, percent)), clean)

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
            try:
                if process.stdout:
                    process.stdout.close()
            except Exception:
                pass

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
        output_template = _with_forced_extension(
            str(output_dir / sanitize_filename_template(filename_template)),
            _fixed_output_extension(job.format_choice),
        )
        normalized_conflict_policy = normalize_conflict_policy(conflict_policy)
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

            downloaded = payload.get("downloaded_bytes")
            total = payload.get("total_bytes")
            total_estimate = payload.get("total_bytes_estimate")
            total_bytes = total if isinstance(total, (int, float)) else total_estimate
            percent = 0.0
            if isinstance(downloaded, (int, float)) and isinstance(total_bytes, (int, float)) and total_bytes > 0:
                percent = (float(downloaded) / float(total_bytes)) * 100.0
            else:
                percent_str = str(payload.get("_percent_str") or "").strip()
                match = _PROGRESS_RE.search(percent_str)
                if match:
                    try:
                        percent = float(match.group("percent"))
                    except ValueError:
                        percent = 0.0
            line = sanitize_error_text(payload.get("_percent_str") or "Downloading...")
            progress_cb(max(0.0, min(99.0, percent)), line or "Downloading...")

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
        if (choice == FormatChoice.MP3.value or choice in _CONVERSION_CONTAINER_CHOICES) and not ffmpeg_path:
            return self._make_result(
                job,
                state=DownloadState.ERROR.value,
                error="ffmpeg is required for the selected format conversion. Install ffmpeg and retry.",
            )
        if ffmpeg_path:
            ydl_opts["ffmpeg_location"] = ffmpeg_path

        if normalized_conflict_policy == "overwrite":
            ydl_opts["overwrites"] = True
        if normalized_conflict_policy == "skip" or skip_existing_files:
            ydl_opts["nooverwrites"] = True
            ydl_opts.pop("overwrites", None)

        if rate_limit > 0:
            ydl_opts["ratelimit"] = rate_limit * 1024

        if choice == FormatChoice.MP3.value:
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }
            ]
        elif choice == FormatChoice.MP4.value:
            ydl_opts["merge_output_format"] = "mp4"
        elif choice in _CONVERSION_CONTAINER_CHOICES:
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
        return self._resolve_terminal_result(
            job,
            return_code=result_code,
            progress_cb=progress_cb,
            saw_already_downloaded=saw_already_downloaded,
            output_path=output_path,
            last_error=fallback_error,
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

    def _batch_wait_for_control_change(self) -> None:
        with self._control_condition:
            self._control_condition.wait(timeout=0.5)

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
                    self._batch_wait_for_control_change()
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
