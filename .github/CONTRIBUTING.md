# Contributing to MediaCrate

Thanks for your interest in contributing to MediaCrate.

## Before You Start

1. Read `README.md`.
2. Search existing issues before opening a new one.
3. Keep changes focused and easy to review.

## Reporting Bugs

Open a bug issue and include:

- What happened
- Expected behavior
- Reproduction steps
- App version (`v2.x.x`)
- OS version
- Screenshots or logs (if available)

## Suggesting Features

Open a feature request and include:

- Problem statement
- Proposed solution
- Why it helps users
- Any alternatives considered

## Pull Requests

1. Fork the repository.
2. Create a branch:

```bash
git checkout -b feature/short-name
```

3. Make and test your changes.
4. Commit with a clear message.
5. Open a pull request with a concise summary.

## Local Development

App source lives in the nested `MediaCrate/` folder.

```powershell
cd MediaCrate
py -m pip install -r requirements.txt
py MediaCrate.py
```

## Code Guidelines

- Keep changes scoped.
- Preserve existing behavior unless intentionally changing it.
- Update docs if behavior or commands changed.

Thanks for helping improve MediaCrate.
