/*
 * FRIDAY Voice Assistant - Main Entry Point
 *
 * This is the C++ core that ties everything together. It runs in a continuous
 * loop listening for the wake word "Friday". Once heard, it captures the user's
 * voice, sends it to the Python STT module, passes the transcribed text to the
 * AI brain, and speaks the response back using Piper TTS.
 *
 * The program auto-detects its own directory at startup, so it works on any
 * machine regardless of where you cloned the repo.
 */

#include <iostream>
#include <vector>
#include <string>
#include <cmath>
#include <iomanip>
#include <unistd.h>
#include <cstdlib>
#include <climits>
#include <alsa/asoundlib.h>
#include <Python.h>

extern "C" {
#include "picovoice/pv_porcupine.h"
}

// Python function handles (loaded once at startup)
PyObject *pSTTFunc         = nullptr;  // recognize_raw()
PyObject *pSTTFuncConvOnly = nullptr;  // recognize_raw_google_only()
PyObject *pTTSFunc         = nullptr;  // speak()
PyObject *pSession         = nullptr;  // FridaySession instance

// Audio handles
snd_pcm_t      *pcm_handle = nullptr;
pv_porcupine_t *porcupine  = nullptr;
int             frame_len  = 0;
std::vector<int16_t> pcm_buf;

// Conversation state
bool isConversing  = false;
int  conv_retries  = 0;
const int MAX_CONV_RETRIES = 2;

// Pre-cached "Yes Sir?" audio for instant playback after wake word
std::string YES_SIR_CACHE = "/tmp/friday_yes_sir.raw";
bool yes_sir_cached = false;

/*
 * Returns true if the AI's reply is a real question (waiting for user response),
 * not a fallback/error phrase like "dobara bolein" or "samajh nahi".
 * This decides whether to stay in conversation mode or go back to standby.
 */
bool is_genuine_question(const std::string& reply) {
    if (reply.find("?") == std::string::npos) return false;
    const char* fallbacks[] = {
        "dobara bolein", "samajh nahi", "sunai nahi", "phir se bolein",
        "kuch sunai", "clear nahi", nullptr
    };
    std::string lower = reply;
    for (auto& c : lower) c = tolower(c);
    for (int i = 0; fallbacks[i]; i++) {
        if (lower.find(fallbacks[i]) != std::string::npos)
            return false;
    }
    return true;
}

// Root Mean Square of a PCM frame — used for voice activity detection
double rms(const int16_t* b, int n) {
    double s = 0;
    for (int i = 0; i < n; i++) s += (double)b[i] * b[i];
    return std::sqrt(s / n);
}

// Throw away N microphone frames to flush stale audio after TTS playback
void drain_mic(int frames = 30) {
    for (int i = 0; i < frames; i++)
        snd_pcm_readi(pcm_handle, pcm_buf.data(), frame_len);
}

/*
 * Keep reading mic frames until the room goes quiet or max_sec is reached.
 * Used after TTS so the assistant doesn't hear its own voice as a command.
 */
void drain_until_silent(float max_sec = 3.0f) {
    const double SILENT_RMS   = 900.0;
    const int    SILENT_COUNT = 15;
    int   quiet_streak = 0;
    float ms_per_frame = (float)frame_len / 16.0f;
    int   max_frames   = (int)(max_sec * 1000.0f / ms_per_frame);

    for (int i = 0; i < max_frames; i++) {
        int rc = snd_pcm_readi(pcm_handle, pcm_buf.data(), frame_len);
        if (rc < 0) { snd_pcm_recover(pcm_handle, rc, 0); continue; }
        if (rms(pcm_buf.data(), frame_len) < SILENT_RMS) {
            if (++quiet_streak >= SILENT_COUNT) break;
        } else {
            quiet_streak = 0;
        }
    }
}

/*
 * Renders "Yes Sir?" to a raw PCM file once at startup.
 * On subsequent wake-word detections we play that file instantly with aplay,
 * which is much faster than calling Piper each time.
 */
