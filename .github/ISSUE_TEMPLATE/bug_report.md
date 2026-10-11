---
name: Bug report
about: Something isn't working as expected
title: "[bug] "
labels: bug
---

**What happened**
A clear description of the bug, and what you expected instead.

**Steps to reproduce**
The exact command(s) you ran:

```bash
# e.g. auris tts "Me" --text "hi" --out hi.wav
```

**Error output**
Paste the full output / traceback (not a screenshot). Redact any auth token.

```
<output here>
```

**Environment**
- OS: <macOS 14 / Ubuntu 22.04 / Windows 11>
- How you use auris: <CLI on my machine | the shared HTTP service | remote user (dashboard/examples)>
- Install: <`./scripts/install.sh` with/without `--model` | `setup-workstation.sh` | client only>
- Device: <CPU | NVIDIA GPU (model) | Apple Silicon> — paste `auris info` if relevant
- auris version / commit: <`git rev-parse --short HEAD`>

**Anything else**
FFmpeg installed (`ffmpeg -version`)? espeak-ng (for presets)? Anything else that might matter.
