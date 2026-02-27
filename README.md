# FRIDAY — AI Voice Assistant for Linux

A wake-word activated voice assistant for Linux. Say "Friday" to wake it up, then give any command in English or Hinglish. It can control your desktop, browse the web, answer questions, and execute multi-step tasks autonomously.

---

## How It Works

```
Mic → Porcupine (wake word) → ALSA capture → STT (Google / Whisper)
    → AI Brain (NVIDIA NIM / OpenRouter / Groq) → LADA executor
    → Piper TTS → Speaker
```

- **Wake word detection** runs entirely on-device using Picovoice Porcupine
- **STT** uses Google Speech Recognition when online, Whisper when offline
- **Three-brain AI architecture**: Brain 1 routes and chats, Brain 2 (LADA) plans and executes desktop tasks, Brain 3 polishes responses
- **TTS** uses Piper with a local neural voice model — no cloud calls for speech

---

## Project Structure

```
MyAssistant/
├── src/
│   ├── main.cpp          # C++ core: wake word loop, VAD, STT/TTS bridge
│   └── hands.hpp         # X11 input automation (click, type, key press)
├── include/
│   └── picovoice/        # Porcupine C headers
├── lib/                  # .so files (auto-downloaded by setup.sh)
├── piper/                # Piper TTS binary + shared libs + espeak-ng-data
├── models/
│   ├── assistant.ppn     # Wake word model (you create this, see setup)
│   ├── porcupine_params.pv  # Acoustic model (auto-downloaded)
│   ├── whisper/          # Whisper STT models (auto-downloaded)
│   └── tts/              # Piper voice models (auto-downloaded)
├── stt/
│   ├── google_stt.pyx    # Cython: STT + TTS entry points called from C++
│   ├── friday_brain.py   # Main AI brain — routes to LLM / LADA / rules
│   ├── eyes_reader.py    # Screen context reader (active window, URL, etc.)
│   ├── app_discovery.py  # Scans .desktop files to find installed apps
│   ├── brain.py          # Standalone brain (interactive / single command)
│   ├── prompt.py         # Loads BOSS_NAME / ASSISTANT_NAME from .env
│   ├── setup.py          # Cython build script for google_stt.pyx
│   └── lada_v2/          # LADA autonomous desktop agent
│       ├── brain.py      # LADA entry point — shell vs UI routing
│       ├── lasa_agent.py # LASA: screen + accurate input agent
│       ├── actions/      # system_actions, browser_actions, ui_actions, youtube
│       ├── core/         # orchestrator, planner, step_executor, verifier, etc.
│       ├── memory/       # plan_cache, context_store, learning_engine
│       ├── perception/   # screen_reader, accessibility, browser_dom, cv_detector
│       └── utils/        # logger, retry_policy, timeout, watchdog, etc.
├── scripts/
│   ├── setup.sh          # One-shot install + download everything
│   └── run.sh            # Load .env and start the assistant
├── Makefile
├── requirements.txt
├── .env.example          # Copy to .env and fill in your keys
└── .gitignore
```

---

## Requirements

