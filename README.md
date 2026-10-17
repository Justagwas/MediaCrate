# MediaCrate

<div align="center">

[![Code: GitHub](https://img.shields.io/badge/Code-GitHub-111827.svg?style=flat&logo=github&logoColor=white)](https://github.com/Justagwas/MediaCrate)
[![Website](https://img.shields.io/badge/Website-MediaCrate-0ea5e9.svg?style=flat&logo=google-chrome&logoColor=white)](https://www.justagwas.com/projects/mediacrate/)
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

<p align="center"><b>Video and Audio Downloader for Windows</b></p>

<p align="center">Save video or audio from a link, or manage several downloads in a queue.</p>

<div align="center">

[![Version](https://img.shields.io/github/v/tag/Justagwas/mediacrate.svg?label=Version)](https://github.com/Justagwas/mediacrate/tags)
[![License](https://img.shields.io/github/license/Justagwas/mediacrate.svg)](https://github.com/Justagwas/mediacrate/blob/main/LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/Justagwas/mediacrate/main.svg?style=flat&cacheSeconds=3600)](https://github.com/Justagwas/mediacrate/commits/main)
[![Open Issues](https://img.shields.io/github/issues/Justagwas/mediacrate.svg)](https://github.com/Justagwas/mediacrate/issues)
[![Stars](https://img.shields.io/github/stars/Justagwas/mediacrate.svg?style=flat&cacheSeconds=3600)](https://github.com/Justagwas/mediacrate/stargazers)
![Downloads (7d)](https://img.shields.io/badge/dynamic/json?style=flat&url=https%3A%2F%2Fdownload-stats-worker.justagwas.workers.dev%2Fdownloads%2Fmediacrate%3Frange%3Dweek&query=%24.data.weekly.all&label=Downloads%20(7d))

</div>

## Overview

Paste a link from a [supported website](https://github.com/Justagwas/MediaCrate/blob/main/MediaCrate/supportedsites.md) and choose video or audio in the format you need. MediaCrate shows the title, thumbnail, available quality choices and estimated file size when the source provides them, so you can review the result before downloading.

Have several links to save? Add them to a queue, choose settings for each item, and control how many downloads run at once. Pause or retry individual items, then open the finished files from History.

## Features

- **Output choices:** MP4 video and audio formats such as MP3, WAV and FLAC, with video quality limits.
- **Size and media previews:** Titles, thumbnails, available formats and estimated sizes before downloading.
- **Batch downloads:** Bulk paste, URL-list import and export, queue search and status filters.
- **Download controls:** Pause, resume, stop and retry, with per-download speed limits and adjustable simultaneous downloads.
- **Saved-file choices:** Naming templates, output folders, existing-file options and optional metadata in supported media files.
- **History and queue recovery:** Access to completed files and a prompt to restore a saved queue.
- **Standalone option:** An executable that runs without installation, alongside the regular Windows installer.
- **Built-in tutorial:** A guided introduction to the application's controls.

## Basic usage

1. Get the [Windows installer or standalone app](https://www.justagwas.com/projects/mediacrate/). Choose the installer for a normal setup, or standalone to run without installing.
2. Launch MediaCrate and paste a public media link.
3. Review the preview and size estimate when available. Choose the output format and, for video, a quality limit.
4. Select **Download**. If a required component is missing, install it from **Settings > Dependencies**, then retry.
5. Wait for the download and any final processing to finish.
6. Open the finished file or its folder from **Settings > History**.

For several links, select **Multi-URL** under **Settings > Input mode**, add your links using **Bulk paste** or **Import**, and choose **Download all**.

Default output folder: `%USERPROFILE%\Downloads\MediaCrate`.

## Using MediaCrate

### One link or a complete queue

Use **Single-URL** for one item. Use **Multi-URL** to paste or import several links, give each item its own format and quality, and manage the queue from one screen. Each link requests one media item; adding a playlist link does not automatically import its tracks.

MediaCrate saves the queue locally and asks whether to restore it on the next launch. Restoring the queue does not restore a running transfer. If **Automatically start analyzed batch rows** is enabled, restored rows that need fresh analysis can start downloading when they become ready. Turn this option off before closing MediaCrate if you want to review them first.

### Clear choices before downloading

Choose a format to suit how you will use the file: MP4 for video, for example, or MP3, WAV or FLAC for audio. You can also choose the filename and save folder, and decide whether to skip, number or overwrite an existing file.

The format determines the file type; video quality sets a maximum resolution, not a promise to improve the source. **Best quality** removes that limit. Size estimates depend on the information supplied by the website. **Prefer accurate file sizes** performs additional analysis when possible; the final file size can still differ after conversion.

FFmpeg is required for merging or converting media, including MP4 and MP3 outputs. Some websites also need Node.js. Install missing components from **Settings > Dependencies**. The Windows app includes its Python runtime, so you do not need to install Python to use an official release.

### Metadata and download controls

Metadata means details such as a title or artist stored with the media. In v2.5.0, the setting labelled **Save metadata as .info.json** embeds available metadata in supported media files using FFmpeg; it does not create a separate JSON file. It is off by default.

**Batch concurrency** controls how many downloads run together. **Preview workers** separately controls how many links can be analyzed at once. Adjust these and the per-download speed limit to suit your connection and computer. More simultaneous downloads do not guarantee faster results; the source website and your connection still affect transfer speed.

### A finished file you can find

The transfer can finish before the file is ready: MediaCrate may still be joining video and audio or converting the output. Wait for completion, then use History to open the file, reveal its folder, or retry the original link.

### Installer, standalone and local data

The standalone executable runs without installation. Both editions include Python and can install missing FFmpeg or Node.js components through **Settings > Dependencies**. Settings, saved queues, history and managed components normally use `%LOCALAPPDATA%\MediaCrate`, rather than travelling beside the standalone executable.

## Project resources

- Project page: <https://www.justagwas.com/projects/mediacrate/>
- Download page: <https://www.justagwas.com/projects/mediacrate/download/>
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
  - <https://www.justagwas.com/projects/mediacrate/download/>
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
- Website: <https://www.justagwas.com/projects/mediacrate/>

## License

Licensed under the GNU General Public License v3.0 (GPL-3.0).

See [`LICENSE`](https://github.com/Justagwas/MediaCrate/blob/main/LICENSE).
