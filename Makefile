CXX      = g++
CXXFLAGS = -std=c++17 -O2 -Wall \
           -Iinclude \
           $(shell python3-config --includes)

LDFLAGS  = -Llib \
           -Wl,-rpath,$(shell pwd)/lib \
           -lpv_porcupine \
           -lasound \
           $(shell python3-config --embed --ldflags)

SRC = src/main.cpp
OBJ = build/main.o
BIN = bin/assistant

LIB_PORCUPINE = lib/libpv_porcupine.so
LIB_RECORDER  = lib/libpv_recorder.so
PV_PARAMS     = models/porcupine_params.pv
PYVER         = $(shell python3 -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')")
STT_SO        = stt/google_stt.cpython-$(PYVER)-x86_64-linux-gnu.so

PORCUPINE_REPO = https://github.com/Picovoice/porcupine/raw/master
PVRECORDER_URL = https://github.com/Picovoice/pvrecorder/raw/main/lib/linux/x86_64/libpv_recorder.so
PIPER_RELEASES = https://github.com/rhasspy/piper/releases/download/2023.11.14-2

# Whisper model size: tiny.en (default) | base.en | small.en
# Override: make WHISPER_MODEL=base.en
WHISPER_MODEL ?= tiny.en

# ── Default: download everything + compile + build ────────────────────────
all: deps stt $(BIN)

# ── Directories ───────────────────────────────────────────────────────────
lib models build:
	@mkdir -p $@
	@mkdir -p models/tts models/whisper bin

# ── Download .so files ────────────────────────────────────────────────────
$(LIB_PORCUPINE): | lib
	@echo "[DEPS] Downloading libpv_porcupine.so..."
	@wget -q --show-progress \
		"$(PORCUPINE_REPO)/lib/linux/x86_64/libpv_porcupine.so" \
		-O $@
	@echo "[DEPS] libpv_porcupine.so ready"

$(LIB_RECORDER): | lib
	@echo "[DEPS] Downloading libpv_recorder.so..."
	@wget -q --show-progress "$(PVRECORDER_URL)" -O $@
	@echo "[DEPS] libpv_recorder.so ready"

$(PV_PARAMS): | models
	@echo "[DEPS] Downloading porcupine_params.pv..."
	@wget -q --show-progress \
		"$(PORCUPINE_REPO)/lib/common/porcupine_params.pv" \
		-O $@
	@echo "[DEPS] porcupine_params.pv ready"

# ── Python packages ───────────────────────────────────────────────────────
python-deps:
	@echo "[DEPS] Installing Python packages..."
	@pip3 install -q cython numpy SpeechRecognition faster-whisper openai httpx python-dotenv 2>/dev/null || \
	 pip3 install --break-system-packages -q cython numpy SpeechRecognition faster-whisper openai httpx python-dotenv 2>/dev/null || \
	 echo "[WARN] Run: pip3 install -r requirements.txt"

deps: | lib models $(LIB_PORCUPINE) $(LIB_RECORDER) $(PV_PARAMS) python-deps

# ── Cython STT module ─────────────────────────────────────────────────────
stt: $(STT_SO)

$(STT_SO):
	@echo "[STT] Compiling google_stt.pyx..."
	@cd stt && python3 setup.py build_ext --inplace 2>&1 | grep -vE "^$|running|writing|gcc"
	@echo "[STT] Compiled"

# ── C++ binary ────────────────────────────────────────────────────────────
$(OBJ): $(SRC) | build
	$(CXX) $(CXXFLAGS) -c $(SRC) -o $(OBJ)

$(BIN): $(OBJ) $(LIB_PORCUPINE)
	$(CXX) $(OBJ) $(LDFLAGS) -o $(BIN)
	@echo ""
	@echo "  Build complete: $(BIN)"
	@[ -f .env ] || (cp .env.example .env && echo "  .env created — fill in your API keys")
	@[ -f models/assistant.ppn ] || echo "  MISSING: models/assistant.ppn  (see README)"
	@echo "  Run: source .env && ./scripts/run.sh"
	@echo ""

# ── Whisper model (optional, called by setup) ─────────────────────────────
whisper: | models
	@echo "[DEPS] Downloading Whisper $(WHISPER_MODEL)..."
	@python3 -c "from faster_whisper import WhisperModel; WhisperModel('$(WHISPER_MODEL)', device='cpu', compute_type='int8', download_root='$(shell pwd)/models/whisper')"
	@echo "[DEPS] Whisper $(WHISPER_MODEL) ready"

# ── Piper TTS voice models (optional, called by setup) ────────────────────
tts-models: | models
	@[ -f models/tts/en_US-lessac-medium.onnx ] || \
	 (echo "[TTS] Downloading English voice..." && \
	  wget -q --show-progress "$(PIPER_RELEASES)/voice-en_US-lessac-medium.tar.gz" -O /tmp/en_tts.tar.gz && \
	  tar -xzf /tmp/en_tts.tar.gz -C models/tts/ --strip-components=1 && \
	  rm /tmp/en_tts.tar.gz && echo "[TTS] English voice ready")
	@[ -f models/tts/hi_IN-aditi-medium.onnx ] || \
	 (echo "[TTS] Downloading Hindi voice..." && \
	  wget -q --show-progress "$(PIPER_RELEASES)/voice-hi_IN-aditi-medium.tar.gz" -O /tmp/hi_tts.tar.gz 2>/dev/null && \
	  tar -xzf /tmp/hi_tts.tar.gz -C models/tts/ --strip-components=1 2>/dev/null; \
	  rm -f /tmp/hi_tts.tar.gz; echo "[TTS] Hindi voice done")

# ── Full setup (deps + whisper + TTS models + build) ─────────────────────
setup: deps stt whisper tts-models $(BIN)
	@echo "[SETUP] All done. Edit .env, add models/assistant.ppn, then run: ./scripts/run.sh"

# ── Helpers ───────────────────────────────────────────────────────────────
system-deps:
	sudo apt-get install -y libasound2-dev python3-dev libx11-dev libxtst-dev \
		xdotool wmctrl scrot wget build-essential

clean:
	rm -f build/*.o bin/assistant
	cd stt && rm -f *.so *.c && rm -rf build/ 2>/dev/null; true

clean-all: clean
	rm -f lib/libpv_*.so models/porcupine_params.pv

.PHONY: all deps python-deps stt whisper tts-models setup clean clean-all system-deps
