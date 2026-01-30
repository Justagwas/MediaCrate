<p align="center">
  <img
    width="192"
    height="192"
    alt="MediaCrate Logo"
    src="https://github.com/user-attachments/assets/298427b1-d609-4e22-84c4-2c28bd980951"
  />
</p>

<h1 align="center">MediaCrate</h1>

<h3 align="center">The universal multimedia downloader</h3>

<p align="center">
  Download video and audio from over <strong>1,000+ supported websites</strong><br/>
  with a simple, local desktop app.
</p>

<p align="center">
  <a href="https://github.com/Justagwas/MediaCrate/releases/latest/download/MediaCrateSetup.exe">
    <img
      src="https://img.shields.io/badge/Download%20for%20Windows-2563eb?style=for-the-badge&logo=windows&logoColor=white"
      alt="Download MediaCrate for Windows"
    />
  </a>
</p>

<p align="center">
  <a href="https://Justagwas.com/projects/mediacrate">Website</a>
  &nbsp;•&nbsp;
  <a href="https://github.com/Justagwas/MediaCrate/releases">Releases</a>
  &nbsp;•&nbsp;
  <a href="https://github.com/Justagwas/MediaCrate/issues">Issues</a>
  &nbsp;•&nbsp;
  <a href="https://github.com/Justagwas/MediaCrate/wiki">Documentation</a>
  &nbsp;•&nbsp;
  <a href="https://github.com/Justagwas/MediaCrate/blob/main/LICENSE">License</a>
</p>

---

## Overview

MediaCrate is a fast, lightweight desktop application for downloading video and
audio from supported websites.

Paste a link, choose your format and quality, and download.

MediaCrate is built to be efficient:

- ~0% CPU usage while idle
- ~1% CPU usage during light downloads
- ~40 MB memory footprint

---

## Easy to operate

1. Paste a URL (or multiple URLs for batch mode)
2. Choose format and quality
3. Click **Download**

That's it.

---

## Features

- One-click downloads with real-time status
- Single and batch downloads
- Output formats: MP4, MP3, MOV, WAV and other
- Video quality selection up to 4K (when available)
- Duplicate and invalid link detection in batch mode
- Safe cancel (active downloads stop immediately)
- Custom download location
- Built-in console with timestamps and warnings

---

## Batch Mode

Batch downloads are handled like so:

- Duplicate URLs are skipped and clearly marked
- Invalid entries are labeled
- Existing files can be skipped or re-downloaded based on preferences
- Adjustable concurrency and retry limits

---

## Supported Sites

Site support follows upstream `yt-dlp` updates.

For the full list of supported websites, see:

```
MediaCrate/supportedsites.md
```

---

## Preview

<details>
<summary><strong>Click to expand preview video</strong></summary>

<video
  src="https://github.com/user-attachments/assets/ef20c013-5a79-41c1-9dc0-d40ba03d27ef"
  controls
  muted
  style="max-width: 100%; height: auto;">
</video>

</details>

---

<details>
<summary><strong>For Developers</strong></summary>

### Configuration

Settings are stored in:

```
MediaCrate_config.json
```

Located next to `MediaCrate.py`.

---

### Requirements

```
packaging==26.0
pathvalidate==3.3.1
pywin32==311
requests==2.32.5
yt-dlp==2026.01.29
````

---

### Run From Source

```bash
pip install -r requirements.txt
python MediaCrate.py
````

---

### Build (PyInstaller)

```bash
pyinstaller -F -w -i "icon.ico" --clean MediaCrate.py
```

The executable will be generated in the `dist/` directory.

</details>

---

## Security and OS Warnings

Your operating system may show warnings when downloading or running MediaCrate
because it is not yet widely recognized.

MediaCrate is:

* Fully open source
* Local-only (no accounts, no telemetry)
* Limited strictly to the URLs you provide

You are encouraged to review the source code or scan the executable with any
security tool of your choice.

If downloaded from the official repository or release page, it can be
independently verified and safely used.

---

## What MediaCrate Is Not

* Not a streaming service
* Not DRM-circumvention software
* Not a cloud-based downloader

MediaCrate only downloads content you explicitly request.

---

## Contributing

Issues, suggestions, and pull requests are welcome.

If reporting a bug, please include clear reproduction steps.

---

## License

Apache License 2.0

See `LICENSE` for details.

---

## Contact

[email@justagwas.com](mailto:email@justagwas.com)
