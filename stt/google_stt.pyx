"""
FRIDAY STT + TTS v7.3
google_stt.pyx

RECOMPILE ZARURI:
    cd ~/MyAssistant/stt
    python3 setup.py build_ext --inplace

Fixes v7.3:
  [Audio] Google STT ko sahi AudioData — 16kHz mono 16-bit
  [Audio] Longer audio capture better accuracy
  [P1]   Online hone par Whisper kabhi nahi
  [P3]   recognize_raw_google_only() conv mode
  [VAD]  Low threshold — normal voice pe react kare
"""

import speech_recognition as sr
from faster_whisper import WhisperModel
import socket
import numpy as np
import os
import subprocess
import re
import time
import sys

# [P11] TTS preprocessing — better Piper pronunciation
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

try:
    from friday_brain import preprocess_for_tts as _preprocess_tts
    _HAS_PREPROCESS = True
except ImportError:
    _HAS_PREPROCESS = False
    def _preprocess_tts(text):
        return text

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "models/whisper")
EN_TTS     = os.path.join(BASE_DIR, "models/tts/en_US-lessac-medium.onnx")
HI_TTS     = os.path.join(BASE_DIR, "models/tts/hi_IN-aditi-medium.onnx")
PIPER_EXE  = os.path.join(BASE_DIR, "piper/piper")
PIPER_DIR  = os.path.join(BASE_DIR, "piper")

# ── Whisper model ─────────────────────────────────────────────────────────
_WHISPER_MODEL_SIZE = os.environ.get("STT_WHISPER_MODEL", "tiny.en")
offline_model = WhisperModel(
    _WHISPER_MODEL_SIZE,
    device="cpu",
    compute_type="int8",
    download_root=MODEL_PATH
)

# ── Google STT recognizer ─────────────────────────────────────────────────
recognizer = sr.Recognizer()
recognizer.energy_threshold        = 200   # [Fix] Zyada sensitive — low-volume pe bhi sune
recognizer.dynamic_energy_threshold = False  # [Fix] Fixed threshold — auto-adjust mic ko confuse karta tha
recognizer.pause_threshold          = 0.6   # [Fix] Shorter pause = faster response
recognizer.non_speaking_duration    = 0.3

# ── Online cache — 10 sec TTL ──────────────────────────────────────────────
_online_cache      = True
_online_last_check = 0.0
_ONLINE_TTL        = 10.0

def is_online() -> bool:
    global _online_cache, _online_last_check
    now = time.monotonic()
    if now - _online_last_check < _ONLINE_TTL:
        return _online_cache
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(("8.8.8.8", 53))
        s.close()
        _online_cache = True
    except:
        _online_cache = False
    _online_last_check = now
    return _online_cache

# ── Garbage filter ────────────────────────────────────────────────────────
_GARBAGE_PATTERNS = [
    r"^\s*$",
    r"^(um+|uh+|hmm+|ah+|oh+)\.?$",
    r"^(\.+|\s+)$",
    r"^\[.*?\]$",
    r"^(thank you|thanks|okay|ok)\.?$",
]

def _is_garbage(text: str) -> bool:
    t = text.strip()
    if len(t) < 2:
        return True
    for p in _GARBAGE_PATTERNS:
        if re.match(p, t, re.IGNORECASE):
            return True
    return False

# ── TTS ───────────────────────────────────────────────────────────────────
def _has_devanagari(text: str) -> bool:
    return bool(re.search(r'[\u0900-\u097F]', text))

def _detect_tts_model(text: str) -> str:
    return HI_TTS if _has_devanagari(text) else EN_TTS

def speak(text: str):
    """Blocking TTS — [P11] with preprocessing for better pronunciation."""
    if not text:
        return
    # [P11] Use friday_brain preprocessor for better Piper pronunciation
    if _HAS_PREPROCESS:
        clean = _preprocess_tts(text)
    else:
        clean = re.sub(r'\[SCREEN DATA\].*', '', text, flags=re.DOTALL)
        clean = re.sub(r'\[\[.*?\]\]', '', clean, flags=re.DOTALL)
        clean = re.sub(r'\*+', '', clean)
        clean = re.sub(r'`+', '', clean)
        clean = re.sub(r'\n+', ' ', clean)
        clean = clean.strip()
    if not clean or len(clean) < 2:
        return
    model    = _detect_tts_model(clean)
    safe_txt = clean.replace('"', '\\"').replace("'", "\\'").replace('`', '')
    try:
        cmd = (
            f'export LD_LIBRARY_PATH={PIPER_DIR}:$LD_LIBRARY_PATH && '
            f'echo "{safe_txt}" | {PIPER_EXE} --model {model} --output_raw 2>/dev/null | '
            f'aplay -r 22050 -f S16_LE -t raw -q'
        )
        proc = subprocess.Popen(cmd, shell=True, executable='/bin/bash')
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except: pass
    except Exception as e:
        print(f"[TTS error] {e}")

def speak_blocking(text: str, timeout: float = 15.0):
    speak(text)

