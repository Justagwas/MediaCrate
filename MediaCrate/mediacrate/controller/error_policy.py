from __future__ import annotations


_ERROR_PATTERNS: tuple[tuple[str, bool, tuple[str, ...]], ...] = (
    (
        "rate_limit",
        True,
        ("429", "too many requests", "rate limit", "try again later"),
    ),
    (
        "network",
        True,
        (
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "connection refused",
            "network is unreachable",
            "dns",
            "temporarily unavailable",
            "service unavailable",
            "temporary",
        ),
    ),
    (
        "authentication",
        False,
        ("sign in", "login", "private", "members-only", "cookie"),
    ),
    (
        "geo_restricted",
        False,
        ("not available in your country", "geo"),
    ),
    (
        "unsupported",
        False,
        ("unsupported url", "unsupported", "extractor error", "unable to extract"),
    ),
    (
        "filesystem",
        False,
        ("permission denied", "access is denied", "no space left", "disk full", "read-only file system"),
    ),
    (
        "dependency",
        False,
        ("ffmpeg", "yt-dlp executable was not found", "python -m yt_dlp"),
    ),
)

_FAILURE_HINTS: dict[str, str] = {
    "rate_limit": "The site is rate-limiting requests. Wait a bit or lower concurrency.",
    "network": "Network issue detected. Retry later or lower concurrency/speed.",
    "authentication": "This URL likely requires login/cookies. Public URLs work best in V2.",
    "geo_restricted": "This content may be region restricted.",
    "unsupported": "Extractor could not handle this URL yet. Try updating yt-dlp.",
    "filesystem": "Download folder issue. Check write permissions and free space.",
    "dependency": "A dependency is missing. Install FFmpeg/Node from Settings.",
}


def classify_download_error(message: str) -> tuple[str, bool]:
    text = str(message or "").strip().lower()
    if not text:
        return "unknown", False
    for category, retryable, tokens in _ERROR_PATTERNS:
        if any(token in text for token in tokens):
            return category, retryable
    return "unknown", False


def format_classified_error(message: str) -> str:
    raw = str(message or "").strip()
    category, _retryable = classify_download_error(raw)
    short = raw.replace("\r", " ").replace("\n", " ")
    if len(short) > 280:
        short = f"{short[:279]}..."
    return f"{category.upper()}: {short}" if short else category.upper()


def failure_hint(category: str) -> str:
    normalized = str(category or "").strip().lower()
    return _FAILURE_HINTS.get(normalized, "Unknown failure. Retry and check the URL/source.")
