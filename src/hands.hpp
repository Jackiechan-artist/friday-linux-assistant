#ifndef HANDS_HPP
#define HANDS_HPP

/*
 * FridayHands — Low-level X11 input automation
 *
 * Provides mouse clicks, keyboard typing, and key presses using XTest.
 * The LADA action modules call these when the AI needs to interact with
 * on-screen UI elements (buttons, text fields, etc.).
 *
 * Requires: libx11-dev, libxtst-dev
 */

#include <X11/Xlib.h>
#include <X11/keysym.h>
#include <X11/extensions/XTest.h>
#include <unistd.h>
#include <string>
#include <iostream>

class FridayHands {
public:
    // Move the mouse to (x, y) and perform a left-click
    static void clickAt(int x, int y) {
        Display *display = XOpenDisplay(NULL);
        if (!display) return;
        XWarpPointer(display, None, DefaultRootWindow(display), 0, 0, 0, 0, x, y);
        XTestFakeButtonEvent(display, 1, True, 0);
        XTestFakeButtonEvent(display, 1, False, 0);
        XFlush(display);
        XCloseDisplay(display);
    }

    /*
     * Run a shell command in the background, suppressing all output.
     * Used by LADA when executing system tasks like opening apps or
     * running scripts that the AI decided to run.
     */
    static void forceExecute(std::string cmd) {
        cmd.erase(0, cmd.find_first_not_of(" \t\r\n"));
        cmd.erase(cmd.find_last_not_of(" \t\r\n") + 1);
        std::string final_cmd = cmd + " > /dev/null 2>&1 &";
        std::cout << "[EXEC] " << final_cmd << std::endl;
        system(final_cmd.c_str());
    }

    /*
     * Type a string character by character using XTest fake key events.
     * Works in any focused window — terminal, text editor, browser address bar, etc.
     * Each keystroke has a 20ms delay to avoid dropping characters.
     */
    static void typeText(std::string text) {
        Display *display = XOpenDisplay(NULL);
        if (!display) return;
        for (char& c : text) {
            KeyCode code;
            if      (c == ' ') code = XKeysymToKeycode(display, XK_space);
            else if (c == '-') code = XKeysymToKeycode(display, XK_minus);
            else if (c == '.') code = XKeysymToKeycode(display, XK_period);
            else               code = XKeysymToKeycode(display, XStringToKeysym(std::string(1, c).c_str()));

            if (code != 0) {
                XTestFakeKeyEvent(display, code, True, 0);
                XTestFakeKeyEvent(display, code, False, 0);
                XFlush(display);
                usleep(20000);
            }
        }
        XCloseDisplay(display);
    }

    // Press a single named key (e.g. "enter", "Tab", "Escape")
    static void pressKey(std::string key) {
        Display *display = XOpenDisplay(NULL);
        if (!display) return;
        KeySym sym = XStringToKeysym(key.c_str());
        if (key == "enter") sym = XK_Return;
        KeyCode code = XKeysymToKeycode(display, sym);
        XTestFakeKeyEvent(display, code, True, 0);
        XTestFakeKeyEvent(display, code, False, 0);
        XFlush(display);
        XCloseDisplay(display);
    }
};

#endif
