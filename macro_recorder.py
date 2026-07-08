"""\nMacroRecorder v16.0.3.0 - Sends input to a target window by name,\nworks even when MacroRecorder itself is in the foreground.\n"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json, time, threading, os
from datetime import datetime

try:
    from pynput import mouse, keyboard
    from pynput.mouse import Button, Controller as MouseController
    from pynput.keyboard import Key, Controller as KeyboardController
    PYNPUT_OK = True
except ImportError:
    PYNPUT_OK = False

try:
    import win32gui, win32con, win32api, win32process
    import ctypes
    WIN32_OK = True
except ImportError:
    WIN32_OK = False

try:
    import schedule
    SCHEDULE_OK = True
except ImportError:
    SCHEDULE_OK = False


# ─────────────────────────────────────────────
#  WINDOW TARGETING HELPERS (Windows only)
# ─────────────────────────────────────────────

def list_windows():
    """Return list of (hwnd, title) for all visible windows."""
    results = []
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                results.append((hwnd, title))
    if WIN32_OK:
        win32gui.EnumWindows(callback, None)
    return results

# ── ctypes SendInput structures ───────────────
# SendInput works at the driver level — it's the only way to do
# real drag-and-drop and mouse movement that every app responds to.

if WIN32_OK:
    import ctypes.wintypes as wintypes

    MOUSEEVENTF_MOVE        = 0x0001
    MOUSEEVENTF_LEFTDOWN    = 0x0002
    MOUSEEVENTF_LEFTUP      = 0x0004
    MOUSEEVENTF_RIGHTDOWN   = 0x0008
    MOUSEEVENTF_RIGHTUP     = 0x0010
    MOUSEEVENTF_ABSOLUTE    = 0x8000

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        class _INPUT(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT)]
        _anonymous_ = ("_input",)
        _fields_ = [("type", wintypes.DWORD), ("_input", _INPUT)]

    # Tell Windows this process is DPI-aware so coordinates are physical pixels
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    def _get_physical_screen_size():
        """
        Return the PRIMARY monitor's true physical pixel dimensions.
        Uses SM_CXSCREEN/SM_CYSCREEN which, after SetProcessDpiAwareness(2),
        returns physical pixels — not logical/scaled values.
        """
        w = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
        h = ctypes.windll.user32.GetSystemMetrics(1)   # SM_CYSCREEN
        return w, h

    def _send_mouse_input(flags, x=0, y=0):
        """Send a mouse event via SendInput using physical pixel coordinates."""
        # SendInput absolute coords must be in range 0-65535 mapped to physical screen
        screen_w, screen_h = _get_physical_screen_size()
        abs_x = int((x * 65535) / (screen_w - 1)) if screen_w > 1 else 0
        abs_y = int((y * 65535) / (screen_h - 1)) if screen_h > 1 else 0
        # Clamp to valid range
        abs_x = max(0, min(65535, abs_x))
        abs_y = max(0, min(65535, abs_y))
        inp = INPUT(type=0)  # INPUT_MOUSE = 0
        inp.mi = MOUSEINPUT(dx=abs_x, dy=abs_y, mouseData=0,
                            dwFlags=flags, time=0, dwExtraInfo=None)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def send_mouse_move(x, y):
    if not WIN32_OK: return
    _send_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, x, y)

def send_mouse_down(x, y, button="left"):
    if not WIN32_OK: return
    _send_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, x, y)
    flag = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    _send_mouse_input(flag | MOUSEEVENTF_ABSOLUTE, x, y)

def send_mouse_up(x, y, button="left"):
    if not WIN32_OK: return
    _send_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, x, y)
    flag = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
    _send_mouse_input(flag | MOUSEEVENTF_ABSOLUTE, x, y)

def send_click_to_window(hwnd, x, y, button="left"):
    """For plain clicks we still use SendInput for consistency."""
    send_mouse_down(x, y, button)
    time.sleep(0.02)
    send_mouse_up(x, y, button)

# Keys that should NEVER send WM_CHAR (function keys, arrows, etc.)
_NON_PRINTABLE_PREFIXES = ("Key.",)

def send_key_to_window(hwnd, key_str, press=True):
    """Send a keypress to a window via PostMessage.

    IMPORTANT: we only ever post WM_KEYDOWN / WM_KEYUP here, never WM_CHAR.
    The target window's own message loop calls TranslateMessage() on the
    WM_KEYDOWN we post, which automatically generates and posts its own
    WM_CHAR for printable keys. If we ALSO post a manual WM_CHAR (as this
    function used to), the control receives two WM_CHAR messages for a
    single keystroke and every printable character — digits included —
    gets typed twice during playback.
    """
    if not WIN32_OK or not hwnd:
        return
    vk = _vk_code(key_str)
    if vk is None:
        return
    if press:
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
    else:
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0)

def send_scroll_to_window(hwnd, x, y, dy):
    if not WIN32_OK or not hwnd:
        return
    client_x, client_y = win32gui.ScreenToClient(hwnd, (x, y))
    lparam = win32api.MAKELONG(client_x, client_y)
    delta = int(dy * 120)
    wparam = win32api.MAKELONG(0, delta)
    win32gui.SendMessage(hwnd, win32con.WM_MOUSEWHEEL, wparam, lparam)

def _vk_code(key_str):
    """Map pynput key string to virtual key code."""
    special = {
        "Key.space": 0x20, "Key.enter": 0x0D, "Key.backspace": 0x08,
        "Key.tab": 0x09, "Key.esc": 0x1B, "Key.shift": 0x10,
        "Key.ctrl": 0x11, "Key.alt": 0x12, "Key.delete": 0x2E,
        "Key.up": 0x26, "Key.down": 0x28, "Key.left": 0x25, "Key.right": 0x27,
        "Key.home": 0x24, "Key.end": 0x23, "Key.page_up": 0x21, "Key.page_down": 0x22,
        "Key.f1":0x70,"Key.f2":0x71,"Key.f3":0x72,"Key.f4":0x73,
        "Key.f5":0x74,"Key.f6":0x75,"Key.f7":0x76,"Key.f8":0x77,
        "Key.f9":0x78,"Key.f10":0x79,"Key.f11":0x7A,"Key.f12":0x7B,
    }
    if key_str in special:
        return special[key_str]
    if len(key_str) == 1:
        return win32api.VkKeyScan(key_str) & 0xFF if WIN32_OK else ord(key_str.upper())
    return None


# ─────────────────────────────────────────────
#  MACRO ENGINE
# ─────────────────────────────────────────────

class MacroEngine:
    def __init__(self):
        self.events = []
        self.recording = False
        self.playing = False
        self._start_time = None
        self._mouse_listener = None
        self._keyboard_listener = None
        self._mouse_ctrl = MouseController() if PYNPUT_OK else None
        self._keyboard_ctrl = KeyboardController() if PYNPUT_OK else None
        self.target_hwnd = None   # if set, replay goes to this window
        self.calib = {"offset_x": 0, "offset_y": 0, "scale_x": 1.0, "scale_y": 1.0}

    def start_recording(self):
        if not PYNPUT_OK:
            raise RuntimeError("pynput not available — pip install pynput")
        self.events = []
        self.recording = True
        self._start_time = time.time()
        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll)
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release)
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def stop_recording(self):
        self.recording = False
        if self._mouse_listener:  self._mouse_listener.stop()
        if self._keyboard_listener: self._keyboard_listener.stop()

    def _ts(self):
        return round(time.time() - self._start_time, 4)

    def _screen_size(self):
        """Physical screen size at this moment (used during recording)."""
        if WIN32_OK:
            w = ctypes.windll.user32.GetSystemMetrics(0)
            h = ctypes.windll.user32.GetSystemMetrics(1)
            return w, h
        return (1920, 1080)  # safe fallback

    def _on_move(self, x, y):
        sw, sh = self._screen_size()
        self.events.append({"type":"move","x":x,"y":y,
                             "rx": x/sw, "ry": y/sh, "t":self._ts()})
    def _on_click(self, x, y, button, pressed):
        sw, sh = self._screen_size()
        self.events.append({"type":"click","x":x,"y":y,
                             "rx": x/sw, "ry": y/sh,
                             "button":button.name,"pressed":pressed,"t":self._ts()})
    def _on_scroll(self, x, y, dx, dy):
        sw, sh = self._screen_size()
        self.events.append({"type":"scroll","x":x,"y":y,
                             "rx": x/sw, "ry": y/sh,
                             "dx":dx,"dy":dy,"t":self._ts()})
    def _on_key_press(self, key):
        self.events.append({"type":"key_press","key":self._key_name(key),"t":self._ts()})
    def _on_key_release(self, key):
        self.events.append({"type":"key_release","key":self._key_name(key),"t":self._ts()})
    def _key_name(self, key):
        try: return key.char
        except AttributeError: return str(key)

    def play(self, speed=1.0, repeat=1, on_done=None):
        if not self.events: return
        self.playing = True

        def _run():
            # Focus the target window ONCE before playback starts
            self._ensure_target_focused()
            for _ in range(repeat):
                if not self.playing: break
                prev_t = 0
                for ev in self.events:
                    if not self.playing: break
                    delay = (ev["t"] - prev_t) / speed
                    if delay > 0: time.sleep(delay)
                    prev_t = ev["t"]
                    self._replay(ev)
            self.playing = False
            if on_done: on_done()

        threading.Thread(target=_run, daemon=True).start()

    def stop_playback(self):
        self.playing = False

    def _calibrated(self, ev):
        """
        Convert event coordinates to current screen pixels.
        Prefers relative coords (rx, ry) recorded as fraction of screen —
        these are resolution-independent. Falls back to absolute (x, y)
        plus manual calibration offset for old macros.
        """
        if WIN32_OK:
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
        else:
            sw, sh = 1920, 1080

        if "rx" in ev and "ry" in ev:
            # Resolution-independent: scale relative coords to current screen
            cx = int(ev["rx"] * sw)
            cy = int(ev["ry"] * sh)
        else:
            # Legacy absolute coords — apply manual calibration offset
            c  = self.calib
            cx = int(ev["x"] * c.get("scale_x", 1.0)) + c.get("offset_x", 0)
            cy = int(ev["y"] * c.get("scale_y", 1.0)) + c.get("offset_y", 0)

        return cx, cy

    def _ensure_target_focused(self):
        """Bring target window to foreground and wait for OS to switch focus."""
        hwnd = self.target_hwnd
        if not hwnd or not WIN32_OK:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                return
            # Restore if minimized
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                time.sleep(0.1)
            # Use AllowSetForegroundWindow trick to bypass Windows focus lock
            current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
            ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)
            time.sleep(0.15)  # wait for OS to actually switch focus
        except Exception:
            pass

    def _replay(self, ev):
        hwnd = self.target_hwnd
        t = ev["type"]

        if WIN32_OK:
            if t == "move":
                cx, cy = self._calibrated(ev)
                send_mouse_move(cx, cy)
            elif t == "click":
                btn = ev.get("button", "left")
                cx, cy = self._calibrated(ev)
                if ev["pressed"]:
                    send_mouse_down(cx, cy, btn)
                else:
                    send_mouse_up(cx, cy, btn)
            elif t == "scroll":
                send_scroll_to_window(hwnd, ev["x"], ev["y"], ev.get("dy", 0))
            elif t == "key_press":
                if hwnd:
                    send_key_to_window(hwnd, ev["key"], press=True)
                elif PYNPUT_OK:
                    self._pk(self._keyboard_ctrl, ev["key"])
            elif t == "key_release":
                if hwnd:
                    send_key_to_window(hwnd, ev["key"], press=False)
                elif PYNPUT_OK:
                    self._rk(self._keyboard_ctrl, ev["key"])
        else:
            # ── Fallback: pynput (requires window focus) ──
            if not PYNPUT_OK: return
            m = self._mouse_ctrl
            k = self._keyboard_ctrl
            if t == "move":
                m.position = (ev["x"], ev["y"])
            elif t == "click":
                btn = Button.left if ev.get("button") == "left" else Button.right
                m.position = (ev["x"], ev["y"])
                if ev["pressed"]: m.press(btn)
                else: m.release(btn)
            elif t == "scroll":
                m.scroll(ev.get("dx",0), ev.get("dy",0))
            elif t == "key_press":
                self._pk(k, ev["key"])
            elif t == "key_release":
                self._rk(k, ev["key"])

    def _pk(self, ctrl, key_str):
        try:
            sp = self._special(key_str)
            ctrl.press(sp if sp else key_str)
        except Exception: pass

    def _rk(self, ctrl, key_str):
        try:
            sp = self._special(key_str)
            ctrl.release(sp if sp else key_str)
        except Exception: pass

    def _special(self, s):
        m = {"Key.space":Key.space,"Key.enter":Key.enter,"Key.backspace":Key.backspace,
             "Key.tab":Key.tab,"Key.shift":Key.shift,"Key.ctrl":Key.ctrl,"Key.alt":Key.alt,
             "Key.esc":Key.esc,"Key.up":Key.up,"Key.down":Key.down,"Key.left":Key.left,
             "Key.right":Key.right,"Key.delete":Key.delete,"Key.home":Key.home,
             "Key.end":Key.end,"Key.page_up":Key.page_up,"Key.page_down":Key.page_down}
        return m.get(s)

    def save(self, path, name="", description=""):
        data = {"name": name or os.path.basename(path), "description": description,
                "created": datetime.now().isoformat(), "event_count": len(self.events),
                "duration": self.events[-1]["t"] if self.events else 0, "events": self.events}
        with open(path, "w") as f: json.dump(data, f, indent=2)

    def load(self, path):
        with open(path) as f: data = json.load(f)
        self.events = data.get("events", [])
        return data


# ─────────────────────────────────────────────
#  SCHEDULER
# ─────────────────────────────────────────────

class MacroScheduler:
    def __init__(self):
        self._jobs = []; self._running = False

    def add_job(self, engine, interval_sec, repeat_times, label=""):
        job = {"label": label, "interval": interval_sec, "repeat": repeat_times,
               "engine": engine, "next_run": time.time() + interval_sec}
        self._jobs.append(job)
        if not self._running:
            self._running = True
            threading.Thread(target=self._loop, daemon=True).start()
        return job

    def remove_all(self): self._jobs.clear()
    def stop(self): self._running = False

    def _loop(self):
        while self._running:
            now = time.time()
            for job in list(self._jobs):
                if now >= job["next_run"] and not job["engine"].playing:
                    job["engine"].play(repeat=job["repeat"])
                    job["next_run"] = now + job["interval"]
            time.sleep(0.5)


# ─────────────────────────────────────────────
#  THEME
# ─────────────────────────────────────────────

DARK_BG  = "#1a1d27"; PANEL_BG = "#22263a"; ACCENT  = "#6c63ff"
ACCENT2  = "#ff6584"; TEXT     = "#e8e8f0"; MUTED   = "#7b7fa8"
SUCCESS  = "#43d98c"; DANGER   = "#ff4d6d"; BORDER  = "#2e3250"; WARN = "#f5a623"
FONT_MONO = ("Consolas", 10); FONT_UI = ("Segoe UI", 10); FONT_HEAD = ("Segoe UI", 13, "bold")

def _btn(parent, text, cmd, color=ACCENT, fg="white", width=14):
    return tk.Button(parent, text=text, command=cmd, bg=color, fg=fg,
                     activebackground=color, activeforeground=fg,
                     font=("Segoe UI", 10, "bold"), bd=0,
                     padx=10, pady=8, width=width, cursor="hand2", relief=tk.FLAT)


# ─────────────────────────────────────────────
#  EDIT EVENT DIALOG
# ─────────────────────────────────────────────

class EditEventDialog(tk.Toplevel):
    def __init__(self, parent, event, on_save):
        super().__init__(parent)
        self.event = dict(event); self.on_save = on_save
        self.title("Edit Event"); self.configure(bg=DARK_BG)
        self.resizable(False, False); self.grab_set()
        self._fields = {}; self._build(); self.geometry("400x380")

    def _build(self):
        tk.Label(self, text="Edit Event", bg=DARK_BG, fg=TEXT, font=FONT_HEAD, pady=12).pack(anchor="w", padx=20)
        tk.Label(self, text=f"Type:  {self.event.get('type','').upper()}",
                 bg=DARK_BG, fg=ACCENT, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=20)
        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=20, pady=8)

        form = tk.Frame(self, bg=DARK_BG); form.pack(fill=tk.X, padx=20)
        etype = self.event.get("type", "")
        editable = ["t"]
        if etype in ("move","click","scroll"): editable += ["x","y"]
        if etype == "click":  editable += ["button"]
        if etype == "scroll": editable += ["dx","dy"]
        if etype in ("key_press","key_release"): editable += ["key"]

        for i, key in enumerate(editable):
            tk.Label(form, text=key, bg=DARK_BG, fg=MUTED, font=FONT_UI,
                     width=10, anchor="w").grid(row=i, column=0, pady=4, sticky="w")
            var = tk.StringVar(value=str(self.event.get(key, "")))
            tk.Entry(form, textvariable=var, bg=PANEL_BG, fg=TEXT, font=FONT_MONO,
                     bd=0, insertbackground=TEXT, width=24).grid(row=i, column=1, pady=4, padx=8, sticky="w")
            self._fields[key] = (var, type(self.event.get(key, "")))

        self._pressed_var = None
        if etype == "click":
            self._pressed_var = tk.BooleanVar(value=self.event.get("pressed", True))
            tk.Label(form, text="pressed", bg=DARK_BG, fg=MUTED, font=FONT_UI,
                     width=10, anchor="w").grid(row=len(editable), column=0, pady=4, sticky="w")
            tk.Checkbutton(form, variable=self._pressed_var, bg=DARK_BG, fg=TEXT,
                           selectcolor=ACCENT, activebackground=DARK_BG).grid(row=len(editable), column=1, sticky="w")

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=20, pady=12)
        row = tk.Frame(self, bg=DARK_BG); row.pack(pady=4)
        _btn(row, "✓  Save", self._save, SUCCESS, width=10).grid(row=0, column=0, padx=6)
        _btn(row, "✕  Cancel", self.destroy, MUTED, width=10).grid(row=0, column=1, padx=6)

    def _save(self):
        updated = dict(self.event)
        for key, (var, orig_type) in self._fields.items():
            raw = var.get().strip()
            try:
                if orig_type == int: updated[key] = int(raw)
                elif orig_type == float: updated[key] = float(raw)
                else: updated[key] = raw
            except ValueError:
                messagebox.showerror("Invalid value", f"'{raw}' is not valid for '{key}'"); return
        if self._pressed_var is not None:
            updated["pressed"] = self._pressed_var.get()
        self.on_save(updated); self.destroy()


# ─────────────────────────────────────────────
#  MAIN APP
# ─────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("UO Macro Helper"); self.geometry("980x680")
        self.minsize(800, 540); self.configure(bg=DARK_BG)
        self.resizable(False, False)  # lock window size — can't be resized or maximized
        # Apply the ttk theme globally BEFORE any ttk widget (Combobox, Treeview,
        # Scrollbar, etc.) is created. Switching themes after widgets already exist
        # is what caused the Target Window combobox to trigger a window-geometry
        # glitch (content getting clipped/shrunk) when interacted with.
        _style = ttk.Style(self)
        _style.theme_use("clam")
        _style.configure("TCombobox", fieldbackground=PANEL_BG, background=PANEL_BG,
                          foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                          lightcolor=PANEL_BG, darkcolor=PANEL_BG)
        _style.map("TCombobox", fieldbackground=[("readonly", PANEL_BG)],
                   foreground=[("readonly", TEXT)])
        self.option_add("*TCombobox*Listbox.background", PANEL_BG)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.engine = MacroEngine()
        self.scheduler = MacroScheduler()
        self._status_var = tk.StringVar(value="Ready")
        self._record_start = None; self._timer_id = None
        self._always_on_top = False
        self._build_ui(); self._update_buttons()

        if not PYNPUT_OK:
            messagebox.showwarning("Missing dependency",
                "Install pynput:\n  pip install pynput\n\nRecording disabled.")
        if not WIN32_OK:
            messagebox.showwarning("Windows-only feature",
                "Install pywin32 for background window targeting:\n  pip install pywin32")

    # ── Layout ─────────────────────────────────

    def _build_ui(self):
        self._build_sidebar(); self._build_main(); self._build_statusbar()

    def _build_sidebar(self):
        sb = tk.Frame(self, bg=PANEL_BG, width=215)
        sb.pack(side=tk.LEFT, fill=tk.Y); sb.pack_propagate(False)

        tk.Label(sb, text="⬡  UO Macro Helper", bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 12, "bold"), pady=20).pack(fill=tk.X, padx=12)
        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12)

        self._nav_buttons = []
        for label, cmd in [("🔴  Recorder", self._show_recorder),
                            ("▶  Library",   self._show_library),
                            ("🕐  Scheduler",self._show_scheduler),
                            ("🖱  Calibration",self._show_calibration),
                            ("⚙  Settings", self._show_settings)]:
            b = tk.Button(sb, text=label, bg=PANEL_BG, fg=TEXT,
                          activebackground=ACCENT, activeforeground="white",
                          font=FONT_UI, bd=0, padx=16, pady=10, anchor="w",
                          cursor="hand2", command=cmd)
            b.pack(fill=tk.X, pady=1); self._nav_buttons.append(b)
        self._nav_buttons[0].configure(bg=ACCENT, fg="white")

        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=8)

        # Always on Top
        aot = tk.Frame(sb, bg=PANEL_BG); aot.pack(fill=tk.X, padx=12, pady=4)
        tk.Label(aot, text="Always on Top", bg=PANEL_BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._aot_btn = tk.Button(aot, text="OFF", bg=BORDER, fg=MUTED,
                                   font=("Segoe UI", 8, "bold"), bd=0, padx=8, pady=2,
                                   cursor="hand2", command=self._toggle_aot)
        self._aot_btn.pack(side=tk.RIGHT)

        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=4)

        # Timer
        self._timer_var = tk.StringVar(value="00:00.0")
        tk.Label(sb, textvariable=self._timer_var, bg=PANEL_BG, fg=ACCENT2,
                 font=("Consolas", 22, "bold")).pack(pady=4)
        tk.Label(sb, text="recording time", bg=PANEL_BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack()

        tk.Frame(sb, bg=PANEL_BG).pack(expand=True, fill=tk.Y)
        tk.Label(sb, text="v16.0.3", bg=PANEL_BG, fg=MUTED, font=("Segoe UI", 8)).pack(pady=8)

    def _toggle_aot(self):
        self._always_on_top = not self._always_on_top
        self.wm_attributes("-topmost", self._always_on_top)
        if self._always_on_top:
            self._aot_btn.configure(text="ON", bg=ACCENT, fg="white")
        else:
            self._aot_btn.configure(text="OFF", bg=BORDER, fg=MUTED)
        self.set_status("Always on Top: " + ("ON ✓" if self._always_on_top else "OFF"))

    def _build_main(self):
        self._main = tk.Frame(self, bg=DARK_BG)
        self._main.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self._frames = {}
        for name, cls in [("recorder",RecorderPanel),("library",LibraryPanel),
                           ("scheduler",SchedulerPanel),("calibration",CalibrationPanel),
                           ("settings",SettingsPanel)]:
            f = cls(self._main, self)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._frames[name] = f
        self._show_panel("recorder")

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=PANEL_BG, height=28)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Label(bar, textvariable=self._status_var, bg=PANEL_BG,
                 fg=MUTED, font=("Segoe UI", 9), padx=12).pack(side=tk.LEFT)

    def _show_panel(self, n): self._frames[n].tkraise()
    def _highlight_nav(self, idx):
        for i, b in enumerate(self._nav_buttons):
            b.configure(bg=ACCENT if i==idx else PANEL_BG, fg="white" if i==idx else TEXT)
    def _show_recorder(self):  self._show_panel("recorder");  self._highlight_nav(0)
    def _show_library(self):   self._frames["library"].refresh(); self._show_panel("library");  self._highlight_nav(1)
    def _show_scheduler(self): self._show_panel("scheduler"); self._highlight_nav(2)
    def _show_calibration(self): self._show_panel("calibration"); self._highlight_nav(3)
    def _show_settings(self):    self._show_panel("settings");     self._highlight_nav(4)

    def start_recording(self):
        try:
            self.engine.start_recording()
            self._record_start = time.time()
            self._tick_timer()
            self.set_status("🔴 Recording…")
            self._update_buttons()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def stop_recording(self):
        self.engine.stop_recording()
        if self._timer_id: self.after_cancel(self._timer_id)
        self.set_status(f"Recorded {len(self.engine.events)} events  ·  {self._timer_var.get()}")
        self._update_buttons()

    def start_playback(self):
        rp = self._frames["recorder"]
        speed  = float(rp.speed_var.get())
        repeat = int(rp.repeat_var.get())
        self.engine.play(speed=speed, repeat=repeat, on_done=self._on_play_done)
        target = self.engine.target_hwnd
        label  = win32gui.GetWindowText(target) if (target and WIN32_OK) else "system (focus-based)"
        self.set_status(f"▶ Playing into: {label}  ×{repeat}  at {speed}×")
        self._update_buttons()

    def stop_playback(self):
        self.engine.stop_playback()
        self.set_status("Stopped."); self._update_buttons()

    def _on_play_done(self):
        self.after(0, self._update_buttons)
        self.after(0, lambda: self.set_status("Playback complete."))

    def _tick_timer(self):
        if not self.engine.recording: return
        elapsed = time.time() - self._record_start
        self._timer_var.set(f"{int(elapsed//60):02d}:{elapsed%60:04.1f}")
        self._timer_id = self.after(100, self._tick_timer)

    def _update_buttons(self):
        self.after(0, self._frames["recorder"].sync_buttons)

    def set_status(self, msg): self._status_var.set(msg)
    def save_macro(self, path, name, desc): self.engine.save(path, name, desc)
    def load_macro(self, path):
        meta = self.engine.load(path)
        self.set_status(f"Loaded: {meta.get('name','')}  ({len(self.engine.events)} events)")
        self._update_buttons(); return meta


# ─────────────────────────────────────────────
#  RECORDER PANEL
# ─────────────────────────────────────────────

class RecorderPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG)
        self.app = app; self._build()

    def _build(self):
        tk.Label(self, text="Macro Recorder", bg=DARK_BG, fg=TEXT,
                 font=FONT_HEAD, pady=14).pack(anchor="w", padx=24)

        # ── Target Window selector ──────────────
        tgt_frame = tk.Frame(self, bg=PANEL_BG)
        tgt_frame.pack(fill=tk.X, padx=24, pady=(0, 10))
        inner = tk.Frame(tgt_frame, bg=PANEL_BG); inner.pack(fill=tk.X, padx=12, pady=8)

        tk.Label(inner, text="Target Window:", bg=PANEL_BG, fg=MUTED,
                 font=FONT_UI).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.target_var = tk.StringVar(value="— System (focus-based) —")
        self.target_cb  = ttk.Combobox(inner, textvariable=self.target_var,
                                        font=FONT_UI, width=42, state="readonly")
        self.target_cb.grid(row=0, column=1, padx=(0, 8))
        self.target_cb.bind("<<ComboboxSelected>>", self._on_target_select)

        _btn(inner, "🔄 Refresh", self._refresh_windows, PANEL_BG, width=10).grid(row=0, column=2)
        self._refresh_windows()

        # ── Transport ──────────────────────────
        btns = tk.Frame(self, bg=DARK_BG); btns.pack(padx=24, anchor="w", pady=(4,0))
        self.btn_record    = _btn(btns, "⏺  Record",    self.app.start_recording, DANGER)
        self.btn_stop      = _btn(btns, "⏹  Stop",      self._stop,               MUTED)
        self.btn_play      = _btn(btns, "▶  Play",      self.app.start_playback,  SUCCESS)
        self.btn_stop_play = _btn(btns, "⏹  Stop Play", self.app.stop_playback,   MUTED)
        for i, b in enumerate([self.btn_record, self.btn_stop, self.btn_play, self.btn_stop_play]):
            b.grid(row=0, column=i, padx=4)

        # ── Options ────────────────────────────
        opts = tk.Frame(self, bg=DARK_BG); opts.pack(padx=24, pady=8, anchor="w")
        tk.Label(opts, text="Speed:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=0, padx=(0,4))
        self.speed_var = tk.StringVar(value="1.0")
        ttk.Combobox(opts, textvariable=self.speed_var, width=6,
                     values=["0.25","0.5","0.75","1.0","1.5","2.0","4.0"]).grid(row=0, column=1, padx=(0,16))
        tk.Label(opts, text="Repeat:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=2, padx=(0,4))
        self.repeat_var = tk.StringVar(value="1")
        tk.Spinbox(opts, textvariable=self.repeat_var, from_=1, to=9999,
                   width=6, bg=PANEL_BG, fg=TEXT, bd=0, font=FONT_UI).grid(row=0, column=3, padx=(0,16))

        # ── Save / Load ────────────────────────
        io = tk.Frame(self, bg=DARK_BG); io.pack(padx=24, anchor="w", pady=(0,8))
        _btn(io, "💾  Save Macro", self._save, ACCENT,   width=14).grid(row=0, column=0, padx=4)
        _btn(io, "📂  Load Macro", self._load, PANEL_BG, width=14).grid(row=0, column=1, padx=4)

        # ── Event log action buttons (moved to left, under Save/Load) ──
        eb = tk.Frame(self, bg=DARK_BG); eb.pack(padx=24, anchor="w", pady=(0,4))
        _btn(eb, "✏ Edit",      self._edit_sel,   ACCENT,   width=8).grid(row=0, column=0, padx=2)
        _btn(eb, "＋ Add",      self._add_event,  PANEL_BG, width=8).grid(row=0, column=1, padx=2)
        _btn(eb, "🗑 Delete",   self._del_sel,    DANGER,   width=8).grid(row=0, column=2, padx=2)
        _btn(eb, "🗑 Clear All",self._clear_all,  DANGER,   width=10).grid(row=0, column=3, padx=2)
        _btn(eb, "⬆",          self._move_up,    PANEL_BG, width=3).grid(row=0, column=4, padx=2)
        _btn(eb, "⬇",          self._move_down,  PANEL_BG, width=3).grid(row=0, column=5, padx=2)

        # ── Event table header ─────────────────
        th = tk.Frame(self, bg=DARK_BG); th.pack(fill=tk.X, padx=24, pady=(4,2))
        tk.Label(th, text="Event Log  (double-click to edit)",
                 bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT)

        # ── Event table ────────────────────────
        cols = ("#","Time(s)","Type","Details")
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                  selectmode="extended", height=10)
        for col, w in zip(cols, [50,80,110,500]):
            self.tree.heading(col, text=col); self.tree.column(col, width=w, anchor="w")

        style = ttk.Style()
        style.configure("Treeview", background=PANEL_BG, fieldbackground=PANEL_BG,
                        foreground=TEXT, rowheight=24, font=FONT_MONO)
        style.configure("Treeview.Heading", background=BORDER, foreground=MUTED,
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)])
        for tag, color in [("move",MUTED),("click",SUCCESS),("scroll",WARN),
                           ("key_press",ACCENT),("key_release","#a89bff")]:
            self.tree.tag_configure(tag, foreground=color)

        vsb = tk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(expand=True, fill=tk.BOTH, padx=(24,0), pady=(0,12))
        vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=(0,12), padx=(0,8))
        self.tree.bind("<Double-1>",    lambda e: self._edit_sel())
        self.tree.bind("<Shift-Up>",    self._on_shift_up)
        self.tree.bind("<Shift-Down>",  self._on_shift_down)
        self.tree.bind("<Delete>",      lambda e: self._del_sel())
        # Ctrl+A = select all
        self.tree.bind("<Control-a>",   lambda e: self.tree.selection_set(self.tree.get_children()))
        self._sched_refresh()

    # ── Window list ────────────────────────────

    def _refresh_windows(self):
        self._window_map = {"— System (focus-based) —": None}
        for hwnd, title in list_windows():
            label = title[:60]
            self._window_map[label] = hwnd
        self.target_cb["values"] = list(self._window_map.keys())
        # Keep current selection if still valid
        if self.target_var.get() not in self._window_map:
            self.target_var.set("— System (focus-based) —")
            self.app.engine.target_hwnd = None

    def _on_target_select(self, _=None):
        label = self.target_var.get()
        hwnd  = self._window_map.get(label)
        self.app.engine.target_hwnd = hwnd
        if hwnd:
            self.app.set_status(f"Target: {label}  (hwnd={hwnd})")
        else:
            self.app.set_status("Target: system (focus-based playback)")

    # ── Table ──────────────────────────────────

    def _detail(self, ev):
        t = ev.get("type","")
        if t == "move":   return f"x={ev['x']}  y={ev['y']}"
        if t == "click":  return f"x={ev['x']}  y={ev['y']}  btn={ev.get('button','?')}  {'DOWN' if ev.get('pressed') else 'UP'}"
        if t == "scroll": return f"x={ev['x']}  y={ev['y']}  dy={ev.get('dy',0)}"
        if t in ("key_press","key_release"): return f"key={ev.get('key','?')}"
        return str(ev)

    def refresh_table(self):
        sel = {self.tree.index(s) for s in self.tree.selection()}
        self.tree.delete(*self.tree.get_children())
        for i, ev in enumerate(self.app.engine.events):
            iid = self.tree.insert("", tk.END,
                                   values=(i+1, f"{ev.get('t',0):.3f}", ev.get("type",""), self._detail(ev)),
                                   tags=(ev.get("type",""),))
            if i in sel: self.tree.selection_add(iid)

    def _sched_refresh(self):
        if self.app.engine.recording:
            self.refresh_table()
            ch = self.tree.get_children()
            if ch: self.tree.see(ch[-1])
        self.after(400, self._sched_refresh)

    def _sel_indices(self):
        return sorted([self.tree.index(i) for i in self.tree.selection()])

    def _on_shift_up(self, event):
        self._shift_select(-1)
        return "break"

    def _on_shift_down(self, event):
        self._shift_select(1)
        return "break"

    def _shift_select(self, direction):
        """Extend selection up or down with Shift+Arrow — anchor-based like standard list."""
        children = self.tree.get_children()
        if not children: return
        sel = self._sel_indices()
        if not sel:
            start = 0 if direction > 0 else len(children) - 1
            self.tree.selection_set(children[start])
            self.tree.focus(children[start])
            self.tree.see(children[start])
            return
        # Use the focused item as the moving end; anchor stays fixed
        focused = self.tree.focus()
        if not focused or focused not in children:
            focused = children[max(sel) if direction > 0 else min(sel)]
        focus_idx = list(children).index(focused)
        new_idx = focus_idx + direction
        if 0 <= new_idx < len(children):
            new_item = children[new_idx]
            # Anchor = opposite end of current selection
            anchor_idx = min(sel) if direction > 0 else max(sel)
            # Select everything from anchor to new position
            lo = min(anchor_idx, new_idx)
            hi = max(anchor_idx, new_idx)
            self.tree.selection_set(children[lo:hi+1])
            self.tree.focus(new_item)
            self.tree.see(new_item)
        return "break"  # prevent default tkinter arrow behavior

    def _edit_sel(self):
        idx = self._sel_indices()
        if not idx: messagebox.showinfo("Select an event","Click a row first."); return
        i = idx[0]; ev = self.app.engine.events[i]
        def save(u): self.app.engine.events[i] = u; self.refresh_table(); self.app.set_status(f"Event #{i+1} updated.")
        EditEventDialog(self, ev, save)

    def _del_sel(self):
        indices = sorted(self._sel_indices(), reverse=True)
        if not indices:
            messagebox.showinfo("Select events",
                "Click one or more rows first.\n\n"
                "  • Ctrl + Click  →  select individual rows\n"
                "  • Shift + Click →  select a range")
            return
        if not messagebox.askyesno("Delete?", f"Delete {len(indices)} event(s)?"): return
        for i in indices:
            del self.app.engine.events[i]
        self.refresh_table()
        self.app.set_status(f"Deleted {len(indices)} event(s).")
        self.app._update_buttons()

    def _clear_all(self):
        if not self.app.engine.events: return
        if not messagebox.askyesno("Clear All?", f"Delete all {len(self.app.engine.events)} events?\n\nThis cannot be undone."): return
        self.app.engine.events.clear()
        self.refresh_table()
        self.app.set_status("All events cleared.")
        self.app._update_buttons()

    def _add_event(self):
        t = round(self.app.engine.events[-1]["t"] + 0.1, 4) if self.app.engine.events else 0.0
        ev = {"type":"click","x":0,"y":0,"button":"left","pressed":True,"t":t}
        self.app.engine.events.append(ev)
        def save(u): self.app.engine.events[-1] = u; self.refresh_table()
        EditEventDialog(self, ev, save)

    def _move_up(self):
        idx = sorted(self._sel_indices())
        if not idx or min(idx) == 0: return
        evs = self.app.engine.events
        # Move each selected row up one position
        for i in idx:
            evs[i-1], evs[i] = evs[i], evs[i-1]
        # New indices are all shifted up by 1
        new_idx = [i-1 for i in idx]
        self.refresh_table()
        self._restore_selection(new_idx)

    def _move_down(self):
        idx = sorted(self._sel_indices(), reverse=True)
        evs = self.app.engine.events
        if not idx or max(idx) >= len(evs)-1: return
        # Move each selected row down one position (process bottom-up)
        for i in idx:
            evs[i], evs[i+1] = evs[i+1], evs[i]
        # New indices are all shifted down by 1
        new_idx = [i+1 for i in idx]
        self.refresh_table()
        self._restore_selection(new_idx)

    def _restore_selection(self, indices):
        """Re-select rows by index after a table refresh."""
        children = self.tree.get_children()
        self.tree.selection_remove(*self.tree.selection())
        for i in indices:
            if 0 <= i < len(children):
                self.tree.selection_add(children[i])
                self.tree.see(children[i])

    def _stop(self):
        if self.app.engine.recording: self.app.stop_recording(); self.refresh_table()
        elif self.app.engine.playing: self.app.stop_playback()

    def _save(self):
        if not self.app.engine.events:
            messagebox.showwarning("Nothing to save","Record a macro first."); return
        win = tk.Toplevel(self); win.title("Save Macro")
        win.geometry("360x220"); win.configure(bg=DARK_BG); win.resizable(False,False)
        tk.Label(win,text="Macro Name:",bg=DARK_BG,fg=TEXT,font=FONT_UI).pack(anchor="w",padx=20,pady=(16,2))
        ne = tk.Entry(win,bg=PANEL_BG,fg=TEXT,font=FONT_UI,bd=0,insertbackground=TEXT)
        ne.pack(fill=tk.X,padx=20); ne.insert(0,f"Macro_{datetime.now().strftime('%H%M%S')}")
        tk.Label(win,text="Description:",bg=DARK_BG,fg=TEXT,font=FONT_UI).pack(anchor="w",padx=20,pady=(10,2))
        de = tk.Entry(win,bg=PANEL_BG,fg=TEXT,font=FONT_UI,bd=0,insertbackground=TEXT)
        de.pack(fill=tk.X,padx=20)
        def do():
            path = filedialog.asksaveasfilename(defaultextension=".macro",
                    filetypes=[("Macro","*.macro"),("JSON","*.json")], initialfile=ne.get())
            if path: self.app.save_macro(path,ne.get(),de.get()); self.app.set_status(f"Saved: {os.path.basename(path)}"); win.destroy()
        _btn(win,"Save",do,ACCENT,width=10).pack(pady=12)

    def _load(self):
        path = filedialog.askopenfilename(filetypes=[("Macro","*.macro"),("JSON","*.json"),("All","*.*")])
        if path:
            try: self.app.load_macro(path); self.refresh_table()
            except Exception as e: messagebox.showerror("Load error",str(e))

    def sync_buttons(self):
        eng = self.app.engine
        rec,playing,has = eng.recording,eng.playing,bool(eng.events)
        self.btn_record.configure(state=tk.DISABLED if rec or playing else tk.NORMAL,
                                   bg=DANGER if not(rec or playing) else MUTED)
        self.btn_stop.configure(state=tk.NORMAL if rec or playing else tk.DISABLED)
        self.btn_play.configure(state=tk.NORMAL if has and not rec and not playing else tk.DISABLED)
        self.btn_stop_play.configure(state=tk.NORMAL if playing else tk.DISABLED)


# ─────────────────────────────────────────────
#  LIBRARY / SCHEDULER / SETTINGS (unchanged)
# ─────────────────────────────────────────────

class LibraryPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG); self.app=app; self._build()
    def _build(self):
        h=tk.Frame(self,bg=DARK_BG); h.pack(fill=tk.X,padx=24,pady=(20,8))
        tk.Label(h,text="Macro Library",bg=DARK_BG,fg=TEXT,font=FONT_HEAD).pack(side=tk.LEFT)
        _btn(h,"📂 Open Folder",self._open_folder,PANEL_BG,width=12).pack(side=tk.RIGHT)
        cols=("Name","Events","Duration","Created","Path")
        self.tree=ttk.Treeview(self,columns=cols,show="headings",selectmode="browse")
        for col,w in zip(cols,[200,80,90,160,300]):
            self.tree.heading(col,text=col); self.tree.column(col,width=w,anchor="w")
        vsb=tk.Scrollbar(self,orient="vertical",command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(expand=True,fill=tk.BOTH,padx=24); vsb.pack(side=tk.RIGHT,fill=tk.Y)
        br=tk.Frame(self,bg=DARK_BG); br.pack(padx=24,pady=10,anchor="w")
        _btn(br,"▶  Load & Play",self._load_play,SUCCESS,width=13).grid(row=0,column=0,padx=4)
        _btn(br,"📥  Load",self._load_sel,ACCENT,width=10).grid(row=0,column=1,padx=4)
        _btn(br,"🗑  Delete",self._delete,DANGER,width=10).grid(row=0,column=2,padx=4)
    def refresh(self,folder=None):
        folder=folder or os.path.expanduser("~")
        self.tree.delete(*self.tree.get_children())
        for f in os.listdir(folder):
            if f.endswith(".macro") or (f.endswith(".json") and "macro" in f.lower()):
                path=os.path.join(folder,f)
                try:
                    with open(path) as fp: data=json.load(fp)
                    self.tree.insert("",tk.END,values=(data.get("name",f),data.get("event_count","?"),
                        f"{round(data.get('duration',0),1)}s",data.get("created","")[:16],path))
                except: pass
    def _open_folder(self):
        folder=filedialog.askdirectory()
        if folder: self.refresh(folder)
    def _sel_path(self):
        sel=self.tree.selection()
        if not sel: messagebox.showinfo("Select a macro","Click a macro first."); return None
        return self.tree.item(sel[0])["values"][4]
    def _load_sel(self):
        p=self._sel_path()
        if p: self.app.load_macro(p); self.app._show_recorder(); self.app._frames["recorder"].refresh_table()
    def _load_play(self):
        p=self._sel_path()
        if p: self.app.load_macro(p); self.app._show_recorder(); self.app._frames["recorder"].refresh_table(); self.after(300,self.app.start_playback)
    def _delete(self):
        p=self._sel_path()
        if p and messagebox.askyesno("Delete?",f"Delete {os.path.basename(p)}?"): os.remove(p); self.refresh()

class SchedulerPanel(tk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,bg=DARK_BG); self.app=app; self._build()
    def _build(self):
        tk.Label(self,text="Scheduler",bg=DARK_BG,fg=TEXT,font=FONT_HEAD,pady=20).pack(anchor="w",padx=24)
        form=tk.Frame(self,bg=PANEL_BG); form.pack(fill=tk.X,padx=24,pady=(0,12))
        inner=tk.Frame(form,bg=PANEL_BG); inner.pack(padx=16,pady=12)
        for col,label in enumerate(["Run every (seconds)","Repeat times","Label"]):
            tk.Label(inner,text=label,bg=PANEL_BG,fg=MUTED,font=FONT_UI).grid(row=0,column=col,padx=8,sticky="w")
        self.interval_var=tk.StringVar(value="60"); self.repeat_var=tk.StringVar(value="1"); self.label_var=tk.StringVar(value="Scheduled Macro")
        for col,(var,w) in enumerate([(self.interval_var,10),(self.repeat_var,8),(self.label_var,20)]):
            tk.Entry(inner,textvariable=var,width=w,bg=DARK_BG,fg=TEXT,font=FONT_UI,bd=0,insertbackground=TEXT).grid(row=1,column=col,padx=8,pady=4,sticky="w")
        _btn(inner,"＋ Add Job",self._add_job,ACCENT,width=12).grid(row=1,column=3,padx=12)
        tk.Label(self,text="Active Jobs",bg=DARK_BG,fg=MUTED,font=("Segoe UI",9),pady=4).pack(anchor="w",padx=24)
        self.jobs_box=tk.Listbox(self,bg=PANEL_BG,fg=TEXT,font=FONT_UI,bd=0,selectbackground=ACCENT,height=12)
        self.jobs_box.pack(fill=tk.BOTH,expand=True,padx=24,pady=(0,8))
        _btn(self,"🗑  Clear All Jobs",self._clear,DANGER,width=16).pack(anchor="w",padx=24,pady=4)
    def _add_job(self):
        if not self.app.engine.events: messagebox.showwarning("No macro","Record or load a macro first."); return
        try: interval=float(self.interval_var.get()); repeat=int(self.repeat_var.get())
        except ValueError: messagebox.showerror("Invalid input","Enter valid numbers."); return
        label=self.label_var.get()
        self.app.scheduler.add_job(self.app.engine,interval,repeat,label)
        self.jobs_box.insert(tk.END,f"  ⏰  {label}  —  every {interval}s  ×{repeat}")
    def _clear(self): self.app.scheduler.remove_all(); self.jobs_box.delete(0,tk.END)

class SettingsPanel(tk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,bg=DARK_BG); self.app=app; self._build()
    def _build(self):
        tk.Label(self,text="Settings",bg=DARK_BG,fg=TEXT,font=FONT_HEAD,pady=20).pack(anchor="w",padx=24)
        for text,default in [("Record mouse movements",True),("Record keyboard events",True),
                              ("Show event log while recording",True),("Confirm before playback",False)]:
            var=tk.BooleanVar(value=default)
            tk.Checkbutton(self,text=text,variable=var,bg=DARK_BG,fg=TEXT,selectcolor=ACCENT,
                           activebackground=DARK_BG,activeforeground=TEXT,font=FONT_UI).pack(anchor="w",padx=28,pady=4)
        tk.Label(self,text="\nHotkeys",bg=DARK_BG,fg=MUTED,font=("Segoe UI",9)).pack(anchor="w",padx=24)
        for key,action in [("F9","Start/Stop Recording"),("F10","Start Playback"),("Esc","Stop Playback")]:
            row=tk.Frame(self,bg=PANEL_BG); row.pack(fill=tk.X,padx=24,pady=2)
            tk.Label(row,text=f"  {key}",bg=PANEL_BG,fg=ACCENT,font=FONT_MONO,width=8).pack(side=tk.LEFT)
            tk.Label(row,text=action,bg=PANEL_BG,fg=TEXT,font=FONT_UI).pack(side=tk.LEFT,padx=8)
        tk.Label(self,text="\nDependencies",bg=DARK_BG,fg=MUTED,font=("Segoe UI",9)).pack(anchor="w",padx=24)
        for lib,ok in [("pynput",PYNPUT_OK),("pywin32",WIN32_OK),("schedule",SCHEDULE_OK)]:
            status="✓  installed" if ok else "✗  missing  —  pip install "+lib
            tk.Label(self,text=f"  {lib}:  {status}",bg=DARK_BG,fg=SUCCESS if ok else DANGER,font=FONT_MONO).pack(anchor="w",padx=28)



# ─────────────────────────────────────────────
#  CALIBRATION PANEL
# ─────────────────────────────────────────────

class CalibrationPanel(tk.Frame):
    """\n    Calibration flow:\n    1. Click "Capture Position" button — app starts listening for your next click.\n    2. Click anywhere on screen (on your target app) — position is saved automatically.\n    3. Run the macro once, note where it actually landed.\n    4. Hover over the landed spot — live tracker shows coordinates.\n    5. Enter those coordinates and click Calculate.\n    """

    CALIB_FILE = os.path.join(os.path.expanduser("~"), ".macrorecorder_calib.json")

    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG)
        self.app = app
        self._calib = {"offset_x": 0, "offset_y": 0}
        self._captured = None      # (x,y) of the target location
        self._waiting  = False     # True while waiting for user to click target
        self._click_listener = None
        self._load_calib()
        self._build()
        self._track_loop()

    # ── Persistence ───────────────────────────

    def _load_calib(self):
        try:
            with open(self.CALIB_FILE) as f:
                self._calib = json.load(f)
        except Exception:
            pass
        self._push_calib()

    def _save_calib(self):
        with open(self.CALIB_FILE, "w") as f:
            json.dump(self._calib, f, indent=2)

    def _push_calib(self):
        self.app.engine.calib = {
            "offset_x": self._calib.get("offset_x", 0),
            "offset_y": self._calib.get("offset_y", 0),
            "scale_x":  1.0,
            "scale_y":  1.0,
        }

    # ── UI ────────────────────────────────────

    def _build(self):
        tk.Label(self, text="Mouse Calibration", bg=DARK_BG, fg=TEXT,
                 font=FONT_HEAD, pady=16).pack(anchor="w", padx=24)

        # Active offset display
        box = tk.Frame(self, bg=PANEL_BG)
        box.pack(fill=tk.X, padx=24, pady=(0, 12))
        inn = tk.Frame(box, bg=PANEL_BG); inn.pack(padx=16, pady=12)
        tk.Label(inn, text="Active Offset", bg=PANEL_BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0,
                 columnspan=4, sticky="w", pady=(0, 6))
        tk.Label(inn, text="X:", bg=PANEL_BG, fg=MUTED, font=FONT_UI).grid(row=1, column=0, padx=(0,4))
        self._disp_x = tk.Label(inn, text="0 px", bg=PANEL_BG, fg=SUCCESS,
                                  font=("Consolas", 14, "bold"))
        self._disp_x.grid(row=1, column=1, padx=(0, 24))
        tk.Label(inn, text="Y:", bg=PANEL_BG, fg=MUTED, font=FONT_UI).grid(row=1, column=2, padx=(0,4))
        self._disp_y = tk.Label(inn, text="0 px", bg=PANEL_BG, fg=SUCCESS,
                                  font=("Consolas", 14, "bold"))
        self._disp_y.grid(row=1, column=3)
        self._refresh_display()

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=24, pady=8)

        # Live cursor
        tk.Label(self, text="Live Cursor Position", bg=DARK_BG, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=24)
        tk.Label(self, text="Hover anywhere to read coordinates:",
                 bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=24, pady=(2,4))
        self._live_lbl = tk.Label(self, text="x=0   y=0", bg=PANEL_BG, fg=ACCENT,
                                   font=("Consolas", 18, "bold"), padx=14, pady=6)
        self._live_lbl.pack(anchor="w", padx=24, pady=(0,12))

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=24, pady=4)

        # Step 1
        tk.Label(self, text="Step 1 - Capture your target location",
                 bg=DARK_BG, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=24, pady=(8,2))
        tk.Label(self,
                 text="Click the button below. The app will wait for your NEXT click on screen.\n"
                      "Click exactly on the spot you want the macro to hit in the target program.",
                 bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=24, pady=(0,8))

        self._cap_btn = _btn(self, "📍  Click Here to Start Capture", self._start_capture,
                             ACCENT, width=30)
        self._cap_btn.pack(anchor="w", padx=24)

        self._cap_status = tk.Label(self, text="Waiting...",
                                     bg=DARK_BG, fg=MUTED, font=("Segoe UI", 10, "bold"))
        self._cap_status.pack(anchor="w", padx=24, pady=(6, 0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=24, pady=12)

        # Step 2 + 3
        tk.Label(self, text="Step 2 - Play macro, check where click lands",
                 bg=DARK_BG, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=24, pady=(0,4))
        tk.Label(self,
                 text="Run your macro once. Hover your mouse over the spot where the click landed.\n"
                      "Read the coordinates from the Live Cursor above and enter them below.",
                 bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=24, pady=(0,10))

        row = tk.Frame(self, bg=DARK_BG); row.pack(anchor="w", padx=24)
        tk.Label(row, text="Landed X:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=0, padx=(0,4))
        self._land_x = tk.Entry(row, width=7, bg=PANEL_BG, fg=TEXT,
                                 font=FONT_MONO, bd=0, insertbackground=TEXT)
        self._land_x.grid(row=0, column=1, padx=(0,16))
        tk.Label(row, text="Landed Y:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=2, padx=(0,4))
        self._land_y = tk.Entry(row, width=7, bg=PANEL_BG, fg=TEXT,
                                 font=FONT_MONO, bd=0, insertbackground=TEXT)
        self._land_y.grid(row=0, column=3, padx=(0,16))
        _btn(row, "Calculate & Save", self._calculate, SUCCESS, width=16).grid(row=0, column=4, padx=4)

        self._result_lbl = tk.Label(self, text="", bg=DARK_BG, fg=ACCENT2,
                                     font=("Segoe UI", 10, "bold"))
        self._result_lbl.pack(anchor="w", padx=24, pady=(8, 0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=24, pady=12)

        # Manual override
        tk.Label(self, text="Manual Override", bg=DARK_BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24)
        man = tk.Frame(self, bg=DARK_BG); man.pack(anchor="w", padx=24, pady=6)
        tk.Label(man, text="Offset X:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=0, padx=(0,4))
        self._man_x = tk.Entry(man, width=6, bg=PANEL_BG, fg=TEXT, font=FONT_MONO, bd=0, insertbackground=TEXT)
        self._man_x.grid(row=0, column=1, padx=(0,12))
        self._man_x.insert(0, str(self._calib.get("offset_x", 0)))
        tk.Label(man, text="Offset Y:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=2, padx=(0,4))
        self._man_y = tk.Entry(man, width=6, bg=PANEL_BG, fg=TEXT, font=FONT_MONO, bd=0, insertbackground=TEXT)
        self._man_y.grid(row=0, column=3, padx=(0,12))
        self._man_y.insert(0, str(self._calib.get("offset_y", 0)))
        _btn(man, "Apply", self._apply_manual, ACCENT, width=8).grid(row=0, column=4, padx=4)
        _btn(man, "Reset Zero", self._reset, MUTED, width=10).grid(row=0, column=5, padx=8)

    # ── Live tracker ──────────────────────────

    def _track_loop(self):
        try:
            if WIN32_OK:
                x, y = win32api.GetCursorPos()
            elif PYNPUT_OK:
                pos = MouseController().position
                x, y = int(pos[0]), int(pos[1])
            else:
                x = y = 0
            self._live_lbl.configure(text=f"x={x}   y={y}")
        except Exception:
            pass
        self.after(50, self._track_loop)

    # ── Step 1: Capture ───────────────────────

    def _start_capture(self):
        if not PYNPUT_OK:
            messagebox.showerror("Missing", "pynput is required for capture.")
            return
        if self._waiting:
            # Cancel if already waiting
            self._stop_listener()
            self._waiting = False
            self._cap_btn.configure(text="📍  Click Here to Start Capture", bg=ACCENT)
            self._cap_status.configure(text="Cancelled.", fg=MUTED)
            return

        self._waiting = True
        self._cap_btn.configure(text="⏳  Waiting for your click on screen...", bg=WARN)
        self._cap_status.configure(
            text="Now click the exact spot in your target program you want the macro to hit.",
            fg=WARN)

        def on_click(x, y, button, pressed):
            if pressed and button == mouse.Button.left:
                # Schedule on main thread
                self.after(0, lambda: self._on_captured(x, y))
                return False  # stop listener

        self._click_listener = mouse.Listener(on_click=on_click)
        self._click_listener.daemon = True
        self._click_listener.start()

    def _on_captured(self, x, y):
        self._waiting = False
        self._captured = (x, y)
        self._stop_listener()
        self._cap_btn.configure(text="📍  Click Here to Start Capture", bg=ACCENT)
        self._cap_status.configure(
            text=f"Captured: x={x}   y={y}   - now run the macro and check where it clicks",
            fg=SUCCESS)
        self.app.set_status(f"Calibration target captured: ({x}, {y})")

    def _stop_listener(self):
        if self._click_listener:
            try: self._click_listener.stop()
            except Exception: pass
            self._click_listener = None

    # ── Calculate ─────────────────────────────

    def _calculate(self):
        if self._captured is None:
            messagebox.showwarning("Step 1 first",
                "Click the Capture button first, then click your target spot.")
            return
        try:
            lx = int(self._land_x.get().strip())
            ly = int(self._land_y.get().strip())
        except ValueError:
            messagebox.showerror("Invalid input", "Enter whole numbers for Landed X and Y.")
            return

        ex, ey = self._captured
        off_x = ex - lx
        off_y = ey - ly

        self._calib = {"offset_x": off_x, "offset_y": off_y, "scale_x": 1.0, "scale_y": 1.0}
        self._save_calib()
        self._push_calib()
        self._refresh_display()
        self._man_x.delete(0, tk.END); self._man_x.insert(0, str(off_x))
        self._man_y.delete(0, tk.END); self._man_y.insert(0, str(off_y))
        self._result_lbl.configure(
            text=f"Saved!  offset ({off_x:+d}, {off_y:+d})  applied to all future playback.",
            fg=SUCCESS)
        self.app.set_status(f"Calibration saved: ({off_x:+d}, {off_y:+d})")

    # ── Manual / Reset ────────────────────────

    def _apply_manual(self):
        try:
            ox = int(self._man_x.get().strip())
            oy = int(self._man_y.get().strip())
        except ValueError:
            messagebox.showerror("Invalid", "Enter whole numbers."); return
        self._calib = {"offset_x": ox, "offset_y": oy, "scale_x": 1.0, "scale_y": 1.0}
        self._save_calib(); self._push_calib(); self._refresh_display()
        self._result_lbl.configure(text=f"Manual offset applied: ({ox:+d}, {oy:+d})", fg=SUCCESS)
        self.app.set_status(f"Manual calibration: ({ox:+d}, {oy:+d})")

    def _reset(self):
        if not messagebox.askyesno("Reset?", "Reset calibration offset to zero?"): return
        self._calib = {"offset_x": 0, "offset_y": 0, "scale_x": 1.0, "scale_y": 1.0}
        self._save_calib(); self._push_calib(); self._refresh_display()
        self._man_x.delete(0, tk.END); self._man_x.insert(0, "0")
        self._man_y.delete(0, tk.END); self._man_y.insert(0, "0")
        self._result_lbl.configure(text="Reset to zero.", fg=MUTED)
        self._captured = None
        self._cap_status.configure(text="Waiting...", fg=MUTED)
        self.app.set_status("Calibration reset to zero.")

    def _refresh_display(self):
        ox = self._calib.get("offset_x", 0)
        oy = self._calib.get("offset_y", 0)
        col = SUCCESS if ox == 0 and oy == 0 else ACCENT2
        self._disp_x.configure(text=f"{ox:+d} px", fg=col)
        self._disp_y.configure(text=f"{oy:+d} px", fg=col)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
