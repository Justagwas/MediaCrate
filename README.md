<p align="center">
  <img
    width="192"
    height="192"
    alt="MediaCrate Logo"
    src="https://github.com/user-attachments/assets/6293a8af-3d7e-4856-a939-ca063cd6185c"
  />
</p>

<h1 align="center">MediaCrate</h1>

<h3 align="center">The universal multimedia downloader</h3>

<p align="center">
  Save media from supported sources with format, quality, and fallback controls<br/>
  with a lightweight desktop app.
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
  <a href="https://www.justagwas.com/projects/mediacrate">Website</a>
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

MediaCrate is a desktop app for downloading video or audio from supported sources using `yt-dlp` with a simple UI.

It includes URL validation, format/quality selection, batch downloads, fallback download strategies, and settings for advanced source handling (cookies and proxy).

For source support details, see [`MediaCrate/supportedsites.md`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/supportedsites.md).  

MediaCrate is built to be efficient:  
- ~0% CPU usage while idle  
- ~1% CPU usage during light downloads  
- ~40 MB memory footprint  

## Basic usage

1. Launch MediaCrate.
2. Paste one media URL (or enable batch mode in **Settings** for multiple links).
3. Choose format and quality.
4. Click **Download**.
5. Use **STOP** to abort active work, or **Open downloads folder** to jump to saved files.

Default output location is `~/Downloads/MediaCrate` (configurable in Settings).

## Features

- Single-link and optional batch-link download workflows.
- Format presets (`VIDEO`, `AUDIO`, `MP4`, `MP3`) plus dynamic format loading via `Load others`.
- Dynamic quality probing (up to available source resolutions, including `2160p` when available).
- Download retries and fallback attempts for difficult sources.
- Optional browser cookies and proxy support.
- First-launch setup wizard and UI scale setup flow.
- Theme support (`dark` / `light`), update checks, and runtime settings persistence.

## Batch downloads

- Batch mode can be enabled in Settings (`Allow Batch Downloads`).
- Detects invalid URLs and duplicate URLs before starting downloads.
- Handles already-existing files with optional re-download prompts.
- Supports configurable batch concurrency, retries, and maximum batch lines.
- Optional `Enable fallback attempts for batch downloads` for harder cases.

## Format, quality, and source handling

- Quality options are probed from source metadata for the selected URL/format.
- Audio formats always use best available audio stream for extraction.
- `Load others` pulls additional container/codec extensions available for the current URL.
- Proxy format is validated (for example `http://host:port` or `socks5://user:pass@host:port`).
- Browser cookies can be enabled and sourced from `chrome`, `edge`, `firefox`, `brave`, `opera`, `vivaldi`, or `safari`.

## First-launch and dependency prompts

- First run includes a setup wizard (download location, warnings behavior, default format, etc.).
- App checks for FFmpeg and Node.js; on Windows, guided install prompts are available.
- Built-in update checks use the official manifest, GitHub latest release, and SourceForge RSS as fallback.

## Preview

- Project page with full preview gallery: [justagwas.com/projects/mediacrate](https://www.justagwas.com/projects/mediacrate)
- OpenPiano Installer Download link: [justagwas.com/projects/mediacrate/download](https://www.justagwas.com/projects/mediacrate/download)

<details>
<summary>For Developers</summary>

### Requirements

- Python 3 with Tkinter available.
- Dependencies from [`MediaCrate/requirements.txt`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/requirements.txt):
  - `packaging==26.0`
  - `pathvalidate==3.3.1`
  - `pywin32==311`
  - `Requests==2.32.5`
  - `yt_dlp==2026.1.29`

### Running From Source

```bash
cd MediaCrate
py -m pip install -r requirements.txt
py MediaCrate.py
```

### Testing (optional)

From `MediaCrate/`:

```bash
py -m unittest discover -s tests -p "test_*.py" -v
```

### Build (optional)

There is no committed automated build script in this repo. For a manual one-file build:

```bash
cd MediaCrate
py -m pip install pyinstaller
py -m PyInstaller -F -w -i "icon.ico" --clean MediaCrate.py
```

### Configuration Files (developer-relevant)

- Runtime config: `MediaCrate_config.json` (auto-generated in app directory or `%LOCALAPPDATA%\MediaCrate` when needed).
- Project dependencies: [`MediaCrate/requirements.txt`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/requirements.txt).
- Supported extractor snapshot: [`MediaCrate/supportedsites.md`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/supportedsites.md).
- Static analysis workflow: [`.github/workflows/codeql.yml`](https://github.com/Justagwas/MediaCrate/blob/main/.github/workflows/codeql.yml).

</details>

## Security and OS Warnings

Windows may show SmartScreen or unsigned-app warnings for new builds. If you downloaded from official project sources, verify and proceed based on your trust policy:

- Website: <https://www.justagwas.com/projects/mediacrate>
- GitHub repo: <https://github.com/Justagwas/MediaCrate>
- Releases: <https://github.com/Justagwas/MediaCrate/releases>

Always review source and scan binaries yourself if required by your environment.

Use MediaCrate only for content you are authorized to download and in line with local law/platform terms.

## Contributing

Contributions are welcome:

- Start with [contribution guidelines](https://github.com/Justagwas/MediaCrate/blob/main/.github/CONTRIBUTING.md).
- Open issues at <https://github.com/Justagwas/MediaCrate/issues>.
- Submit pull requests at <https://github.com/Justagwas/MediaCrate/pulls>.
- Follow the [Code of Conduct](https://github.com/Justagwas/MediaCrate/blob/main/.github/CODE_OF_CONDUCT.md).

Security reports should follow [`.github/SECURITY.md`](https://github.com/Justagwas/MediaCrate/blob/main/.github/SECURITY.md).

## License

Licensed under the Apache License 2.0. See [`LICENSE`](https://github.com/Justagwas/MediaCrate/blob/main/LICENSE).

## Contact

- Email: [email@justagwas.com](mailto:email@justagwas.com)
- Website: <https://www.justagwas.com/projects/mediacrate>