void pre_cache_yes_sir(const std::string& base) {
    std::string piper_dir = base + "/piper";
    std::string piper_exe = piper_dir + "/piper";
    std::string model_en  = base + "/models/tts/en_US-lessac-medium.onnx";

    std::string cmd =
        "export LD_LIBRARY_PATH=" + piper_dir + ":$LD_LIBRARY_PATH && "
        "echo 'Yes Sir?' | " + piper_exe + " --model " + model_en +
        " --output_raw 2>/dev/null > " + YES_SIR_CACHE;

    int r = system(cmd.c_str());
    if (r == 0 && access(YES_SIR_CACHE.c_str(), F_OK) == 0) {
        yes_sir_cached = true;
        std::cout << "[CACHE] 'Yes Sir?' audio ready" << std::endl;
    } else {
        std::cout << "[CACHE] Pre-cache failed — will call TTS each time" << std::endl;
    }
}

// Play the wake-word acknowledgement ("Yes Sir?") as fast as possible
void play_yes_sir() {
    if (yes_sir_cached) {
        std::string cmd = "aplay -r 22050 -f S16_LE -t raw -q " + YES_SIR_CACHE + " 2>/dev/null &";
        system(cmd.c_str());
        usleep(600000);
    } else if (pTTSFunc) {
        PyObject *a = PyUnicode_FromString("Yes Sir?");
        PyObject_CallOneArg(pTTSFunc, a);
        Py_DECREF(a);
        PyErr_Clear();
    }
}

/*
 * Send text to the Python TTS (Piper), then drain the mic for a duration
 * proportional to the word count so we don't pick up playback echo.
 */
void friday_speak(const std::string& txt) {
    if (!pTTSFunc || txt.empty()) return;

    PyObject *a = PyUnicode_FromString(txt.c_str());
    PyObject_CallOneArg(pTTSFunc, a);
    Py_DECREF(a);
    PyErr_Clear();

    int words = 1;
    for (char c : txt) if (c == ' ') words++;
    int drain_frames = std::max(80, words * 20);
    drain_mic(drain_frames);
    drain_until_silent(2.5f);
}

/*
 * Voice Activity Detection — captures one utterance from the mic.
 *
 * Algorithm:
 *   1. Wait for RMS to exceed START_THRESH (speech started).
 *   2. Keep a short pre-roll buffer so the first syllable isn't clipped.
 *   3. After speech starts, collect frames until SILENCE_END consecutive
 *      quiet frames are seen.
 *   4. Discard captures that had no real speech (noise only).
 *
 * Returns raw 16-bit PCM bytes, or empty vector on timeout/silence.
 */
std::vector<char> capture(float timeout_sec = 8.0f) {
    std::vector<char> stream;
    stream.reserve(16000 * 2 * 3);

    const double START_THRESH = 320.0;
    const double END_THRESH   = 180.0;
    const double NOISE_FLOOR  = 150.0;
    const int    SILENCE_END  = 12;
    const int    MIN_SPEECH   = 4;
    const int    PRE_ROLL_MAX = 6;

    int silence    = 0;
    int has_speech = 0;
    bool started   = false;
    std::vector<std::vector<char>> pre_roll;

    float ms_per_frame = (float)frame_len / 16.0f;
    int   max_wait     = (int)(timeout_sec * 1000.0f / ms_per_frame);

    for (int i = 0; i < max_wait; i++) {
        int rc = snd_pcm_readi(pcm_handle, pcm_buf.data(), frame_len);
        if (rc < 0) { snd_pcm_recover(pcm_handle, rc, 0); continue; }

        double v = rms(pcm_buf.data(), frame_len);

        if (!started) {
            if (v > NOISE_FLOOR) {
                std::vector<char> frame_copy(
                    (char*)pcm_buf.data(),
                    (char*)pcm_buf.data() + frame_len * 2
                );
                pre_roll.push_back(frame_copy);
                if ((int)pre_roll.size() > PRE_ROLL_MAX)
                    pre_roll.erase(pre_roll.begin());
            }
            if (v > START_THRESH) {
                started = true;
                silence = 0;
                for (auto& f : pre_roll)
                    stream.insert(stream.end(), f.begin(), f.end());
                pre_roll.clear();
            }
            continue;
        }

        stream.insert(stream.end(),
                      (char*)pcm_buf.data(),
                      (char*)pcm_buf.data() + (frame_len * 2));

        if (v < END_THRESH) {
            silence++;
        } else {
            silence = 0;
            has_speech++;
        }

        if (silence > SILENCE_END && has_speech > MIN_SPEECH) break;

        // If we hit double silence and barely any real speech, it was noise — reset
        if (silence > SILENCE_END * 2 && has_speech <= MIN_SPEECH) {
            stream.clear();
            started = false; silence = 0; has_speech = 0;
        }
    }

    if (has_speech <= MIN_SPEECH) return {};

    float dur = (float)stream.size() / (16000.0f * 2.0f);
    std::cout << "[VAD] " << std::fixed << std::setprecision(2) << dur
              << "s | " << has_speech << " frames" << std::endl;
    return stream;
}

