<p align="center">
  placeholder
</p>

<h1 align="center">MediaCrate</h1>

<p align="center">
  <b>Fast. Simple. Reliable.</b><br>
  Download video and audio from supported sites with MediaCrate desktop app.
</p>

<p align="center">
  <a href="https://github.com/Justagwas/MediaCrate/releases/latest">
    <img src="https://img.shields.io/github/v/release/Justagwas/MediaCrate?style=for-the-badge" alt="Latest Release">
  </a>
  <a href="https://github.com/Justagwas/MediaCrate/releases">
    <img src="https://img.shields.io/github/downloads/Justagwas/MediaCrate/total?style=for-the-badge" alt="Total Downloads">
  </a>
  <a href="https://github.com/Justagwas/MediaCrate/issues">
    <img src="https://img.shields.io/github/issues/Justagwas/MediaCrate?style=for-the-badge" alt="Open Issues">
  </a>
  <a href="https://github.com/Justagwas/MediaCrate/commits">
    <img src="https://img.shields.io/github/last-commit/Justagwas/MediaCrate?style=for-the-badge" alt="Last Commit">
  </a>
</p>

<p align="center">
  <a href="https://github.com/Justagwas/MediaCrate/releases/latest">
    <img src="https://img.shields.io/badge/Download-Windows-blue?style=for-the-badge&logo=windows" alt="Download for Windows">
  </a>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#preview">Preview</a> •
  <a href="#security--warnings">Security</a> •
  <a href="#license">License</a>
</p>

---

## Built for people who just want it to work

MediaCrate is a lightweight desktop application for downloading video and audio from supported websites. It's designed to feel effortless for casual users, while remaining transparent and configurable for developers and enthusiasts.

Just paste a link and click to download.

---

## Features

* One-click downloads with real-time status
* Single or batch downloads
* Output formats: MP4, MP3, MOV, WAV
* Video quality selection up to 4K (when available)
* Duplicate and invalid link detection in batch mode
* Safe cancel - active downloads stop instantly
* Custom download location
* Built-in console with timestamps and selectable text

---

## Quick Start

1. Paste a URL (or multiple URLs for batch mode)
2. Choose format and quality
3. Click Download

That's it.

---

## Batch Mode

Batch downloads are handled like so:

* Duplicate URLs are skipped and marked
* Invalid entries are clearly labeled
* Existing files can be skipped or re-downloaded based on your preferences
* Adjustable concurrency and retry limits

---

## Supported Sites

MediaCrate is powered by yt-dlp, so site support matches upstream.

See `MediaCrate/supportedsites.md` for the full list.

---

## Preview

<details>
<summary><strong>Watch MediaCrate in action</strong></summary>

placeholder

</details>

<p align="center">
  <img src="placeholder" alt="MediaCrate" />
</p>

---

<details>
<summary><strong>For Developers</strong></summary>

### Configuration

Settings are stored in:

```
MediaCrate_config.json
```

Located next to `MediaCrate.py`.

### Requirements

```
packaging==25.0
pathvalidate==3.3.1
pywin32==311
Requests==2.32.5
yt_dlp==2025.12.8
```

### Run From Source

```bash
pip install -r requirements.txt
python MediaCrate.py
```

### Build (PyInstaller)

```bash
pyinstaller -F -w -i "icon.ico" --version-file=version.txt --clean MediaCrate.py
```

The executable will be generated in the `dist/` directory.

</details>

---

## Security & Warnings

Operating systems may show warnings when downloading MediaCrate simply because it is not yet widely recognized.

MediaCrate is:

* Fully open source
* Local-only (no accounts, no telemetry)
* Limited strictly to the URLs you provide

You are encouraged to review the source code or scan the executable with any security tool of your choice.

If you downloaded MediaCrate from the official repository or release page, it is safe to use.

---

## What MediaCrate Is Not

* Not a streaming service
* Not DRM-circumvention software
* Not a cloud-based downloader

MediaCrate downloads only what you explicitly request.

---

## Contributing

Issues, suggestions, and pull requests are welcome.

If reporting bugs, please include clear reproduction steps.

---

## License

Apache-2.0

See `LICENSE.txt` for details.

---

## Contact

[email@justagwas.com](mailto:email@justagwas.com)

---

MediaCrate is designed to be intuitive, fast, and simple.