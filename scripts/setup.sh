#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# FRIDAY AI Assistant — One-shot setup script
#
# What this does:
#   1. Installs system packages (ALSA, Python dev headers, X11 libs, tools)
#   2. Recompiles the Cython STT module (google_stt.pyx)
#   3. Downloads the Picovoice .so libraries from GitHub Releases
#   4. Downloads Porcupine acoustic model (porcupine_params.pv)
#   5. Asks which Whisper model to use and downloads it
#   6. Downloads Piper TTS voice models (English + Hindi)
#   7. Creates .env from .env.example if not present
#
# Run once after cloning:
#   chmod +x scripts/setup.sh
#   ./scripts/setup.sh
# ──────────────────────────────────────────────────────────────────────────

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "  FRIDAY AI Assistant — Setup"
echo "  Project root: $BASE"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────
info "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    libasound2-dev \
    python3-dev python3-pip \
    libx11-dev libxtst-dev \
    xdotool wmctrl scrot \
    wget curl \
    build-essential \
    2>&1 | grep -E "(Installing|already)" || true
success "System packages installed"

# ── 2. Python dependencies ────────────────────────────────────────────────
info "Installing Python packages..."
pip3 install --quiet --upgrade pip
pip3 install --quiet \
    cython numpy \
    SpeechRecognition \
    faster-whisper \
    openai \
    python-dotenv \
    httpx \
    opencv-python \
    pyatspi 2>/dev/null || warn "pyatspi not available via pip — install with: sudo apt install python3-pyatspi"
success "Python packages installed"

# ── 3. Compile Cython STT module ─────────────────────────────────────────
info "Compiling STT module (google_stt.pyx)..."
cd "$BASE/stt"
python3 setup.py build_ext --inplace 2>&1 | tail -3
success "STT module compiled"
cd "$BASE"

# ── 4. Download libpv_recorder.so ─────────────────────────────────────────
if [ ! -f "$BASE/lib/libpv_recorder.so" ]; then
    info "Downloading libpv_recorder.so..."
    PVR_URL="https://github.com/Picovoice/pvrecorder/releases/download/v1.2.2/pvrecorder-v1.2.2.tar.gz"
    TMP=$(mktemp -d)
    wget -q --show-progress "$PVR_URL" -O "$TMP/pvrecorder.tar.gz"
    tar -xzf "$TMP/pvrecorder.tar.gz" -C "$TMP"
    SO=$(find "$TMP" -name "libpv_recorder.so" -path "*/linux/x86_64/*" | head -1)
    if [ -z "$SO" ]; then
        # Fallback: try direct file download
        warn "Archive extract failed, trying direct download..."
        wget -q --show-progress \
            "https://github.com/Picovoice/pvrecorder/raw/main/lib/linux/x86_64/libpv_recorder.so" \
            -O "$BASE/lib/libpv_recorder.so"
    else
        cp "$SO" "$BASE/lib/libpv_recorder.so"
    fi
    rm -rf "$TMP"
    success "libpv_recorder.so downloaded"
else
    success "libpv_recorder.so already present"
fi

# ── 5. Download libpv_porcupine.so ────────────────────────────────────────
if [ ! -f "$BASE/lib/libpv_porcupine.so" ]; then
    info "Downloading libpv_porcupine.so..."
    PVVER="v3.0.3"
    wget -q --show-progress \
        "https://github.com/Picovoice/porcupine/raw/master/lib/linux/x86_64/libpv_porcupine.so" \
        -O "$BASE/lib/libpv_porcupine.so"
    success "libpv_porcupine.so downloaded"
else
    success "libpv_porcupine.so already present"
fi

# ── 6. Download porcupine_params.pv (acoustic model) ─────────────────────
mkdir -p "$BASE/models"
if [ ! -f "$BASE/models/porcupine_params.pv" ]; then
    info "Downloading Porcupine acoustic model..."
    wget -q --show-progress \
        "https://github.com/Picovoice/porcupine/raw/master/lib/common/porcupine_params.pv" \
        -O "$BASE/models/porcupine_params.pv"
    success "porcupine_params.pv downloaded"
else
    success "porcupine_params.pv already present"
fi