/*
 * Speech-to-Text: sends raw PCM bytes to the Python STT module.
 * Uses recognize_raw_google_only during conversation (faster, Google only),
 * and recognize_raw otherwise (Google with Whisper fallback when offline).
 */
std::string do_stt(const std::vector<char>& audio) {
    if (audio.empty()) return "";
    PyObject *func = (isConversing && pSTTFuncConvOnly) ? pSTTFuncConvOnly : pSTTFunc;
    if (!func) return "";
    PyObject *pb = PyBytes_FromStringAndSize(audio.data(), audio.size());
    PyObject *pa = PyTuple_Pack(1, pb);
    PyObject *pr = PyObject_Call(func, pa, nullptr);
    Py_DECREF(pb); Py_DECREF(pa);
    std::string r;
    if (pr && PyUnicode_Check(pr)) r = PyUnicode_AsUTF8(pr);
    Py_XDECREF(pr);
    PyErr_Clear();
    return r;
}

/*
 * Sends transcribed text to the FridaySession Python object.
 * The brain handles routing to AI, LADA executor, web search, etc.
 * Returns the assistant's text reply (which gets spoken by friday_speak).
 */
std::string friday_process(const std::string& txt) {
    if (!pSession) return "";
    PyObject *k = PyUnicode_FromString("process");
    PyObject *a = PyUnicode_FromString(txt.c_str());
    PyObject *r = PyObject_CallMethodOneArg(pSession, k, a);
    Py_DECREF(k); Py_DECREF(a);
    std::string res;
    if (r && PyUnicode_Check(r)) res = PyUnicode_AsUTF8(r);
    Py_XDECREF(r);
    PyErr_Clear();
    return res;
}

/*
 * Main conversation loop (called once per wake-word or conversation turn).
 * Captures voice → STT → Brain → TTS. Manages the isConversing state so
 * multi-turn dialogue works without re-saying the wake word each time.
 */
void handle_flow() {
    float timeout = isConversing ? 6.0f : 10.0f;
    auto audio    = capture(timeout);

    if (audio.empty()) {
        if (isConversing) {
            std::cout << "[TIMEOUT] Going standby." << std::endl;
            friday_process("__TIMEOUT__");
            isConversing = false;
            conv_retries = 0;
        }
        return;
    }

    std::string user_text = do_stt(audio);

    if (user_text.size() < 2) {
        if (isConversing) {
            conv_retries++;
            if (conv_retries >= MAX_CONV_RETRIES) {
                friday_process("__TIMEOUT__");
                isConversing = false;
                conv_retries = 0;
            } else {
                friday_process("__EMPTY_STT__");
            }
        }
        return;
    }

    conv_retries = 0;
    std::string reply = friday_process(user_text);

    if (is_genuine_question(reply)) {
        isConversing = true;
        conv_retries = 0;
        std::cout << "[CONV] Waiting for follow-up (6s)..." << std::endl;
    } else {
        isConversing = false;
        conv_retries = 0;
        std::cout << "[STANDBY] Say 'Friday' to wake me." << std::endl;
    }
}

/*
 * Initializes the Python interpreter and imports the STT + brain modules.
 * base is the project root directory (resolved at runtime, not hardcoded).
 */
bool init_python(const std::string& base) {
    Py_Initialize();
    std::string cmd =
        "import sys\n"
        "sys.path.insert(0,'" + base + "/stt')\n"
        "sys.path.insert(0,'" + base + "')\n";
    PyRun_SimpleString(cmd.c_str());

    PyObject *stt = PyImport_ImportModule("google_stt");
    if (!stt) { PyErr_Print(); return false; }
    pSTTFunc         = PyObject_GetAttrString(stt, "recognize_raw");
    pSTTFuncConvOnly = PyObject_GetAttrString(stt, "recognize_raw_google_only");
    pTTSFunc         = PyObject_GetAttrString(stt, "speak");
    if (!pSTTFuncConvOnly) { PyErr_Clear(); pSTTFuncConvOnly = pSTTFunc; }
    Py_DECREF(stt);

    PyObject *brain = PyImport_ImportModule("friday_brain");
    if (!brain) { PyErr_Print(); return false; }
    PyObject *cls   = PyObject_GetAttrString(brain, "FridaySession");
    PyObject *kw    = PyDict_New();
    PyObject *empty = PyTuple_New(0);
    PyDict_SetItemString(kw, "tts_fn", pTTSFunc);
    pSession = PyObject_Call(cls, empty, kw);
    Py_DECREF(cls); Py_DECREF(kw); Py_DECREF(empty); Py_DECREF(brain);
    if (!pSession) { PyErr_Print(); return false; }
    return true;
}