- **OS**: Linux x86_64 (Ubuntu 20.04+ / Debian 11+ / Linux Mint 20+)
- **Python**: 3.9 or newer
- **Microphone**: any ALSA-compatible mic
- **Internet**: needed for Google STT and LLM calls; Whisper works offline

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/yourusername/MyAssistant.git
cd MyAssistant
```

### 2. Run the setup script

```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
```

This will:
- Install system packages (ALSA, X11, build tools, xdotool, wmctrl)
- Compile the Cython STT module
- Download `libpv_recorder.so` and `libpv_porcupine.so` from GitHub Releases
- Download the Porcupine acoustic model
- Ask which Whisper model you want (tiny / base / small) and download it
- Download Piper TTS voice models (English + Hindi)
- Create `.env` from `.env.example`

### 3. Create your wake word model (.ppn)

The wake word "Friday" needs a personal model file tied to your Picovoice account:

1. Sign up (free) at [console.picovoice.ai](https://console.picovoice.ai)
2. Go to **Porcupine Wake Word** → **Create New Wake Word**
3. Type `friday` as the wake phrase
4. Download the `.ppn` file for **Linux (x86_64)**
5. Rename it to `assistant.ppn` and place it at `models/assistant.ppn`

### 4. Configure .env

```bash
nano .env
```

Fill in at minimum:

| Key | Where to get it |
|-----|----------------|
| `PICOVOICE_KEY` | [console.picovoice.ai](https://console.picovoice.ai) — free |
| `NVIDIA_KEY_BRAIN1` | [build.nvidia.com](https://build.nvidia.com) — free tier |
| `GROQ_KEY` | [console.groq.com](https://console.groq.com) — free |

You can use OpenRouter instead of NVIDIA if you prefer. Set `OPENROUTER_KEY` and change `BRAIN1_BASE_URL` to `https://openrouter.ai/api/v1`.

### 5. Build and run

```bash
./scripts/run.sh
```

Or manually:

```bash
make
source .env
export LD_LIBRARY_PATH="$PWD/lib:$PWD/piper:$LD_LIBRARY_PATH"
./bin/assistant
```

---

## API Keys — Paid vs Free

All supported providers have a **free tier** that is sufficient for personal use:

| Provider | Free tier | Used for |
|----------|-----------|---------|
| [Picovoice](https://console.picovoice.ai) | Yes, unlimited personal | Wake word |
| [NVIDIA NIM](https://build.nvidia.com) | 1,000 credits/month | AI brain |
| [Groq](https://console.groq.com) | Generous free tier | Fallback AI |
| [OpenRouter](https://openrouter.ai) | Pay-per-token, cheap | Optional fallback |

---

## Voice Commands — Examples

```
"Friday"                         → wake up
"Chrome kholo"                   → open Chrome
"YouTube par lofi music chala"   → open YouTube and search
"Mera screen kya hai"            → describe active window
"Google karo latest AI news"     → web search
"Volume band karo"               → mute system audio
"Ruko" / "Stop"                  → interrupt any running task
```

---

## Troubleshooting

**Porcupine init failed**
- Make sure `PICOVOICE_KEY` is set in `.env`
- Make sure `models/assistant.ppn` exists

**Audio init failed**
- Check `arecord -l` lists your microphone
- Try `export ALSA_PCM_CARD=1` if using a USB mic

**STT not working**
- Google STT needs internet. If offline, Whisper is used automatically
- Raise `STT_ENERGY_THRESHOLD` in `.env` if in a noisy environment

**TTS silent**
- Check `models/tts/en_US-lessac-medium.onnx` exists
- Run `aplay /dev/urandom -t raw -r 22050 -f S16_LE` to test your speaker

**Recompile STT after Python update**
```bash
cd stt
python3 setup.py build_ext --inplace
```

---

## Architecture — Detailed

### C++ Core (`src/main.cpp`)
The entry point. Runs a tight loop reading Porcupine frames from ALSA. On wake word detection, switches to VAD capture, sends PCM bytes to Python, handles TTS drain. Manages conversation state so multi-turn dialogue doesn't require re-waking.

### Python STT + TTS (`stt/google_stt.pyx`)
Compiled with Cython for performance. Exports `recognize_raw()` (online: Google, offline: Whisper), `recognize_raw_google_only()` (conversation mode), and `speak()` (Piper TTS).

### AI Brain (`stt/friday_brain.py`)
Routes each utterance through a decision tree: rule-based instant responses first, then LLM for complex queries, LADA for desktop tasks. Manages a three-brain pipeline with fallback chains.

### LADA (`stt/lada_v2/`)
Autonomous desktop agent. The planner breaks multi-step tasks into a step graph, the executor runs each step (shell command or UI interaction), the verifier checks screen state after each step, and the recovery module replans on failure.

---

## License

MIT