# ── 7. Wake word .ppn file ────────────────────────────────────────────────
if [ ! -f "$BASE/models/assistant.ppn" ]; then
    echo ""
    warn "Wake word model (models/assistant.ppn) is missing!"
    echo ""
    echo "  To create it:"
    echo "  1. Sign up (free) at https://console.picovoice.ai"
    echo "  2. Go to Porcupine Wake Word → Create a wake word → type 'Friday'"
    echo "  3. Download the .ppn file for Linux (x86_64)"
    echo "  4. Rename it to 'assistant.ppn' and place it in: $BASE/models/"
    echo ""
fi

# ── 8. Whisper model ──────────────────────────────────────────────────────
echo ""
echo "  Select Whisper model for offline speech recognition:"
echo "  (Used only when internet is unavailable)"
echo ""
echo "  1) tiny.en   ~75MB  — fastest, good for English"
echo "  2) base.en   ~145MB — balanced"
echo "  3) small.en  ~465MB — most accurate"
echo ""
read -rp "  Enter choice [1/2/3] (default: 1): " WHISPER_CHOICE
case "$WHISPER_CHOICE" in
    2) WHISPER_MODEL="base.en"  ;;
    3) WHISPER_MODEL="small.en" ;;
    *) WHISPER_MODEL="tiny.en"  ;;
esac
info "Downloading Whisper model: $WHISPER_MODEL"
mkdir -p "$BASE/models/whisper"
python3 -c "
from faster_whisper import WhisperModel
print('Downloading...')
WhisperModel('$WHISPER_MODEL', device='cpu', compute_type='int8', download_root='$BASE/models/whisper')
print('Done.')
"

# Update .env with selected model
if [ -f "$BASE/.env" ]; then
    sed -i "s/^STT_WHISPER_MODEL=.*/STT_WHISPER_MODEL=$WHISPER_MODEL/" "$BASE/.env"
fi
success "Whisper model ready"

# ── 9. Piper TTS voice models ─────────────────────────────────────────────
mkdir -p "$BASE/models/tts"
PIPER_RELEASES="https://github.com/rhasspy/piper/releases/download/2023.11.14-2"

if [ ! -f "$BASE/models/tts/en_US-lessac-medium.onnx" ]; then
    info "Downloading English TTS voice (en_US-lessac-medium)..."
    wget -q --show-progress \
        "$PIPER_RELEASES/voice-en_US-lessac-medium.tar.gz" \
        -O /tmp/en_tts.tar.gz
    tar -xzf /tmp/en_tts.tar.gz -C "$BASE/models/tts/" --strip-components=1
    rm /tmp/en_tts.tar.gz
    success "English TTS voice downloaded"
else
    success "English TTS voice already present"
fi

if [ ! -f "$BASE/models/tts/hi_IN-aditi-medium.onnx" ]; then
    info "Downloading Hindi TTS voice (hi_IN-aditi-medium)..."
    wget -q --show-progress \
        "$PIPER_RELEASES/voice-hi_IN-aditi-medium.tar.gz" \
        -O /tmp/hi_tts.tar.gz
    tar -xzf /tmp/hi_tts.tar.gz -C "$BASE/models/tts/" --strip-components=1 2>/dev/null || \
        warn "Hindi voice download failed — English only mode will work fine"
    rm -f /tmp/hi_tts.tar.gz
else
    success "Hindi TTS voice already present"
fi

# ── 10. .env file ─────────────────────────────────────────────────────────
if [ ! -f "$BASE/.env" ]; then
    cp "$BASE/.env.example" "$BASE/.env"
    echo ""
    warn ".env created from .env.example"
    echo "  Open .env and fill in your API keys before running:"
    echo "  - PICOVOICE_KEY  → https://console.picovoice.ai (free)"
    echo "  - NVIDIA_KEY_*   → https://build.nvidia.com (free tier)"
    echo "  - GROQ_KEY       → https://console.groq.com (free)"
    echo ""
else
    success ".env already exists"
fi

# ── 11. Build C++ binary ──────────────────────────────────────────────────
info "Building C++ assistant binary..."
cd "$BASE"
make clean 2>/dev/null || true
make
success "Binary compiled: bin/assistant"

echo ""
echo -e "${GREEN}  Setup complete!${NC}"
echo ""
echo "  Next steps:"
echo "  1. Edit .env and add your API keys"
echo "  2. Place your assistant.ppn wake word file in models/"
echo "  3. Run: ./scripts/run.sh"
echo ""