# ── Audio validation ──────────────────────────────────────────────────────
def _validate_audio(audio_bytes: bytes, sample_rate: int = 16000) -> bool:
    """
    Audio bytes check karo — noise/silence nahi hai.
    C++ se 16kHz mono 16-bit signed PCM aata hai.
    """
    if not audio_bytes or len(audio_bytes) < 3200:  # < 0.1 sec
        return False
    try:
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(audio_np ** 2)))
        duration_sec = len(audio_bytes) / (sample_rate * 2)
        print(f"[STT-Audio] size={len(audio_bytes)} duration={duration_sec:.2f}s rms={rms:.0f}")
        if rms < 50.0:  # Near-silence — not worth sending
            print(f"[STT-Audio] Too quiet (rms={rms:.0f}) — skipping")
            return False
        return True
    except:
        return True  # Assume valid if check fails

# ── Google STT ────────────────────────────────────────────────────────────
def _google_stt(audio_bytes: bytes, retries: int = 3) -> str:
    """
    [P12 Fix] Google STT with better retry, dual language, and timeout.
    C++ se aata hai: 16kHz, mono, 16-bit signed PCM
    """
    audio_data = sr.AudioData(audio_bytes, sample_rate=16000, sample_width=2)
    
    # [P12] Load STT config from env
    _timeout_s = int(os.environ.get("STT_TIMEOUT", "8"))
    _lang_primary = os.environ.get("STT_LANGUAGE", "en-IN")
    _retries = int(os.environ.get("STT_RETRY", str(retries)))

    for attempt in range(_retries + 1):
        try:
            # Primary: en-IN (Roman Hinglish + English)
            result = recognizer.recognize_google(
                audio_data,
                language=_lang_primary,
                show_all=False
            )
            if result and not _is_garbage(result):
                return result.strip()

            # Secondary: hi-IN (pure Hindi — Devanagari commands)
            try:
                r2 = recognizer.recognize_google(
                    audio_data,
                    language="hi-IN",
                    show_all=False
                )
                if r2 and not _is_garbage(r2):
                    print(f"[STT-Google-HI] {r2}")
                    return r2.strip()
            except:
                pass
            
            # Third attempt: en-US fallback
            if attempt == _retries - 1:
                try:
                    r3 = recognizer.recognize_google(
                        audio_data,
                        language="en-US",
                        show_all=False
                    )
                    if r3 and not _is_garbage(r3):
                        print(f"[STT-Google-US] {r3}")
                        return r3.strip()
                except:
                    pass
            
            return ""

        except sr.UnknownValueError:
            print(f"[STT-Google] UnknownValue attempt {attempt+1}/{_retries+1}")
            if attempt < _retries:
                time.sleep(0.2)
            else:
                return ""
        except sr.RequestError as e:
            print(f"[STT-Google] Network error: {e}")
            global _online_cache, _online_last_check
            _online_cache      = False
            _online_last_check = 0.0
            return ""
        except Exception as e:
            print(f"[STT-Google] Error: {e}")
            if attempt < _retries:
                time.sleep(0.3)
    return ""

# ── Whisper (offline only) ─────────────────────────────────────────────────
def _whisper_stt(audio_bytes: bytes) -> str:
    try:
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        rms_val  = float(np.sqrt(np.mean(audio_np ** 2)))
        if rms_val < 0.01:
            return ""
        segments, _ = offline_model.transcribe(
            audio_np,
            beam_size=1,
            language="en",
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_speech_duration_ms": 300, "max_silence_duration_ms": 800},
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
        )
        parts  = [seg.text for seg in segments if seg.no_speech_prob < 0.5]
        result = "".join(parts).strip()
        return result if (result and not _is_garbage(result)) else ""
    except Exception as e:
        print(f"[STT-Whisper] Error: {e}")
        return ""

# ── Main STT ──────────────────────────────────────────────────────────────
def recognize_raw(audio_bytes: bytes) -> str:
    """
    Normal STT.
    [P1 Fix] Online → Google only, Whisper NEVER online mein.
    """
    if not audio_bytes:
        return ""

    if not _validate_audio(audio_bytes):
        return ""

    if is_online():
        result = _google_stt(audio_bytes, retries=2)
        if result:
            print(f"[STT-Google] {result}")
        else:
            print(f"[STT-Google] No speech detected (online — Whisper skipped)")
        return result

    # Offline
    result = _whisper_stt(audio_bytes)
    if result:
        print(f"[STT-Whisper] {result}")
    return result

def recognize_raw_google_only(audio_bytes: bytes) -> str:
    """
    [P3 Fix] Conversation mode — Google only, 3 retries.
    """
    if not audio_bytes:
        return ""
    if not _validate_audio(audio_bytes):
        return ""
    if not is_online():
        print("[STT-Conv] Offline — skipped")
        return ""
    result = _google_stt(audio_bytes, retries=3)
    if result:
        print(f"[STT-Google-Conv] {result}")
    return result
