<p align="center">
  <img width="256" height="200"
       alt="MediaCrate Logo"
       src="https://github.com/user-attachments/assets/3bf8495d-40ef-4ca5-863e-a20fb017a738" />
</p>

<h2 align="center">The Universal Multimedia Downloader</h2>

<p align="center">
  Download video and audio from over <b>1,000+ websites</b><br/>
  with one click
</p>

<br/>

<p align="center">
  <a href="https://github.com/Justagwas/MediaCrate/releases/latest/download/MediaCrateSetup.exe">
    <img
      src="https://img.shields.io/badge/▼%20Download%20MediaCrate%20for%20Windows%20▼-2563eb?style=for-the-badge&logo=windows&logoColor=white"
      alt="Download MediaCrate for Windows"
    >
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

## Built to be simple

MediaCrate is a fast, lightweight desktop application for downloading video and audio from supported websites.

Paste a link, choose your options, and download.

MediaCrate is built to be efficient:

- ~0% CPU usage while idle

- ~1% CPU usage during light downloads

- ~40 MB memory footprint

---

## 📋 Features

* One-click downloads with real-time status
* Single or batch downloads
* Output formats: MP4, MP3, MOV, WAV
* Video quality selection up to 4K (when available)
* Duplicate and invalid link detection in batch mode
* Safe cancel - active downloads stop instantly
* Custom download location
* Built-in console with timestamps and selectable text

---

## Easy to operate

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

Site support follows yt-dlp upstream updates.

See `MediaCrate/supportedsites.md` for the full list.

---

## 🎥 Preview

<details>
  <summary><strong>CLICK HERE TO EXPAND AND VIEW PREVIEW VIDEO</strong></summary>
  <video src="https://github.com/user-attachments/assets/ef20c013-5a79-41c1-9dc0-d40ba03d27ef" controls muted style="max-width: auto; height: auto;" ></video>
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
pyinstaller -F -w -i "icon.ico" --clean MediaCrate.py
```

The executable will be generated in the `dist/` directory.

</details>

---

## ⚠️ Security & Warnings

Operating systems may show warnings when downloading MediaCrate simply because it is not yet widely recognized.

MediaCrate is:

* Fully open source
* Local-only (no accounts, no telemetry)
* Limited strictly to the URLs you provide

You are encouraged to review the source code or scan the executable with any security tool of your choice.

If downloaded from the official repository or release page, it can be independently verified and safely used.

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

## 📜 License

Apache-2.0

See [`LICENSE`](https://github.com/Justagwas/MediaCrate?tab=Apache-2.0-1-ov-file) for details.

---

## 📬 Contact

[email@justagwas.com](mailto:email@justagwas.com)

---

MediaCrate is designed to be intuitive, fast, and simple.
