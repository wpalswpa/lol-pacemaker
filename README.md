# LoL Pacemaker

**Zero-server, ad-free, real-time AI shotcaller & win-rate companion for League of Legends.**

Built to run **alongside** pre/post-game tools like Blitz, Porofessor, or U.GG — without overlapping their UI or hurting your FPS.

---

## Why LoL Pacemaker?

Existing League apps excel at **pre-game** (runes, bans, matchups) and **post-game** stats. What's missing is an ultra-light **in-game macro shotcaller** that reads the live board every 2 seconds and tells you what actually matters *right now*:

- **Jungle Smite Tracking**: Instant **"Free Baron — Enemy JG Dead (32s)"** call when enemy Smite is off the map, and **"Do NOT 50/50"** warnings when your jungler is down.
- **Respawn-Timed Push Windows**: Computes exact numerical advantage & max enemy respawn timer (`5v3 · 28s window`) so you know whether to force Baron, take towers, or reset.
- **Dragon Soul & Voidgrubs Tempo**: Alerts you 45s before Soul Point / Enemy Soul Threat and tracks Voidgrub / Herald windows.
- **Carry Grouping & Gold Recall Spikes**: Identifies your team's fed carry lane and flags optimal recall gold timings without cluttering the screen.
- **$0 Server Cost & <1ms Local ONNX**: Runs a 585-byte ONNX win-rate model locally on your CPU (`onnxruntime`). Zero cloud latency, zero ads, and zero gameplay data leaving your PC.
- **Multi-Language (`i18n`)**: Automatically detects your locale (`en`, `ko`, and extensible via `locales/*.json`).

---

## Riot Policy & Fair-Play Guarantee

- **Read-only Official Local API**: Uses only `https://127.0.0.1:2999/liveclientdata/allgamedata` (public Tab scoreboard & events).
- **No Unfair Advantage**: Never reads hidden enemy gold, wards, fog-of-war positions, or enemy cooldowns.
- **No Memory Injection / No OCR**: Runs as a standalone companion overlay window (`F8` click-through, `F9` hide).

---

## Project Structure

```
lol-pacemaker/
├── AGENTS.md            # Architecture & design specification (source of truth)
├── pacemaker.py         # PyInstaller entry point
├── assets/              # Bundled 8-feature ONNX fallback (+ model_extended.onnx / distribution_stats.json once trained)
├── locales/             # en / ko / ja / es / pt language packs (keys kept 100% symmetric by tests)
├── src/
│   ├── main.py          # Entry point: overlay (default) or --web browser HUD
│   ├── engine.py        # 2s Live Client poller -> snapshot -> localized result
│   ├── overlay.py       # Tk transparent HUD (drag, opacity, F8 click-through, F9 hide, language chip)
│   ├── hud_state.py     # HUD settings & clamping (pure logic, tested)
│   ├── predictor.py     # ONNX inference: updated model -> 18-feature -> 8-feature fallback; win-rate drivers
│   ├── strategy_core.py # Priority shotcaller + Top-% / cause / action briefing
│   ├── percentile.py    # Tier-distribution Top-% (math.erf, no SciPy)
│   ├── updater.py       # HTTPS + SHA-256 verified, atomic model updates from GitHub Releases
│   └── i18n.py
├── ml/                  # Offline only: collector (Riot API -> JSONL), features, train (ONNX + distribution_stats.json)
├── scripts/             # build.py (PyInstaller), make_checksums.py
├── .github/workflows/release.yml   # tag-triggered clean builds -> draft release
├── web/index.html       # 5-language static download page
└── tests/
```

---

## Quick Start

```bash
uv sync --all-groups
uv run python -m pytest -v            # full suite
uv run python -m src.main --lang en --tier mid      # overlay window
uv run python -m src.main --web                     # or the browser HUD: http://127.0.0.1:8787/overlay
```

HUD keys: **F8** click-through · **F9** hide/show · **[ ]** opacity · drag to move · click the language chip to cycle languages.

## Training a model (offline, needs your own Riot API key)

```bash
export RIOT_API_KEY=...                                   # never commit or log it
uv run python -m ml.collector --platform kr --bucket mid --max-matches 500 --patch 16.5
uv run python -m ml.train --data data/snapshots.jsonl --out build/model
```

The collector resumes from `data/checkpoint.json`. Copy `model_extended.onnx` and `distribution_stats.json` to `assets/` (or attach them to a release together with `checksums.sha256`) and the app picks them up.

## Build & release

```bash
uv run python scripts/build.py        # -> dist/LoLPacemaker-<windows|macos>.zip
```

Pushing a `v*` tag runs the release workflow (tests -> Windows/macOS builds -> `checksums.sha256` -> **draft** release).
Binaries are unsigned, so Windows SmartScreen / macOS Gatekeeper show a first-run warning.
