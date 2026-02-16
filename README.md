# MediaCrate

<div align="center">

[![Code: GitHub](https://img.shields.io/badge/Code-GitHub-111827.svg?style=flat&logo=github&logoColor=white)](https://github.com/Justagwas/MediaCrate)
[![Website](https://img.shields.io/badge/Website-MediaCrate-0ea5e9.svg?style=flat&logo=google-chrome&logoColor=white)](https://Justagwas.com/projects/mediacrate)
[![Mirror: SourceForge](https://img.shields.io/badge/Mirror-SourceForge-ff6600.svg?style=flat&logo=sourceforge&logoColor=white)](https://sourceforge.net/projects/mediacrate/)

</div>

<p align="center">
  <img
    width="128"
    height="128"
    alt="MediaCrate Logo"
    src="https://github.com/user-attachments/assets/6293a8af-3d7e-4856-a939-ca063cd6185c"
  />
</p>

<div align="center">

[![Download (Windows)](https://img.shields.io/badge/Download-Windows%20(MediaCrateSetup.exe)-2563eb.svg?style=flat&logo=windows&logoColor=white)](https://github.com/Justagwas/MediaCrate/releases/latest/download/MediaCrateSetup.exe)

</div>

<p align="center"><b>The all-in-one multimedia downloader application</b></p>

<p align="center">Paste a link, select your preferred format and quality, queue 100+ downloads with pause/resume/retry support, and manage it all in one place</p>

<div align="center">

[![Version](https://img.shields.io/github/v/tag/Justagwas/MediaCrate.svg?label=Version)](
https://github.com/Justagwas/MediaCrate/tags)
[![Last Commit](https://img.shields.io/github/last-commit/Justagwas/MediaCrate/main.svg?style=flat&cacheSeconds=3600)](
https://github.com/Justagwas/MediaCrate/commits/main)
[![Stars](https://img.shields.io/github/stars/Justagwas/MediaCrate.svg?style=flat&cacheSeconds=3600)](
https://github.com/Justagwas/MediaCrate/stargazers)
[![Open Issues](https://img.shields.io/github/issues/Justagwas/MediaCrate.svg)](
https://github.com/Justagwas/MediaCrate/issues)
[![License](https://img.shields.io/github/license/Justagwas/MediaCrate.svg)](
https://github.com/Justagwas/MediaCrate/blob/main/LICENSE)

</div>

## Overview

MediaCrate is a Windows desktop downloader built with Python and `yt-dlp`. It supports both single-URL and multi-URL workflows, with in-app queue controls, dependency setup, and persistent user settings.

MediaCrate's performance:
- ~0% CPU usage while idle
- ~1% CPU usage during active use
- ~60 MB memory usage
- Download performance depends solely on your internet connection.

- Supported sites: https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/supportedsites.md

## Basic usage

1. Download and install from the [latest release](https://github.com/Justagwas/MediaCrate/releases/latest/download/MediaCrateSetup.exe).
2. Launch MediaCrate.
3. Paste a URL in the input field.
4. Choose format and quality.
5. Click `DOWNLOAD` for single-link mode, or switch to `Multi-URL` for queue mode.
6. Track progress in the progress bar and console.
7. Open files/folders from the History section.

Default output folder: `%USERPROFILE%\Downloads\MediaCrate`.

## Features

- Single-URL and Multi-URL download modes.
- Queue operations: add, bulk paste, import, export, start, pause, resume, stop, retry, remove.
- Format and quality selection with metadata probing and fallback behavior.
- Download controls for conflict policy, retry profile, speed limit, and adaptive concurrency.
- Built-in dependency install actions for FFmpeg and Node.js.
- History tools for open file/folder, retry URL, and clear history.
- Guided in-app tutorial.
- Runtime storage under `%LOCALAPPDATA%\MediaCrate`.

## Feature sections

### Download modes

- `Single-URL`: direct one-link workflow.
- `Multi-URL`: queue-based workflow with row-level status and actions.
- Both modes share the same output, format/quality, and policy controls.

### Queue and batch controls

- Bulk operations include paste/import/export and start-all behavior.
- Pause/resume and retry states are surfaced per-row and in aggregate progress.
- Queue state can be restored between sessions.

### Dependencies

- FFmpeg is required for key merge/conversion paths and is checked on startup.
- Node.js is installable from Settings for compatible workflows.
- Install progress and status are shown in-app.

### Updates and release metadata

- Update checks are driven by the official manifest (`latest.json`) and trusted fallback providers.
- Release details are available from GitHub releases and the project site.

## Preview

- Website project page (overview + gallery): <https://www.justagwas.com/projects/mediacrate>
- Download page: <https://www.justagwas.com/projects/mediacrate/download>
- Releases: <https://github.com/Justagwas/mediacrate/releases>

<details><summary>For Developers</summary>

### Requirements

- Windows (primary runtime target).
- Python 3.11+.
- Dependencies in [`MediaCrate/requirements.txt`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/requirements.txt)

### Running From Source

```powershell
cd MediaCrate
py -m pip install -r requirements.txt
py MediaCrate.py
```

### Configuration Files

- App settings schema and persistence logic: [`MediaCrate/mediacrate/core/config_service.py`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/mediacrate/core/config_service.py)
- Runtime path resolution and storage directories: [`MediaCrate/mediacrate/core/paths.py`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/mediacrate/core/paths.py)

</details>

## Security and OS Warnings

- Windows SmartScreen can show warnings for newer or unsigned binaries.
- Download from official links only:
  - <https://github.com/Justagwas/mediacrate/releases>
  - <https://www.justagwas.com/projects/mediacrate/download>
  - <https://sourceforge.net/projects/mediacrate/>
- Security policy and private vulnerability reporting: [`.github/SECURITY.md`](https://github.com/Justagwas/MediaCrate/blob/main/.github/SECURITY.md)

## Contributing

Contributions are welcome.

- Start with [`.github/CONTRIBUTING.md`](https://github.com/Justagwas/MediaCrate/blob/main/.github/CONTRIBUTING.md)
- Follow [`.github/CODE_OF_CONDUCT.md`](https://github.com/Justagwas/MediaCrate/blob/main/.github/CODE_OF_CONDUCT.md)
- Use [Issues](https://github.com/Justagwas/MediaCrate/issues) for bugs, requests, and questions
- Wiki: <https://github.com/Justagwas/mediacrate/wiki>

## License

Licensed under the Apache License 2.0.

See [`LICENSE`](https://github.com/Justagwas/MediaCrate/blob/main/LICENSE).

## Contact

- Email: [email@justagwas.com](mailto:email@justagwas.com)
- Website: <https://www.justagwas.com/projects/mediacrate>
