"""
Control real Android phones from the PC over USB (ADB).

One-time setup
  1. Install "Android SDK Platform-Tools" (it contains adb.exe) and add its folder
     to PATH, or set the ADB_PATH environment variable to the full path of adb.exe.
  2. On each phone: Settings -> About phone -> tap "Build number" 7 times, then
     Settings -> Developer options -> turn on "USB debugging".
  3. Plug the phone in, accept the "Allow USB debugging?" prompt on the phone.
  4. Check it works:   python core/phone_control.py devices

Quick tests from a terminal
     python core/phone_control.py devices
     python core/phone_control.py unlock 1234          # your phone PIN (optional)
     python core/phone_control.py open youtube
     python core/phone_control.py youtube "lofi music"
     python core/phone_control.py home
     python core/phone_control.py shot phone.png

Every function takes serial=None. None means "every connected phone", so one voice
command can drive all of them at once; pass a serial to target a single phone.
Nothing is stored: a PIN is only typed when you pass one.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

# Spoken names -> Android package names (add your own).
APPS = {
    "youtube": "com.google.android.youtube",
    "chrome": "com.android.chrome",
    "whatsapp": "com.whatsapp",
    "instagram": "com.instagram.android",
    "facebook": "com.facebook.katana",
    "telegram": "org.telegram.messenger",
    "spotify": "com.spotify.music",
    "maps": "com.google.android.apps.maps",
    "gmail": "com.google.android.gm",
    "photos": "com.google.android.apps.photos",
    "play store": "com.android.vending",
    "settings": "com.android.settings",
    "camera": "com.android.camera",
    "phone": "com.google.android.dialer",
    "messages": "com.google.android.apps.messaging",
    "clock": "com.google.android.deskclock",
    "calculator": "com.google.android.calculator",
    "netflix": "com.netflix.mediaclient",
    "snapchat": "com.snapchat.android",
    "tiktok": "com.zhiliaoapp.musically",
}

KEYS = {"home": 3, "back": 4, "recents": 187, "power": 26, "wake": 224, "sleep": 223,
        "volume up": 24, "volume down": 25, "mute": 164, "play pause": 85,
        "next": 87, "previous": 88, "enter": 66, "menu": 82}


class PhoneError(RuntimeError):
    pass


def _adb_exe() -> str:
    exe = os.environ.get("ADB_PATH") or shutil.which("adb")
    if not exe:
        raise PhoneError("adb was not found. Install Android platform-tools and add it "
                         "to PATH (or set ADB_PATH). See core/phone_control.py.")
    return exe


def _run(args: list[str], serial: str | None = None, timeout: float = 15.0,
         binary: bool = False):
    cmd = [_adb_exe()]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise PhoneError("The phone did not answer in time.")
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip()
        raise PhoneError(msg or f"adb failed: {' '.join(args)}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def devices() -> list[tuple[str, str]]:
    """[(serial, state)] -- state is 'device' when ready, 'unauthorized' if the
    'Allow USB debugging' prompt has not been accepted yet."""
    out = _run(["devices"])
    res = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            res.append((parts[0], parts[1]))
    return res


def ready() -> list[str]:
    return [s for s, st in devices() if st == "device"]


def _targets(serial: str | None) -> list[str]:
    if serial:
        return [serial]
    r = ready()
    if not r:
        raise PhoneError("No phone is connected. Plug one in with USB debugging on "
                         "(check with: python core/phone_control.py devices).")
    return r


def _each(serial, fn):
    """Run fn(serial) on one phone or all of them; returns a short report."""
    out = []
    for s in _targets(serial):
        try:
            fn(s)
            out.append(f"{s}: ok")
        except PhoneError as e:
            out.append(f"{s}: {e}")
    return "; ".join(out)


# ------------------------------------------------------------------ actions
def screen_size(serial: str) -> tuple[int, int]:
    m = re.search(r"(\d+)x(\d+)", _run(["shell", "wm", "size"], serial))
    return (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)


def key(name_or_code, serial: str | None = None) -> str:
    code = KEYS.get(str(name_or_code).lower().strip(), name_or_code)
    return _each(serial, lambda s: _run(["shell", "input", "keyevent", str(code)], s))


def tap(x: int, y: int, serial: str | None = None) -> str:
    return _each(serial, lambda s: _run(["shell", "input", "tap", str(x), str(y)], s))


def swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300, serial: str | None = None) -> str:
    return _each(serial, lambda s: _run(
        ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms)], s))


def scroll(direction: str = "down", serial: str | None = None) -> str:
    def go(s):
        w, h = screen_size(s)
        up = direction.lower() == "up"
        a, b = (int(h * 0.30), int(h * 0.70)) if up else (int(h * 0.70), int(h * 0.30))
        _run(["shell", "input", "swipe", str(w // 2), str(a), str(w // 2), str(b), "300"], s)
    return _each(serial, go)


def type_text(text: str, serial: str | None = None) -> str:
    safe = re.sub(r"([&|;<>()$`\\\"'*?\[\]{}~#])", r"\\\1", text).replace(" ", "%s")
    return _each(serial, lambda s: _run(["shell", "input", "text", safe], s))


def unlock(pin: str | None = None, serial: str | None = None) -> str:
    """Wake the screen, swipe the lock screen up, and type the PIN if one is given."""
    def go(s):
        _run(["shell", "input", "keyevent", "224"], s)
        w, h = screen_size(s)
        _run(["shell", "input", "swipe", str(w // 2), str(int(h * 0.85)),
              str(w // 2), str(int(h * 0.25)), "250"], s)
        if pin:
            _run(["shell", "input", "text", re.sub(r"\D", "", pin)], s)
            _run(["shell", "input", "keyevent", "66"], s)
    return _each(serial, go)


def lock(serial: str | None = None) -> str:
    return key("sleep", serial)


def open_app(name: str, serial: str | None = None) -> str:
    """Open an app by spoken name ('youtube') or by package ('com.whatsapp')."""
    pkg = APPS.get(name.lower().strip(), name.strip())
    return _each(serial, lambda s: _run(
        ["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], s))


def open_url(url: str, serial: str | None = None) -> str:
    quoted = "'" + url.replace("'", "%27") + "'"      # the phone's shell would eat ? and &
    return _each(serial, lambda s: _run(
        ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", quoted], s))


def youtube_search(query: str, serial: str | None = None) -> str:
    return open_url("https://www.youtube.com/results?search_query=" + quote_plus(query), serial)


def call(number: str, serial: str | None = None) -> str:
    """Opens the dialler with the number typed in (never dials by itself)."""
    num = re.sub(r"[^\d+]", "", number)
    return _each(serial, lambda s: _run(
        ["shell", "am", "start", "-a", "android.intent.action.DIAL", "-d", f"tel:{num}"], s))


def screenshot(path: str = "phone.png", serial: str | None = None) -> str:
    s = _targets(serial)[0]
    data = _run(["exec-out", "screencap", "-p"], s, timeout=20, binary=True)
    Path(path).write_bytes(data)
    return str(Path(path).resolve())


def status() -> str:
    d = devices()
    if not d:
        return "No phones detected."
    return "; ".join(f"{s} ({st})" for s, st in d)


# --------------------------------------------------------------------- CLI
def _cli(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    c, rest = argv[0].lower(), argv[1:]
    try:
        if c == "devices":
            print(status())
        elif c == "unlock":
            print(unlock(rest[0] if rest else None))
        elif c == "lock":
            print(lock())
        elif c == "open":
            print(open_app(" ".join(rest)))
        elif c == "youtube":
            print(youtube_search(" ".join(rest)))
        elif c == "url":
            print(open_url(rest[0]))
        elif c == "call":
            print(call(rest[0]))
        elif c == "type":
            print(type_text(" ".join(rest)))
        elif c == "scroll":
            print(scroll(rest[0] if rest else "down"))
        elif c == "shot":
            print(screenshot(rest[0] if rest else "phone.png"))
        elif c in KEYS:
            print(key(c))
        else:
            print("Unknown command. Run without arguments for help.")
            return 2
    except PhoneError as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))