// Opens the default ALSA capture device at 16kHz mono 16-bit
bool init_audio() {
    if (snd_pcm_open(&pcm_handle, "default", SND_PCM_STREAM_CAPTURE, 0) < 0) return false;
    return snd_pcm_set_params(pcm_handle,
                              SND_PCM_FORMAT_S16_LE,
                              SND_PCM_ACCESS_RW_INTERLEAVED,
                              1, 16000, 1, 500000) >= 0;
}

/*
 * Loads Porcupine with the wake word model (.ppn) and acoustic model (.pv).
 * key is the Picovoice access key from your .env / environment.
 */
bool init_porcupine(const std::string& models, const std::string& key) {
    std::string ppn = models + "/assistant.ppn";
    std::string pv  = models + "/porcupine_params.pv";
    const char *kp[] = { ppn.c_str() };
    float sens[]     = { 0.85f };
    return pv_porcupine_init(key.c_str(), pv.c_str(), "", 1, kp, sens, &porcupine)
           == PV_STATUS_SUCCESS;
}

/*
 * Resolves the absolute path to the directory containing this binary.
 * This is how we avoid hardcoded paths like /home/username/MyAssistant.
 */
std::string get_base_dir() {
    char buf[PATH_MAX];
    ssize_t len = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (len == -1) return ".";
    buf[len] = '\0';
    std::string exe_path(buf);
    // bin/assistant -> trim twice to reach project root
    size_t p = exe_path.rfind('/');
    if (p != std::string::npos) exe_path = exe_path.substr(0, p);
    p = exe_path.rfind('/');
    if (p != std::string::npos) exe_path = exe_path.substr(0, p);
    return exe_path;
}

int main() {
    // Resolve project root dynamically so any user can run this
    const std::string BASE = get_base_dir();

    // Read Picovoice key from environment (set via .env → sourced in run.sh)
    const char* pv_env = std::getenv("PICOVOICE_KEY");
    if (!pv_env || std::string(pv_env).empty()) {
        std::cerr << "[ERROR] PICOVOICE_KEY not set. Source your .env or run via ./scripts/run.sh\n";
        return 1;
    }
    const std::string PV_KEY = pv_env;

    std::cout << "\nFRIDAY v8.0 | Starting from: " << BASE << "\n" << std::endl;

    if (!init_python(BASE))                       { std::cerr << "Python init failed\n";    return 1; }
    if (!init_audio())                             { std::cerr << "Audio init failed\n";     return 1; }
    if (!init_porcupine(BASE + "/models", PV_KEY)) { std::cerr << "Porcupine init failed\n"; return 1; }

    frame_len = pv_porcupine_frame_length();
    pcm_buf.resize(frame_len);

    pre_cache_yes_sir(BASE);

    std::cout << "All systems online. Say 'Friday' to begin!\n" << std::endl;

    while (true) {
        int32_t idx = -1;

        if (!isConversing) {
            int rc = snd_pcm_readi(pcm_handle, pcm_buf.data(), frame_len);
            if (rc < 0) { snd_pcm_recover(pcm_handle, rc, 0); continue; }
            pv_porcupine_process(porcupine, pcm_buf.data(), &idx);
        } else {
            usleep(100000);
            idx = 0;
        }

        if (idx >= 0) {
            if (!isConversing) {
                std::cout << "[WAKE] Wake word detected!" << std::endl;
                drain_mic(25);
                drain_until_silent(1.5f);
                play_yes_sir();
                drain_mic(50);
                drain_until_silent(1.2f);
            }
            handle_flow();
            drain_until_silent(4.0f);
        }
    }

    pv_porcupine_delete(porcupine);
    snd_pcm_close(pcm_handle);
    Py_XDECREF(pSession);
    Py_Finalize();
    return 0;
}
