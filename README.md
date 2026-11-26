# LoL Shotcaller

**Zero-server, ad-free, real-time AI shotcaller & win-rate companion for League of Legends.**

Built to run **alongside** pre/post-game tools like Blitz, Porofessor, or U.GG — without overlapping their UI or hurting your FPS.

---

## Why LoL Shotcaller?

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
lol-shotcaller/
├── AGENTS.md            # Architecture & design specification
├── pyproject.toml       # Minimal dependencies (onnxruntime, numpy, pydantic)
├── assets/              # Verified 585B ONNX win-rate model & baseline stats
├── locales/             # Region/language packs (en.json, ko.json)
├── src/
│   ├── i18n.py          # OS locale auto-detection & template formatting
│   ├── predictor.py     # Zero-server local ONNX inference (<1ms)
│   ├── strategy_core.py # Language-neutral priority shotcaller engine
│   └── app.py           # Local 2999 poller + lightweight companion HUD server
└── tests/               # Unit tests for ONNX, i18n symmetry, and shotcalling
```

---

## Quick Start

```bash
uv sync --all-groups
uv run pytest -v
uv run python -m src.app --lang en
```

Then open `http://127.0.0.1:8787/overlay` (or launch the companion popup) while in Practice Tool, Replay, or a live match.
