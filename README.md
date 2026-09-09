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
    src="MediaCrate/icon.ico"
  />
</p>

<div align="center">

[![Download (Windows)](https://img.shields.io/badge/Download-Windows%20(MediaCrateSetup.exe)-2563eb.svg?style=flat&logo=windows&logoColor=white)](https://github.com/Justagwas/MediaCrate/releases/latest/download/MediaCrateSetup.exe)

</div>

<p align="center"><b>The all-in-one multimedia downloader application</b></p>

<p align="center">Paste a link, select your preferred format and quality, queue 100+ downloads with pause/resume/retry support, and manage it all in one place</p>

<div align="center">

[![Version](https://img.shields.io/github/v/tag/Justagwas/mediacrate.svg?label=Version)](https://github.com/Justagwas/mediacrate/tags)
[![License](https://img.shields.io/github/license/Justagwas/mediacrate.svg)](https://github.com/Justagwas/mediacrate/blob/main/LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/Justagwas/mediacrate/main.svg?style=flat&cacheSeconds=3600)](https://github.com/Justagwas/mediacrate/commits/main)
[![Open Issues](https://img.shields.io/github/issues/Justagwas/mediacrate.svg)](https://github.com/Justagwas/mediacrate/issues)
[![Stars](https://img.shields.io/github/stars/Justagwas/mediacrate.svg?style=flat&cacheSeconds=3600)](https://github.com/Justagwas/mediacrate/stargazers)
![Downloads (7d)](https://img.shields.io/badge/dynamic/json?style=flat&url=https%3A%2F%2Fdownload-stats-worker.justagwas.workers.dev%2Fdownloads%2Fmediacrate%3Frange%3Dweek&query=%24.data.weekly.all&label=Downloads%20(7d))

</div>

## Overview

MediaCrate is a Windows application for saving video or audio from supported websites. Paste one link for a quick download or prepare an organized queue when you have several items to collect.

Helpful previews show the title, thumbnail, available formats, quality choices, and estimated size when the website provides them. Each download keeps its own progress and controls, so it is always clear what is happening and where the finished file will go.

MediaCrate's performance:  
~0% CPU usage while idle  
~1% CPU usage during active use  
~60 MB memory usage      
  
Download speed depends on the internet connection and the website providing the media.  

[View the current supported site list](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/supportedsites.md).

## Basic usage

1. Download and install from the [latest release](https://github.com/Justagwas/MediaCrate/releases/latest/download/MediaCrateSetup.exe).
2. Launch MediaCrate.
3. Paste a media link into the input field.
4. Choose video or audio, then select the preferred format and quality.
5. Select **Download** for one link, or switch to **Multi URL** to prepare a queue.
6. Follow the progress, pause or retry when needed, and let MediaCrate finish the file.
7. Open completed files or their folders from **History**.

Default output folder: `%USERPROFILE%\Downloads\MediaCrate`.

## Features

- A focused mode for one link and a managed queue for many.
- Helpful previews with titles, thumbnails, formats, quality choices, and size estimates.
- Video downloads and a range of audio only formats.
- Bulk paste, text file import and export, search, and status filters.
- Pause, resume, stop, retry, remove, and start all controls.
- Adjustable download speed and control over how many items run together.
- Clear choices when a file with the same name already exists.
- Local queue restoration and download history.
- A guided tutorial available inside the application.

## Using MediaCrate

### One link or a complete queue

Use **Single URL** when you want one item with as little setup as possible. Use **Multi URL** to paste or import several links, give each item its own format and quality, and manage the collection from one screen.

MediaCrate remembers the queue between sessions. An interrupted item is not restarted without your knowledge, so you can review it before continuing.

### Clear choices before downloading

When a preview is available, MediaCrate shows the useful details before the transfer begins. Choose the result you want, decide what should happen if the file already exists, and set speed or queue limits when necessary.

If a selected format needs an additional media component, MediaCrate can guide you through installing it from **Settings**.

### A finished file you can find

MediaCrate brings video and audio together or converts the result when required, then saves it under a clear name. The local history can open the file, reveal its folder, or return the original link to the current workflow.

## Project resources

- Project page: <https://www.justagwas.com/projects/mediacrate>
- Download page: <https://www.justagwas.com/projects/mediacrate/download>
- Documentation: <https://github.com/Justagwas/MediaCrate/wiki>
- Releases: <https://github.com/Justagwas/MediaCrate/releases>

<details>
<summary>For Developers</summary>

### Requirements

- Windows (primary runtime target).
- Python 3.11 or later.
- Dependencies in [`MediaCrate/requirements.txt`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/requirements.txt).

### Running From Source

```powershell
cd MediaCrate
py -m pip install -r requirements.txt
py MediaCrate.py
```

### Configuration Files

- App settings schema and persistence logic: [`MediaCrate/mediacrate/core/config.py`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/mediacrate/core/config.py)
- Runtime path resolution and storage directories: [`MediaCrate/mediacrate/core/paths.py`](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/mediacrate/core/paths.py)

</details>

## Security and OS Warnings

- Windows SmartScreen may display a warning for a new or unsigned release.
- Obtain MediaCrate only from the official distribution locations:
  - <https://github.com/Justagwas/MediaCrate/releases>
  - <https://www.justagwas.com/projects/mediacrate/download>
  - <https://sourceforge.net/projects/mediacrate/>
- Refer to the repository [Security Policy](https://github.com/Justagwas/MediaCrate/security/policy) for private vulnerability reporting.

## Contributing

Contributions are welcome.

- Start with [`.github/CONTRIBUTING.md`](https://github.com/Justagwas/MediaCrate/blob/main/.github/CONTRIBUTING.md)
- Follow the shared [Code of Conduct](https://github.com/Justagwas/.github/blob/main/CODE_OF_CONDUCT.md)
- Use [Issues](https://github.com/Justagwas/MediaCrate/issues) for defect reports, feature proposals, and questions
- Wiki: <https://github.com/Justagwas/MediaCrate/wiki>

## Contact

- Email: [email@justagwas.com](mailto:email@justagwas.com)
- Website: <https://www.justagwas.com/projects/mediacrate>

## License

Licensed under the GNU General Public License v3.0 (GPL-3.0).

See [`LICENSE`](https://github.com/Justagwas/MediaCrate/blob/main/LICENSE).
