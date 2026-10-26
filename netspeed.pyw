"""NetSpeed Monitor - a lightweight network speed monitor for Windows.

- Starts measuring right away; lives in the system tray (double-click: open, right-click: menu)
- Floating window: one line, fixed-width numbers that don't jump around, dark/light theme,
  hover to see today's usage
- Main window: live speed, a scaled chart with hover read-out, today / this month /
  this session / peak, and the last 7 days
- Settings: adapter, refresh interval, units, start with Windows, floating window look,
  auto shutdown when the speed stays low, language (中文 / 日本語 / English)

Settings and daily usage are stored in netspeed.json next to this file (created on first run).
Requires: Windows, Python 3.8+, psutil.  Run with pythonw.exe so no console window appears.
"""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import winreg
from collections import deque
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

import psutil

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(APP_DIR, "netspeed.json")
# The icon is embedded at the end of this file and written here at startup,
# because the tray, the windows and shortcuts all need an icon *file*.
ICON_PATH = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), "NetSpeedMonitor", "netspeed.ico")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# Declare argument/return types so 64-bit handles are not truncated to 32 bits.
LRESULT = ctypes.c_ssize_t
user32.GetParent.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.LoadImageW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
kernel32.GetModuleHandleW.restype = wt.HMODULE
# Separate handle with use_last_error=True: GetLastError() through windll can be overwritten
# by the interpreter between calls, so the single-instance check reads ctypes.get_last_error().
kernel32_le = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32_le.CreateMutexW.restype = wt.HANDLE
kernel32_le.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]

INTERVALS = [0.5, 1.0, 2.0, 5.0]
RANGES = [1, 5, 15]   # chart time span in minutes
VIRTUAL_KEYWORDS = (
    "virtual", "hyper-v", "vmware", "virtualbox", "vethernet", "pseudo", "loopback", "miniport",
    "npcap", "tap-", "wintun", "wireguard", "tailscale", "zerotier", "tun",
)

# Colors: download blue, upload orange
DOWN = "#0a84ff"
UP = "#ff9f0a"
FLOAT_THEMES = {
    "dark": {"bg": "#1f1f1f", "fg": "#f2f2f2", "border": "#3a3a3a", "down": "#4cc2ff", "up": "#ffb454"},
    "light": {"bg": "#ffffff", "fg": "#1a1a1a", "border": "#cfcfcf", "down": "#0067c0", "up": "#c45500"},
}
WINDOW_BG = "#f3f3f3"
CARD_BG = "#ffffff"
CARD_BORDER = "#e3e3e3"
MUTED = "#6b6b6b"
GRID = "#ececec"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- translations

LANGUAGES = [("zh", "中文"), ("ja", "日本語"), ("en", "English")]
UI_FONTS = {"zh": "Microsoft YaHei UI", "ja": "Yu Gothic UI", "en": "Segoe UI"}

TEXTS = {
    "zh": {
        "app_name": "网速监控",
        "menu_open": "打开主界面", "menu_float": "显示悬浮窗", "menu_settings": "设置…", "menu_exit": "退出",
        "download": "下载", "upload": "上传", "total": "合计",
        "directions": ["下载", "上传", "合计"],
        "power_actions": ["关机", "重启", "睡眠", "休眠", "注销"],
        "range": "{n} 分钟",
        "tip_today": "今日", "tip_month": "本月", "tip_hint": "双击打开主界面，右键查看菜单",
        "card_today": "今天", "card_month": "本月", "card_session": "本次运行", "card_peak": "历史最高",
        "uptime": "时长", "reset": "清零", "reset_confirm": "清零历史最高速度记录？",
        "week": "最近 7 天", "today_short": "今天",
        "now": "现在", "secs_ago": "{n} 秒前", "mins_ago": "{n} 分钟前", "collecting": "正在收集数据…",
        "settings": "设置", "tab_general": "常规", "tab_float": "悬浮窗", "tab_auto": "自动关机",
        "adapter": "网卡", "adapter_auto": "自动（所有物理网卡合计）", "disconnected": "（未连接）",
        "interval": "刷新间隔", "seconds": "{n} 秒",
        "units": "单位", "unit_bytes": "字节（MB/s）", "unit_bits": "比特（Mbps，和宽带套餐一致）",
        "autostart": "开机自动启动", "autostart_failed": "设置失败：{error}",
        "language": "语言", "lang_auto": "自动（跟随系统）",
        "restart_ask": "语言会在重启后生效。现在重启吗？",
        "float_show": "显示悬浮窗", "topmost": "总在最前", "lock": "锁定位置（防止误拖动）",
        "theme": "主题", "theme_dark": "深色", "theme_light": "浅色", "opacity": "不透明度",
        "low_enable": "启用（适合下载完成后自动关机）",
        "condition": "条件", "cond_1": "速度低于", "cond_2": "KB/s，持续", "cond_3": "分钟",
        "then": "然后", "status": "状态",
        "status_off": "未启用", "status_waiting": "等待网速超过设定值",
        "status_counting": "网速已偏低 {low}，{remaining} 后{action}",
        "low_note": "网速要先高于设定值才开始计时，所以启用时电脑空闲也不会马上触发。"
                    "触发前弹出 30 秒倒计时，可以取消；触发一次后自动关闭此功能。",
        "applied": "改动会立即生效", "close": "关闭",
        "tray_low": "低速{action}：", "tray_low_count": "已低速 {t}", "tray_low_wait": "等待网速升高",
        "countdown": "网速已持续偏低，将在 {n} 秒后{action}。", "act_now": "立即{action}", "cancel": "取消",
    },
    "ja": {
        "app_name": "ネット速度モニター",
        "menu_open": "メイン画面を開く", "menu_float": "フローティング表示", "menu_settings": "設定…",
        "menu_exit": "終了",
        "download": "ダウンロード", "upload": "アップロード", "total": "合計",
        "directions": ["ダウンロード", "アップロード", "合計"],
        "power_actions": ["シャットダウン", "再起動", "スリープ", "休止状態", "サインアウト"],
        "range": "{n} 分",
        "tip_today": "今日", "tip_month": "今月", "tip_hint": "ダブルクリックでメイン画面、右クリックでメニュー",
        "card_today": "今日", "card_month": "今月", "card_session": "今回の起動", "card_peak": "最高速度",
        "uptime": "時間", "reset": "リセット", "reset_confirm": "最高速度の記録をリセットしますか？",
        "week": "過去 7 日間", "today_short": "今日",
        "now": "現在", "secs_ago": "{n} 秒前", "mins_ago": "{n} 分前", "collecting": "データ収集中…",
        "settings": "設定", "tab_general": "一般", "tab_float": "フローティング", "tab_auto": "自動シャットダウン",
        "adapter": "ネットワーク", "adapter_auto": "自動（すべての物理アダプターの合計）",
        "disconnected": "（未接続）",
        "interval": "更新間隔", "seconds": "{n} 秒",
        "units": "単位", "unit_bytes": "バイト（MB/s）", "unit_bits": "ビット（Mbps、回線プランと同じ単位）",
        "autostart": "Windows の起動時に自動で開始", "autostart_failed": "設定できませんでした：{error}",
        "language": "言語", "lang_auto": "自動（システムに合わせる）",
        "restart_ask": "言語の変更は再起動後に反映されます。今すぐ再起動しますか？",
        "float_show": "フローティングを表示", "topmost": "常に手前に表示", "lock": "位置を固定（誤ドラッグ防止）",
        "theme": "テーマ", "theme_dark": "ダーク", "theme_light": "ライト", "opacity": "不透明度",
        "low_enable": "有効にする（ダウンロード完了後の自動シャットダウンに便利）",
        "condition": "条件", "cond_1": "の速度が", "cond_2": "KB/s 未満の状態が", "cond_3": "分続いたら",
        "then": "動作", "status": "状態",
        "status_off": "無効", "status_waiting": "速度が設定値を超えるのを待っています",
        "status_counting": "低速状態 {low}、あと {remaining} で{action}",
        # no spaces in this sentence: Tk wraps at spaces, which would split "30" from "秒"
        "low_note": "速度がいったん設定値を超えてから計測を始めるため、有効にした直後にPCが"
                    "アイドル状態でもすぐには実行されません。実行前に30秒のカウントダウンがあり、"
                    "キャンセルできます。一度実行するとこの機能は自動的にオフになります。",
        "applied": "変更はすぐに反映されます", "close": "閉じる",
        "tray_low": "低速時に{action}：", "tray_low_count": "低速 {t}", "tray_low_wait": "速度の上昇待ち",
        "countdown": "低速状態が続いたため、{n} 秒後に{action}します。", "act_now": "今すぐ{action}",
        "cancel": "キャンセル",
    },
    "en": {
        "app_name": "NetSpeed Monitor",
        "menu_open": "Open", "menu_float": "Show floating window", "menu_settings": "Settings…",
        "menu_exit": "Exit",
        "download": "Download", "upload": "Upload", "total": "Total",
        "directions": ["Download", "Upload", "Total"],
        "power_actions": ["Shut down", "Restart", "Sleep", "Hibernate", "Sign out"],
        "range": "{n} min",
        "tip_today": "Today", "tip_month": "Month", "tip_hint": "Double-click to open, right-click for the menu",
        "card_today": "Today", "card_month": "This month", "card_session": "This session",
        "card_peak": "Peak speed",
        "uptime": "Uptime", "reset": "Reset", "reset_confirm": "Reset the peak speed records?",
        "week": "Last 7 days", "today_short": "Today",
        "now": "now", "secs_ago": "{n}s ago", "mins_ago": "{n} min ago", "collecting": "Collecting data…",
        "settings": "Settings", "tab_general": "General", "tab_float": "Floating window",
        "tab_auto": "Auto shutdown",
        "adapter": "Adapter", "adapter_auto": "Automatic (all physical adapters)", "disconnected": " (disconnected)",
        "interval": "Refresh", "seconds": "{n} s",
        "units": "Units", "unit_bytes": "Bytes (MB/s)", "unit_bits": "Bits (Mbps, as ISPs advertise)",
        "autostart": "Start with Windows", "autostart_failed": "Couldn't change the setting: {error}",
        "language": "Language", "lang_auto": "Automatic (system)",
        "restart_ask": "The new language takes effect after a restart. Restart now?",
        "float_show": "Show floating window", "topmost": "Always on top", "lock": "Lock position",
        "theme": "Theme", "theme_dark": "Dark", "theme_light": "Light", "opacity": "Opacity",
        "low_enable": "Enable (handy for shutting down after a download finishes)",
        "condition": "When", "cond_1": "speed stays below", "cond_2": "KB/s for", "cond_3": "minutes",
        "then": "Then", "status": "Status",
        "status_off": "Off", "status_waiting": "Waiting for the speed to rise above the threshold",
        "status_counting": "Low for {low} - {action} in {remaining}",
        "low_note": "Timing starts only after the speed has first risen above the threshold, so enabling "
                    "this on an idle PC won't trigger it right away. A 30-second countdown lets you cancel "
                    "before anything happens, and the feature turns itself off after running once.",
        "applied": "Changes apply immediately", "close": "Close",
        "tray_low": "{action} when idle: ", "tray_low_count": "low for {t}", "tray_low_wait": "waiting for traffic",
        "countdown": "The speed has stayed low. {action} in {n} seconds.", "act_now": "{action} now",
        "cancel": "Cancel",
    },
}
LANG = "en"


def system_language():
    """Map the Windows display language to one of the supported languages (default English)."""
    primary = kernel32.GetUserDefaultUILanguage() & 0x3FF
    return {0x04: "zh", 0x11: "ja"}.get(primary, "en")


def t(key, **kwargs):
    text = TEXTS[LANG].get(key, TEXTS["en"][key])
    return text.format(**kwargs) if kwargs else text


# ---------------------------------------------------------------- formatting

def split_speed(bytes_per_sec, bits):
    """Return (number, unit) with about 3 significant digits so the width stays stable."""
    if bits:
        v, units, base = bytes_per_sec * 8 / 1000, ("Kbps", "Mbps", "Gbps"), 1000
    else:
        v, units, base = bytes_per_sec / 1024, ("KB/s", "MB/s", "GB/s"), 1024
    i = 0
    while v >= 999.5 and i < len(units) - 1:
        v /= base
        i += 1
    return short_number(v), units[i]


def fmt_speed(bytes_per_sec, bits):
    number, unit = split_speed(bytes_per_sec, bits)
    return f"{number} {unit}"


def short_number(v):
    if v < 9.995:
        return f"{v:.2f}"
    if v < 99.95:
        return f"{v:.1f}"
    return f"{v:.0f}"


def fmt_bytes(b):
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while b >= 1024 and i < len(units) - 1:
        b /= 1024
        i += 1
    return f"{b:.0f} {units[i]}" if i == 0 else f"{short_number(b)} {units[i]}"


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}"


def nice_ceiling(v):
    """Round v up to 1, 2, 2.5 or 5 x 10^n - used as the top of the chart's y axis."""
    if v <= 0:
        return 1
    exp = 10 ** int(f"{v:e}".split("e")[1])
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


# ---------------------------------------------------------------- settings & usage storage

DEFAULTS = {
    "language": "auto",
    "adapter": "",            # "" = all physical adapters combined
    "interval": 1.0,
    "bits": False,
    "float_show": True,
    "float_theme": "dark",
    "topmost": True,
    "lock": False,
    "opacity": 92,
    "x": None,
    "y": None,
    "chart_range": 1,
    "max_down": 0.0,
    "max_down_time": "",
    "max_up": 0.0,
    "max_up_time": "",
    "low_dir": 0,
    "low_kb": 50,
    "low_minutes": 5,
    "low_action": 0,
}


class Store:
    """netspeed.json: {"settings": {...}, "usage": {"2026-10-26": [bytes_down, bytes_up], ...}}"""

    def __init__(self):
        self.exists = os.path.exists(DATA_PATH)
        self.data = {"settings": {}, "usage": {}}
        try:
            with open(DATA_PATH, encoding="utf-8-sig") as f:
                loaded = json.load(f)
            self.data["settings"].update(loaded.get("settings", {}))
            self.data["usage"].update(loaded.get("usage", {}))
        except (OSError, ValueError, AttributeError):
            pass

    def save(self):
        """Write the file; return False instead of raising when it can't be written (e.g. the file is
        briefly locked by an antivirus or sync tool), so a failed save never interrupts the UI."""
        tmp = DATA_PATH + ".tmp"   # write then replace, so a crash mid-write can't corrupt the file
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, DATA_PATH)
            return True
        except OSError:
            return False


class Config:
    def __init__(self, store):
        self.store = store
        self.first_run = not store.exists
        saved = store.data["settings"]
        for key, default in DEFAULTS.items():
            value = saved.get(key, default)
            if default is not None and value is not None and type(value) is not type(default):
                try:
                    value = type(default)(value)
                except (TypeError, ValueError):
                    value = default
            setattr(self, key, value)
        if self.language not in ("auto",) + tuple(code for code, _ in LANGUAGES):
            self.language = "auto"
        self.interval = clamp(self.interval, 0.25, 10)
        self.opacity = clamp(self.opacity, 40, 100)
        if self.float_theme not in FLOAT_THEMES:
            self.float_theme = "dark"
        if self.chart_range not in RANGES:
            self.chart_range = 1
        self.low_dir = clamp(self.low_dir, 0, 2)
        self.low_action = clamp(self.low_action, 0, 4)

    def save(self):
        self.store.data["settings"] = {key: getattr(self, key) for key in DEFAULTS}
        return self.store.save()


class Usage:
    """Bytes transferred per day, kept in the store's "usage" section."""

    def __init__(self, store):
        self.store = store
        self.days = store.data["usage"]
        self.dirty = False
        self.last_save = time.time()

    def add(self, rx, tx):
        if not rx and not tx:
            return
        day = self.days.setdefault(date.today().isoformat(), [0, 0])
        day[0] += rx
        day[1] += tx
        self.dirty = True
        if time.time() - self.last_save > 30:
            self.save()

    def save(self):
        if not self.dirty:
            return
        self.last_save = time.time()   # on failure, retry in 30 s rather than on every tick
        if self.store.save():
            self.dirty = False

    def day(self, d):
        return self.days.get(d.isoformat(), [0, 0])

    def month(self, d):
        prefix = d.strftime("%Y-%m")
        rx = sum(v[0] for k, v in self.days.items() if k.startswith(prefix))
        tx = sum(v[1] for k, v in self.days.items() if k.startswith(prefix))
        return rx, tx


def ensure_icon_file():
    """Write the embedded icon to ICON_PATH (skipped when an identical file is already there)."""
    data = base64.b64decode(ICON_DATA)
    try:
        with open(ICON_PATH, "rb") as f:
            if f.read() == data:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(ICON_PATH), exist_ok=True)
    with open(ICON_PATH, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------- measuring

def adapter_descriptions():
    """Adapter name -> hardware description, e.g. "Ethernet" -> "Realtek Gaming 2.5GbE Family Controller"."""
    iphlpapi = ctypes.windll.iphlpapi
    table = ctypes.c_void_p()
    if iphlpapi.GetIfTable2(ctypes.byref(table)) != 0:
        return {}
    try:
        result = {}
        count = ctypes.c_ulong.from_address(table.value).value
        for i in range(count):
            row = table.value + 8 + i * 1352            # MIB_IF_ROW2 is 1352 bytes on x64
            alias = ctypes.wstring_at(row + 28)         # Alias[257]
            result.setdefault(alias, ctypes.wstring_at(row + 542))   # Description[257]
        return result
    finally:
        iphlpapi.FreeMibTable(table)


def is_virtual(name, descriptions):
    text = (name + " " + descriptions.get(name, "")).lower()
    return any(k in text for k in VIRTUAL_KEYWORDS)


class SpeedMeter:
    def __init__(self):
        self.prev = {}
        self.last = None
        self.down = self.up = 0.0
        self.dt = 0.0
        self.desc = adapter_descriptions()
        self.desc_time = time.time()

    @staticmethod
    def adapters():
        """[(name, description, is_up)], connected physical adapters first."""
        desc = adapter_descriptions()
        items = [(name, desc.get(name, name), st.isup) for name, st in psutil.net_if_stats().items()
                 if "loopback" not in name.lower()]
        return sorted(items, key=lambda t: (not t[2], is_virtual(t[0], desc), t[0]))

    def auto_names(self):
        """Connected physical adapters. VPN/virtual adapters are skipped so traffic isn't counted twice."""
        if time.time() - self.desc_time > 60:   # descriptions rarely change; refresh once a minute
            self.desc, self.desc_time = adapter_descriptions(), time.time()
        up = [n for n, st in psutil.net_if_stats().items() if st.isup and "loopback" not in n.lower()]
        return [n for n in up if not is_virtual(n, self.desc)] or up

    def reset(self):
        self.prev = {}
        self.last = None

    def sample(self, adapter):
        """Take one sample; return the bytes (down, up) transferred since the previous one."""
        now = time.perf_counter()
        dt = now - self.last if self.last is not None else 0
        self.last = now
        try:
            counters = psutil.net_io_counters(pernic=True)
            names = [adapter] if adapter in counters else self.auto_names()
        except Exception:
            counters, names = {}, []
        current, d_rx, d_tx = {}, 0, 0
        for name in names:
            if name not in counters:
                continue
            c = counters[name]
            current[name] = (c.bytes_recv, c.bytes_sent)
            prev = self.prev.get(name)
            if prev:
                d_rx += max(0, c.bytes_recv - prev[0])   # counter reset / wrap-around counts as 0
                d_tx += max(0, c.bytes_sent - prev[1])
        self.prev = current
        self.dt = dt if 0 < dt < 30 else 0              # no speed on the first sample or after sleep
        if not self.dt:
            self.down = self.up = 0.0
            return 0, 0
        self.down, self.up = d_rx / dt, d_tx / dt
        return d_rx, d_tx


# ---------------------------------------------------------------- system helpers

def run_power_action(action):
    """0 shut down, 1 restart, 2 sleep, 3 hibernate, 4 sign out."""
    no_window = subprocess.CREATE_NO_WINDOW
    shutdown = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe")
    if action == 0:
        subprocess.Popen([shutdown, "/s", "/t", "0"], creationflags=no_window)
    elif action == 1:
        subprocess.Popen([shutdown, "/r", "/t", "0"], creationflags=no_window)
    elif action == 2:
        ctypes.windll.powrprof.SetSuspendState(False, False, False)
    elif action == 3:
        ctypes.windll.powrprof.SetSuspendState(True, False, False)
    elif action == 4:
        subprocess.Popen([shutdown, "/l"], creationflags=no_window)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "NetSpeedMonitor"


def pythonw_path():
    path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return path if os.path.exists(path) else sys.executable


def autostart_command():
    return f'"{pythonw_path()}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0].lower() == autostart_command().lower()
    except OSError:
        return False


def set_autostart(on):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def work_area_at(x, y, default_to_nearest=True):
    """Work area (screen minus taskbar) of the monitor containing (x, y)."""
    monitor = user32.MonitorFromPoint(wt.POINT(x, y), 2 if default_to_nearest else 0)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def cursor_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def round_corners(toplevel):
    """Rounded corners on Windows 11 (no effect on older versions)."""
    try:
        value = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(user32.GetParent(toplevel.winfo_id()), 33,
                                                   ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------- tray icon (plain Shell_NotifyIcon, no extra packages)

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]


shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


class TrayIcon:
    """System tray icon.

    Important: wndproc runs *inside* tkinter's Windows message dispatch, so it must never call
    tkinter. A nested call clears tkinter's saved thread state, and the next time Tk calls back
    into Python the process aborts. wndproc therefore only queues events in self.events;
    App.poll_tray picks them up from a regular Tk timer.
    """
    WM_TRAY = 0x8001          # WM_APP + 1
    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
    NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4

    def __init__(self, icon_path, tip, on_end_session):
        self.events = deque()
        self.on_end_session = on_end_session   # must not touch tkinter (see the class docstring)
        self.tip = tip
        hinst = kernel32.GetModuleHandleW(None)
        self.proc = WNDPROC(self.wndproc)        # keep a reference or the callback gets garbage-collected
        wc = WNDCLASSW()
        wc.lpfnWndProc = self.proc
        wc.hInstance = hinst
        wc.lpszClassName = "NetSpeedMonitorTray"
        user32.RegisterClassW(ctypes.byref(wc))
        # Register the message before creating the window: wndproc already runs during creation.
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.hwnd = user32.CreateWindowExW(0, wc.lpszClassName, wc.lpszClassName, 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        size = user32.GetSystemMetrics(49)      # SM_CXSMICON
        self.hicon = user32.LoadImageW(None, icon_path, 1, size, size, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
        self.notify(self.NIM_ADD, self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP)

    def notify(self, message, flags):
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = self.WM_TRAY
        data.hIcon = self.hicon
        data.szTip = self.tip[:127]
        shell32.Shell_NotifyIconW(message, ctypes.byref(data))

    def set_tip(self, tip):
        if tip != self.tip:
            self.tip = tip
            self.notify(self.NIM_MODIFY, self.NIF_TIP)

    def remove(self):
        self.notify(self.NIM_DELETE, 0)

    def wndproc(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_TRAY:
            event = lparam & 0xFFFF
            if event == 0x0203:                 # WM_LBUTTONDBLCLK
                self.events.append("open")
            elif event == 0x0205:               # WM_RBUTTONUP
                user32.SetForegroundWindow(self.hwnd)   # pure Win32, safe here
                self.events.append("menu")
            return 0
        if msg == 0x0011:                       # WM_QUERYENDSESSION: Windows is shutting down / signing out
            self.on_end_session()
            return 1                            # allow it
        if msg == self.taskbar_created:         # Explorer restarted: add the icon again
            self.notify(self.NIM_ADD, self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ---------------------------------------------------------------- application

class App:
    def __init__(self):
        global LANG
        store = Store()
        self.cfg = Config(store)
        self.usage = Usage(store)
        if self.cfg.first_run:
            self.cfg.save()
        LANG = system_language() if self.cfg.language == "auto" else self.cfg.language
        ensure_icon_file()
        self.meter = SpeedMeter()
        self.history = deque(maxlen=4000)        # (timestamp, down, up)
        self.start_time = time.time()
        self.session_rx = self.session_tx = 0
        self.low_enabled = False
        self.low_active = False
        self.low_seconds = 0.0
        self.peak_dirty = False
        self.last_peak_save = time.time()
        self.tick_count = 0
        self.restart_requested = False

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(t("app_name"))
        self.root.iconbitmap(default=ICON_PATH)
        self.dpi = self.root.winfo_fpixels("1i")
        ui = UI_FONTS[LANG]
        self.fonts = {
            "ui": tkfont.Font(family=ui, size=9),
            "small": tkfont.Font(family=ui, size=8),
            "title": tkfont.Font(family=ui, size=9, weight="bold"),
            "num": tkfont.Font(family="Segoe UI", size=9),
            "num_small": tkfont.Font(family="Segoe UI", size=8),
            "big": tkfont.Font(family="Segoe UI Semibold", size=22),
        }
        style = ttk.Style(self.root)
        style.configure(".", font=self.fonts["ui"])

        self.menu = self.build_menu()
        self.float = FloatWindow(self)
        self.main = MainWindow(self)
        self.settings_win = None
        self.tray = TrayIcon(ICON_PATH, t("app_name"), self.save_all)
        self.poll_tray()
        if self.cfg.first_run:
            self.main.show()

        self.meter.sample(self.cfg.adapter)
        self.root.after(int(self.cfg.interval * 1000), self.tick)

    def s(self, px):
        """Scale a 96-DPI pixel size to the current screen DPI."""
        return round(px * self.dpi / 96)

    # ---- right-click menu (shared by the tray icon and the floating window)

    def build_menu(self):
        self.var_float = tk.BooleanVar(value=self.cfg.float_show)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=t("menu_open"), command=lambda: self.main.show(), font=self.fonts["title"])
        menu.add_checkbutton(label=t("menu_float"), variable=self.var_float,
                             command=lambda: self.set_float_visible(self.var_float.get()))
        menu.add_command(label=t("menu_settings"), command=self.show_settings)
        menu.add_separator()
        menu.add_command(label=t("menu_exit"), command=self.exit)
        return menu

    def poll_tray(self):
        self.root.after(100, self.poll_tray)
        while self.tray.events:
            event = self.tray.events.popleft()
            if event == "open":
                self.main.show()
            elif event == "menu":
                self.popup_menu_at_cursor()

    def popup_menu_at_cursor(self):
        x, y = cursor_pos()
        self.menu.tk_popup(x, y)

    def set_float_visible(self, show):
        self.cfg.float_show = show
        self.cfg.save()
        self.var_float.set(show)
        self.float.set_visible(show)

    def show_settings(self):
        if self.settings_win is None:
            self.settings_win = SettingsWindow(self)
        else:
            self.settings_win.top.deiconify()
            self.settings_win.top.lift()

    # ---- sampling

    def tick(self):
        c = self.cfg
        self.root.after(int(c.interval * 1000), self.tick)
        d_rx, d_tx = self.meter.sample(c.adapter)
        down, up = self.meter.down, self.meter.up
        self.session_rx += d_rx
        self.session_tx += d_tx
        self.usage.add(d_rx, d_tx)
        self.history.append((time.time(), down, up))

        now = datetime.now().strftime("%Y/%m/%d %H:%M")
        if down > c.max_down:
            c.max_down, c.max_down_time, self.peak_dirty = down, now, True
        if up > c.max_up:
            c.max_up, c.max_up_time, self.peak_dirty = up, now, True
        if self.peak_dirty and time.time() - self.last_peak_save >= 15:
            self.last_peak_save = time.time()
            if c.save():
                self.peak_dirty = False

        self.check_low_speed(down, up, self.meter.dt)
        self.float.refresh()
        self.tick_count += 1
        if c.topmost and self.tick_count % 10 == 0:
            self.float.reassert_topmost()
        self.main.refresh()
        if self.settings_win is not None:
            self.settings_win.refresh_status()

        tip = f"{t('app_name')}\n↓ {fmt_speed(down, c.bits)}\n↑ {fmt_speed(up, c.bits)}"
        if self.low_enabled:
            action = t("power_actions")[c.low_action]
            tip += "\n" + t("tray_low", action=action) + (
                t("tray_low_count", t=fmt_duration(self.low_seconds)) if self.low_active else t("tray_low_wait"))
        self.tray.set_tip(tip)

    def check_low_speed(self, down, up, dt):
        if not self.low_enabled or dt <= 0:
            return
        c = self.cfg
        value = (down, up, down + up)[c.low_dir]
        if value >= c.low_kb * 1024:
            # Start timing only after the speed has been above the threshold once,
            # so enabling the feature on an idle PC doesn't trigger it immediately.
            self.low_active = True
            self.low_seconds = 0.0
            return
        if not self.low_active:
            return
        self.low_seconds += dt
        if self.low_seconds >= c.low_minutes * 60:
            self.set_low_enabled(False)
            CountdownDialog(self, c.low_action)

    def set_low_enabled(self, on):
        self.low_enabled = on
        self.low_active = False
        self.low_seconds = 0.0
        if self.settings_win is not None:
            self.settings_win.var_low.set(on)

    def change_adapter(self, name):
        self.cfg.adapter = name
        self.cfg.save()
        self.meter.reset()
        self.meter.sample(name)

    def save_all(self):
        """Save settings and usage. Plain file I/O only, so it is safe to call from the tray's wndproc."""
        self.cfg.save()
        self.usage.save()

    def restart(self):
        self.restart_requested = True
        self.exit()

    def exit(self):
        self.save_all()
        self.tray.remove()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------- floating window

class FloatWindow:
    def __init__(self, app):
        self.app = app
        top = self.top = tk.Toplevel(app.root)
        top.overrideredirect(True)
        self.canvas = tk.Canvas(top, highlightthickness=1, bd=0)
        self.canvas.pack()
        self.tip = None
        self.tip_job = None
        for event, handler in (("<ButtonPress-1>", self.on_drag_start), ("<B1-Motion>", self.on_drag_move),
                               ("<ButtonRelease-1>", self.on_drag_end),
                               ("<Double-Button-1>", lambda e: app.main.show()),
                               ("<Button-3>", lambda e: app.menu.tk_popup(e.x_root, e.y_root)),
                               ("<Enter>", self.on_enter), ("<Leave>", self.on_leave)):
            self.canvas.bind(event, handler)
        self.apply_settings()
        self.place_initial()
        top.after(50, lambda: round_corners(top))
        if not app.cfg.float_show:
            top.withdraw()

    def reassert_topmost(self):
        if self.top.winfo_viewable():
            # HWND_TOPMOST; SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            user32.SetWindowPos(user32.GetParent(self.top.winfo_id()), wt.HWND(-1), 0, 0, 0, 0, 0x0013)

    def set_visible(self, show):
        if show:
            self.top.deiconify()
        else:
            self.top.withdraw()

    def apply_settings(self):
        c = self.app.cfg
        theme = FLOAT_THEMES[c.float_theme]
        self.canvas.configure(bg=theme["bg"], highlightbackground=theme["border"])
        self.top.attributes("-topmost", c.topmost)
        self.top.attributes("-alpha", c.opacity / 100)
        # Number and unit each get a fixed-width slot (number right-aligned), so the text doesn't
        # shift left and right as the speed changes.
        f, s = self.app.fonts["num"], self.app.s
        self.arrow_w = f.measure("↓")
        self.num_w = f.measure("888.8")
        self.unit_w = max(f.measure(u) for u in ("KB/s", "MB/s", "GB/s", "Kbps", "Mbps", "Gbps"))
        self.block_w = self.arrow_w + s(4) + self.num_w + s(4) + self.unit_w
        self.width = s(10) + self.block_w * 2 + s(14) + s(10)
        self.height = f.metrics("linespace") + s(8)
        self.canvas.configure(width=self.width, height=self.height)
        self.refresh()

    def place_initial(self):
        c = self.app.cfg
        self.top.update_idletasks()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        if c.x is not None and work_area_at(c.x + w // 2, c.y + h // 2, False):
            x, y = c.x, c.y
        else:   # first run, or the saved position is on a monitor that's no longer connected
            left, top, right, bottom = work_area_at(0, 0)
            x, y = right - w - self.app.s(12), bottom - h - self.app.s(12)
        self.top.geometry(f"+{x}+{y}")

    def refresh(self):
        a, c = self.app, self.app.cfg
        theme = FLOAT_THEMES[c.float_theme]
        f, s = a.fonts["num"], a.s
        cv = self.canvas
        cv.delete("all")
        y = self.height // 2
        x = s(10)
        for arrow, value, color in (("↓", a.meter.down, theme["down"]), ("↑", a.meter.up, theme["up"])):
            number, unit = split_speed(value, c.bits)
            cv.create_text(x, y, text=arrow, font=f, fill=color, anchor="w")
            nx = x + self.arrow_w + s(4)
            cv.create_text(nx + self.num_w, y, text=number, font=f, fill=theme["fg"], anchor="e")
            cv.create_text(nx + self.num_w + s(4), y, text=unit, font=f, fill=theme["fg"], anchor="w")
            x += self.block_w + s(14)
        if self.tip is not None:
            self.update_tip()

    # ---- hover: show today's usage

    def on_enter(self, e):
        self.tip_job = self.top.after(600, self.show_tip)

    def on_leave(self, e):
        if self.tip_job:
            self.top.after_cancel(self.tip_job)
            self.tip_job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

    def show_tip(self):
        self.tip_job = None
        tip = self.tip = tk.Toplevel(self.top)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        self.tip_label = tk.Label(tip, font=self.app.fonts["ui"], bg="#ffffe1", fg="#1a1a1a", justify="left",
                                  bd=1, relief="solid", padx=8, pady=4)
        self.tip_label.pack()
        self.update_tip()
        tip.update_idletasks()
        x, y = self.top.winfo_rootx(), self.top.winfo_rooty()
        th = tip.winfo_reqheight()
        tip.geometry(f"+{x}+{y - th - 4 if y - th - 4 > 0 else y + self.height + 4}")

    def update_tip(self):
        u = self.app.usage
        today = u.day(date.today())
        month = u.month(date.today())
        self.tip_label.configure(text=f"{t('tip_today')}  ↓ {fmt_bytes(today[0])}   ↑ {fmt_bytes(today[1])}\n"
                                      f"{t('tip_month')}  ↓ {fmt_bytes(month[0])}   ↑ {fmt_bytes(month[1])}\n"
                                      f"{t('tip_hint')}")

    # ---- dragging (snaps to screen edges when released close to one)

    def on_drag_start(self, e):
        self.on_leave(e)
        self.drag_offset = (e.x_root - self.top.winfo_x(), e.y_root - self.top.winfo_y())

    def on_drag_move(self, e):
        if self.app.cfg.lock:
            return
        dx, dy = self.drag_offset
        self.top.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def on_drag_end(self, e):
        if self.app.cfg.lock:
            return
        x, y = self.top.winfo_x(), self.top.winfo_y()
        w, h = self.top.winfo_width(), self.top.winfo_height()
        left, top, right, bottom = work_area_at(x + w // 2, y + h // 2)
        d = self.app.s(12)
        if abs(x - left) < d:
            x = left
        if abs(right - (x + w)) < d:
            x = right - w
        if abs(y - top) < d:
            y = top
        if abs(bottom - (y + h)) < d:
            y = bottom - h
        self.top.geometry(f"+{x}+{y}")
        self.app.cfg.x, self.app.cfg.y = x, y
        self.app.cfg.save()


# ---------------------------------------------------------------- main window

class Card:
    """A small white card: a title and a few "label  value" rows."""

    def __init__(self, app, parent, title, rows):
        self.frame = tk.Frame(parent, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER)
        tk.Label(self.frame, text=title, font=app.fonts["title"], bg=CARD_BG, fg="#1a1a1a",
                 anchor="w").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 4))
        self.values = {}
        for i, (key, label, color) in enumerate(rows, start=1):
            tk.Label(self.frame, text=label, font=app.fonts["ui"], bg=CARD_BG, fg=color or MUTED,
                     anchor="w").grid(row=i, column=0, sticky="w", padx=(10, 6))
            value = tk.Label(self.frame, font=app.fonts["num"], bg=CARD_BG, fg="#1a1a1a", anchor="w")
            value.grid(row=i, column=1, sticky="w", padx=(0, 10))
            self.values[key] = value
        tk.Frame(self.frame, height=6, bg=CARD_BG).grid(row=len(rows) + 1, column=0)
        self.frame.columnconfigure(1, weight=1)

    def set(self, key, text):
        self.values[key].configure(text=text)


class MainWindow:
    def __init__(self, app):
        self.app = app
        s = app.s
        top = self.top = tk.Toplevel(app.root, bg=WINDOW_BG)
        top.title(t("app_name"))
        top.withdraw()
        top.protocol("WM_DELETE_WINDOW", top.withdraw)   # closing only hides it; the app stays in the tray
        top.minsize(s(560), s(440))
        top.geometry(f"{s(720)}x{s(600)}")
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        self.hover_x = None
        down_label, up_label = "↓ " + t("download"), "↑ " + t("upload")

        # ---- header: live speed
        head = tk.Frame(top, bg=WINDOW_BG)
        head.grid(row=0, column=0, sticky="ew", padx=s(16), pady=(s(12), s(4)))
        self.big = {}
        for col, (key, label, color) in enumerate((("down", down_label, DOWN), ("up", up_label, UP))):
            box = tk.Frame(head, bg=WINDOW_BG)
            box.grid(row=0, column=col, sticky="w", padx=(0, s(36)))
            tk.Label(box, text=label, font=app.fonts["ui"], bg=WINDOW_BG, fg=color).pack(anchor="w")
            value = tk.Label(box, font=app.fonts["big"], bg=WINDOW_BG, fg="#1a1a1a")
            value.pack(anchor="w")
            self.big[key] = value
        head.columnconfigure(2, weight=1)
        right = tk.Frame(head, bg=WINDOW_BG)
        right.grid(row=0, column=3, sticky="se")
        self.cb_range = ttk.Combobox(right, values=[t("range", n=n) for n in RANGES], state="readonly", width=8)
        self.cb_range.current(RANGES.index(app.cfg.chart_range))
        self.cb_range.bind("<<ComboboxSelected>>", self.on_range)
        self.cb_range.pack(side="left", padx=(0, s(8)))
        ttk.Button(right, text=t("settings"), command=app.show_settings).pack(side="left")

        # ---- chart
        chart_frame = tk.Frame(top, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER)
        chart_frame.grid(row=1, column=0, sticky="nsew", padx=s(16), pady=s(4))
        self.chart = tk.Canvas(chart_frame, bg=CARD_BG, highlightthickness=0, height=s(180))
        self.chart.pack(fill="both", expand=True, padx=s(4), pady=s(4))
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        self.chart.bind("<Motion>", self.on_chart_motion)
        self.chart.bind("<Leave>", self.on_chart_leave)

        # ---- usage cards
        cards = tk.Frame(top, bg=WINDOW_BG)
        cards.grid(row=2, column=0, sticky="ew", padx=s(16), pady=s(4))
        traffic_rows = [("down", down_label, DOWN), ("up", up_label, UP), ("total", t("total"), None)]
        self.cards = {
            "today": Card(app, cards, t("card_today"), traffic_rows),
            "month": Card(app, cards, t("card_month"), traffic_rows),
            "session": Card(app, cards, t("card_session"),
                            [("time", t("uptime"), None), ("down", down_label, DOWN), ("up", up_label, UP)]),
            "peak": Card(app, cards, t("card_peak"), [("down", down_label, DOWN), ("down_t", "", None),
                                                      ("up", up_label, UP), ("up_t", "", None)]),
        }
        for i, card in enumerate(self.cards.values()):
            card.frame.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else s(8), 0))
            cards.columnconfigure(i, weight=1, uniform="card")
        reset = tk.Label(self.cards["peak"].frame, text=t("reset"), font=app.fonts["small"], bg=CARD_BG, fg=DOWN,
                         cursor="hand2")
        reset.grid(row=0, column=1, sticky="e", padx=10)
        reset.bind("<Button-1>", lambda e: self.reset_peaks())

        # ---- last 7 days
        week_frame = tk.Frame(top, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER)
        week_frame.grid(row=3, column=0, sticky="ew", padx=s(16), pady=(s(4), s(14)))
        week_title = tk.Label(week_frame, text=t("week"), font=app.fonts["title"], bg=CARD_BG)
        week_title.pack(anchor="w", padx=10, pady=(8, 0))
        self.week = tk.Canvas(week_frame, bg=CARD_BG, highlightthickness=0, height=s(96))
        self.week.pack(fill="x", padx=s(4), pady=(0, s(4)))
        self.week.bind("<Configure>", lambda e: self.draw_week())

    def visible(self):
        return self.top.state() == "normal"

    def show(self):
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
        self.refresh(force=True)

    def on_range(self, e):
        self.app.cfg.chart_range = RANGES[self.cb_range.current()]
        self.app.cfg.save()
        self.draw_chart()

    def reset_peaks(self):
        if not messagebox.askyesno(t("app_name"), t("reset_confirm"), parent=self.top):
            return
        c = self.app.cfg
        c.max_down = c.max_up = 0.0
        c.max_down_time = c.max_up_time = ""
        c.save()
        self.refresh(force=True)

    def refresh(self, force=False):
        if not (force or self.visible()):
            return
        a, c = self.app, self.app.cfg
        self.big["down"].configure(text=fmt_speed(a.meter.down, c.bits))
        self.big["up"].configure(text=fmt_speed(a.meter.up, c.bits))

        today = a.usage.day(date.today())
        month = a.usage.month(date.today())
        for key, (rx, tx) in (("today", today), ("month", month)):
            card = self.cards[key]
            card.set("down", fmt_bytes(rx))
            card.set("up", fmt_bytes(tx))
            card.set("total", fmt_bytes(rx + tx))
        sess = self.cards["session"]
        sess.set("time", fmt_duration(time.time() - a.start_time))
        sess.set("down", fmt_bytes(a.session_rx))
        sess.set("up", fmt_bytes(a.session_tx))
        peak = self.cards["peak"]
        peak.set("down", fmt_speed(c.max_down, c.bits))
        peak.set("down_t", c.max_down_time[5:16])   # "03/28 15:10": no year/seconds, so it fits the card
        peak.set("up", fmt_speed(c.max_up, c.bits))
        peak.set("up_t", c.max_up_time[5:16])

        self.draw_chart()
        self.draw_week()

    # ---- speed chart

    def chart_geometry(self):
        s = self.app.s
        w, h = self.chart.winfo_width(), self.chart.winfo_height()
        left, right, top, bottom = s(62), w - s(10), s(10), h - s(22)
        return left, right, top, bottom

    def draw_chart(self):
        a, c, s = self.app, self.app.cfg, self.app.s
        cv = self.chart
        cv.delete("all")
        left, right, top, bottom = self.chart_geometry()
        if right - left < 50 or bottom - top < 40:
            return
        now = time.time()
        span = c.chart_range * 60
        points = [p for p in a.history if p[0] >= now - span - 2]
        peak = max([max(p[1], p[2]) for p in points] + [1])

        # y axis: pick a unit that fits the peak, then round the top to a "nice" number
        unit_bytes, unit = (1, "B/s")
        for ub, un in ((1024 ** 3, "GB/s"), (1024 ** 2, "MB/s"), (1024, "KB/s")):
            if peak >= ub:
                unit_bytes, unit = ub, un
                break
        if c.bits:
            for ub, un in ((1e9 / 8, "Gbps"), (1e6 / 8, "Mbps"), (1e3 / 8, "Kbps"), (1 / 8, "bps")):
                if peak >= ub:
                    unit_bytes, unit = ub, un
                    break
        top_value = nice_ceiling(peak / unit_bytes)
        for i in range(5):
            v = top_value * i / 4
            y = bottom - (bottom - top) * i / 4
            cv.create_line(left, y, right, y, fill=GRID)
            label = f"{v:g}" if v else "0"
            cv.create_text(left - s(6), y, text=f"{label} {unit}" if i == 4 else label, anchor="e",
                           font=a.fonts["num_small"], fill=MUTED)
        # x axis: time
        for i in range(5):
            x = left + (right - left) * i / 4
            secs = span - span * i / 4
            if secs == 0:
                text = t("now")
            elif secs >= 60:
                text = t("mins_ago", n=f"{secs / 60:g}")
            else:
                text = t("secs_ago", n=f"{secs:g}")
            anchor = "nw" if i == 0 else "ne" if i == 4 else "n"   # keep the end labels inside the chart
            cv.create_text(x, bottom + s(4), text=text, anchor=anchor, font=a.fonts["small"], fill=MUTED)
            if 0 < i < 4:
                cv.create_line(x, top, x, bottom, fill=GRID, dash=(2, 3))
        cv.create_line(left, bottom, right, bottom, fill="#d0d0d0")

        if len(points) < 2:
            cv.create_text((left + right) / 2, (top + bottom) / 2, text=t("collecting"), fill=MUTED,
                           font=a.fonts["ui"])
            return
        scale = (bottom - top) / (top_value * unit_bytes)

        def xy(ts, v):
            return left + (right - left) * (1 - (now - ts) / span), bottom - v * scale

        down_pts = [(max(left, x), y) for x, y in (xy(ts, d) for ts, d, _ in points)]
        up_pts = [(max(left, x), y) for x, y in (xy(ts, u) for ts, _, u in points)]
        area = [(down_pts[0][0], bottom)] + down_pts + [(down_pts[-1][0], bottom)]
        cv.create_polygon(*[v for p in area for v in p], fill="#d6e9ff", outline="")
        cv.create_line(*[v for p in down_pts for v in p], fill=DOWN, width=max(1, s(2)))
        cv.create_line(*[v for p in up_pts for v in p], fill=UP, width=max(1, s(2)))
        # legend
        x = right - s(10)
        for text, color in ((t("upload"), UP), (t("download"), DOWN)):
            item = cv.create_text(x, top + s(6), text=text, anchor="e", font=a.fonts["small"], fill=MUTED)
            x = cv.bbox(item)[0] - s(4)
            cv.create_line(x - s(16), top + s(6), x, top + s(6), fill=color, width=s(3))
            x -= s(28)

        if self.hover_x is not None and left <= self.hover_x <= right:
            self.draw_hover(points, left, right, top, bottom, now, span, scale)

    def draw_hover(self, points, left, right, top, bottom, now, span, scale):
        a, s = self.app, self.app.s
        t_at = now - span * (1 - (self.hover_x - left) / (right - left))
        ts, d, u = min(points, key=lambda p: abs(p[0] - t_at))
        x = left + (right - left) * (1 - (now - ts) / span)
        if x < left:
            return
        cv = self.chart
        cv.create_line(x, top, x, bottom, fill="#9a9a9a")
        for v, color in ((d, DOWN), (u, UP)):
            y = bottom - v * scale
            r = s(3)
            cv.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="white")
        text = f"{datetime.fromtimestamp(ts):%H:%M:%S}\n↓ {fmt_speed(d, a.cfg.bits)}\n↑ {fmt_speed(u, a.cfg.bits)}"
        on_left = x < (left + right) / 2
        item = cv.create_text(x + s(10) if on_left else x - s(10), top + s(18), text=text,
                              anchor="nw" if on_left else "ne", font=a.fonts["num_small"], fill="#1a1a1a")
        x1, y1, x2, y2 = cv.bbox(item)
        box = cv.create_rectangle(x1 - s(6), y1 - s(4), x2 + s(6), y2 + s(4), fill="white", outline="#d0d0d0")
        cv.tag_raise(item, box)

    def on_chart_motion(self, e):
        self.hover_x = e.x
        self.draw_chart()

    def on_chart_leave(self, e):
        self.hover_x = None
        self.draw_chart()

    # ---- last 7 days bar chart

    def draw_week(self):
        a, s = self.app, self.app.s
        cv = self.week
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 100:
            return
        days = [date.today() - timedelta(days=i) for i in range(6, -1, -1)]
        data = [a.usage.day(d) for d in days]
        peak = max([rx + tx for rx, tx in data] + [1])
        slot = (w - s(20)) / 7
        bar_w = min(s(36), slot * 0.5)
        base, top = h - s(20), s(18)
        for i, (d, (rx, tx)) in enumerate(zip(days, data)):
            cx = s(10) + slot * (i + 0.5)
            hd = (base - top) * rx / peak
            hu = (base - top) * tx / peak
            if rx or tx:
                cv.create_rectangle(cx - bar_w / 2, base - hd, cx + bar_w / 2, base, fill=DOWN, outline="")
                cv.create_rectangle(cx - bar_w / 2, base - hd - hu, cx + bar_w / 2, base - hd, fill=UP, outline="")
                cv.create_text(cx, base - hd - hu - s(3), text=fmt_bytes(rx + tx), anchor="s",
                               font=a.fonts["num_small"], fill="#1a1a1a")
            label = t("today_short") if d == date.today() else f"{d.month}/{d.day}"
            cv.create_text(cx, base + s(4), text=label, anchor="n", font=a.fonts["small"], fill=MUTED)
        cv.create_line(s(10), base, w - s(10), base, fill="#d0d0d0")


# ---------------------------------------------------------------- settings window

class SettingsWindow:
    def __init__(self, app):
        self.app = app
        c = app.cfg
        top = self.top = tk.Toplevel(app.root)
        top.title(t("settings"))
        top.resizable(False, False)
        top.protocol("WM_DELETE_WINDOW", self.close)
        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        # ---- general
        general = ttk.Frame(nb, padding=12)
        nb.add(general, text=t("tab_general"))
        try:
            self.adapters = SpeedMeter.adapters()
        except Exception:
            self.adapters = []
        self.adapter_names = [""] + [name for name, _, _ in self.adapters]
        labels = [t("adapter_auto")] + [desc + ("" if up else t("disconnected")) for _, desc, up in self.adapters]
        self.cb_adapter = ttk.Combobox(general, values=labels, state="readonly", width=42)
        self.cb_adapter.current(self.adapter_names.index(c.adapter) if c.adapter in self.adapter_names else 0)
        self.cb_adapter.bind("<<ComboboxSelected>>",
                             lambda e: app.change_adapter(self.adapter_names[self.cb_adapter.current()]))
        self.row(general, 0, t("adapter"), self.cb_adapter)
        self.cb_interval = ttk.Combobox(general, values=[t("seconds", n=f"{v:g}") for v in INTERVALS],
                                        state="readonly", width=8)
        self.cb_interval.current(INTERVALS.index(c.interval) if c.interval in INTERVALS else 1)
        self.cb_interval.bind("<<ComboboxSelected>>",
                              lambda e: self.set("interval", INTERVALS[self.cb_interval.current()]))
        self.row(general, 1, t("interval"), self.cb_interval)
        self.var_bits = tk.BooleanVar(value=c.bits)
        units = ttk.Frame(general)
        ttk.Radiobutton(units, text=t("unit_bytes"), value=False, variable=self.var_bits,
                        command=lambda: self.set("bits", self.var_bits.get())).pack(side="left")
        ttk.Radiobutton(units, text=t("unit_bits"), value=True, variable=self.var_bits,
                        command=lambda: self.set("bits", self.var_bits.get())).pack(side="left", padx=(12, 0))
        self.row(general, 2, t("units"), units)
        self.lang_codes = ["auto"] + [code for code, _ in LANGUAGES]
        self.cb_lang = ttk.Combobox(general, values=[t("lang_auto")] + [name for _, name in LANGUAGES],
                                    state="readonly", width=20)
        self.cb_lang.current(self.lang_codes.index(c.language))
        self.cb_lang.bind("<<ComboboxSelected>>", self.on_language)
        self.row(general, 3, t("language"), self.cb_lang)
        self.var_autostart = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(general, text=t("autostart"), variable=self.var_autostart,
                        command=self.on_autostart).grid(row=4, column=1, sticky="w", pady=6)

        # ---- floating window
        fl = ttk.Frame(nb, padding=12)
        nb.add(fl, text=t("tab_float"))
        self.var_float = app.var_float
        ttk.Checkbutton(fl, text=t("float_show"), variable=self.var_float,
                        command=lambda: app.set_float_visible(self.var_float.get())).grid(
            row=0, column=1, sticky="w", pady=4)
        self.var_top = tk.BooleanVar(value=c.topmost)
        ttk.Checkbutton(fl, text=t("topmost"), variable=self.var_top,
                        command=lambda: self.set("topmost", self.var_top.get(), float_=True)).grid(
            row=1, column=1, sticky="w", pady=4)
        self.var_lock = tk.BooleanVar(value=c.lock)
        ttk.Checkbutton(fl, text=t("lock"), variable=self.var_lock,
                        command=lambda: self.set("lock", self.var_lock.get())).grid(row=2, column=1, sticky="w", pady=4)
        self.var_theme = tk.StringVar(value=c.float_theme)
        themes = ttk.Frame(fl)
        for value, key in (("dark", "theme_dark"), ("light", "theme_light")):
            ttk.Radiobutton(themes, text=t(key), value=value, variable=self.var_theme,
                            command=lambda: self.set("float_theme", self.var_theme.get(), float_=True)).pack(
                side="left", padx=(0, 12))
        self.row(fl, 3, t("theme"), themes)
        self.var_opacity = tk.IntVar(value=c.opacity)
        opacity = ttk.Frame(fl)
        ttk.Scale(opacity, from_=40, to=100, variable=self.var_opacity, length=200,
                  command=lambda v: self.set("opacity", int(float(v)), float_=True)).pack(side="left")
        self.lb_opacity = ttk.Label(opacity, width=5)
        self.lb_opacity.pack(side="left", padx=6)
        self.row(fl, 4, t("opacity"), opacity)

        # ---- auto shutdown
        low_tab = ttk.Frame(nb, padding=12)
        nb.add(low_tab, text=t("tab_auto"))
        self.var_low = tk.BooleanVar(value=app.low_enabled)
        ttk.Checkbutton(low_tab, text=t("low_enable"), variable=self.var_low,
                        command=lambda: app.set_low_enabled(self.var_low.get())).grid(
            row=0, column=1, sticky="w", pady=4)
        self.var_kb = tk.StringVar(value=str(c.low_kb))
        self.var_min = tk.StringVar(value=str(c.low_minutes))
        cond = ttk.Frame(low_tab)
        self.cb_dir = ttk.Combobox(cond, values=t("directions"), state="readonly", width=10)
        self.cb_dir.current(c.low_dir)
        self.cb_dir.bind("<<ComboboxSelected>>", lambda e: self.set("low_dir", self.cb_dir.current()))
        self.cb_dir.pack(side="left")
        ttk.Label(cond, text=t("cond_1")).pack(side="left", padx=(6, 4))
        ttk.Spinbox(cond, from_=1, to=1000000, width=7, textvariable=self.var_kb).pack(side="left")
        ttk.Label(cond, text=t("cond_2")).pack(side="left", padx=4)
        ttk.Spinbox(cond, from_=1, to=1440, width=5, textvariable=self.var_min).pack(side="left")
        ttk.Label(cond, text=t("cond_3")).pack(side="left", padx=4)
        self.row(low_tab, 1, t("condition"), cond)
        self.cb_action = ttk.Combobox(low_tab, values=t("power_actions"), state="readonly", width=12)
        self.cb_action.current(c.low_action)
        self.cb_action.bind("<<ComboboxSelected>>", lambda e: self.set("low_action", self.cb_action.current()))
        self.row(low_tab, 2, t("then"), self.cb_action)
        self.lb_status = ttk.Label(low_tab, foreground=DOWN)
        self.row(low_tab, 3, t("status"), self.lb_status)
        ttk.Label(low_tab, text=t("low_note"), foreground=MUTED, wraplength=app.s(480), justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        for var, key in ((self.var_kb, "low_kb"), (self.var_min, "low_minutes")):
            var.trace_add("write", lambda *args, var=var, key=key: self.set_int(var, key))

        bottom = ttk.Frame(top, padding=(10, 8, 10, 10))
        bottom.pack(fill="x")
        ttk.Label(bottom, text=t("applied"), foreground=MUTED).pack(side="left")
        ttk.Button(bottom, text=t("close"), command=self.close).pack(side="right")
        self.refresh_status()
        top.update_idletasks()
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry(f"+{(sw - top.winfo_width()) // 2}+{(sh - top.winfo_height()) // 3}")

    @staticmethod
    def row(parent, r, label, widget):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=6)
        widget.grid(row=r, column=1, sticky="w", pady=6)

    def set(self, key, value, float_=False):
        setattr(self.app.cfg, key, value)
        self.app.cfg.save()
        if float_:
            self.app.float.apply_settings()
        self.app.main.refresh()
        self.refresh_status()

    def set_int(self, var, key):
        try:
            value = max(1, int(var.get()))
        except ValueError:
            return   # half-typed value: don't save yet
        self.set(key, value)

    def on_language(self, e):
        code = self.lang_codes[self.cb_lang.current()]
        if code == self.app.cfg.language:
            return
        self.set("language", code)
        if messagebox.askyesno(t("app_name"), t("restart_ask"), parent=self.top):
            self.app.restart()

    def on_autostart(self):
        try:
            set_autostart(self.var_autostart.get())
        except OSError as ex:
            messagebox.showerror(t("app_name"), t("autostart_failed", error=ex), parent=self.top)
            self.var_autostart.set(autostart_enabled())

    def refresh_status(self):
        a = self.app
        self.lb_opacity.configure(text=f"{a.cfg.opacity}%")
        if not a.low_enabled:
            text = t("status_off")
        elif not a.low_active:
            text = t("status_waiting")
        else:
            remaining = max(0, a.cfg.low_minutes * 60 - a.low_seconds)
            text = t("status_counting", low=fmt_duration(a.low_seconds), remaining=fmt_duration(remaining),
                     action=t("power_actions")[a.cfg.low_action])
        self.lb_status.configure(text=text)

    def close(self):
        self.app.settings_win = None
        self.top.destroy()


class CountdownDialog:
    """30-second countdown before the power action runs, with Cancel."""

    def __init__(self, app, action):
        self.app = app
        self.action = t("power_actions")[action]
        self.action_index = action
        self.left = 30
        top = self.top = tk.Toplevel(app.root)
        top.title(t("app_name"))
        top.resizable(False, False)
        top.attributes("-topmost", True)
        top.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(top, padding=16)
        body.pack()
        self.message = ttk.Label(body, font=app.fonts["ui"])
        self.message.pack(anchor="w", pady=(0, 12))
        buttons = ttk.Frame(body)
        buttons.pack(anchor="e")
        ttk.Button(buttons, text=t("act_now", action=self.action), command=self.finish).pack(side="left", padx=(6, 0))
        cancel = ttk.Button(buttons, text=t("cancel"), command=self.close)
        cancel.pack(side="left", padx=(6, 0))
        self.update_text()
        top.update_idletasks()
        top.geometry(f"+{(top.winfo_screenwidth() - top.winfo_width()) // 2}"
                     f"+{(top.winfo_screenheight() - top.winfo_height()) // 3}")
        top.bell()
        cancel.focus_set()
        self.job = top.after(1000, self.countdown)

    def update_text(self):
        self.message.configure(text=t("countdown", n=self.left, action=self.action))

    def countdown(self):
        self.left -= 1
        self.update_text()
        if self.left <= 0:
            self.finish()
        else:
            self.job = self.top.after(1000, self.countdown)

    def finish(self):
        self.close()
        self.app.save_all()
        run_power_action(self.action_index)

    def close(self):
        self.top.after_cancel(self.job)
        self.top.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()
    # Single instance. After a restart the old process may still be exiting, so wait a moment.
    for _ in range(30):
        mutex = kernel32_le.CreateMutexW(None, False, "NetSpeedMonitor_SingleInstance")
        if ctypes.get_last_error() != 183:   # ERROR_ALREADY_EXISTS
            break
        kernel32.CloseHandle(wt.HANDLE(mutex))
        if "--restart" not in sys.argv:
            return
        time.sleep(0.1)
    else:
        return
    app = App()
    app.run()
    kernel32.CloseHandle(wt.HANDLE(mutex))
    if app.restart_requested:
        subprocess.Popen([pythonw_path(), os.path.abspath(__file__), "--restart"])


# ---------------------------------------------------------------- embedded icon (base64 of an .ico file)

ICON_DATA = (
    "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"
    "Nj7Ty+iw6QbJsuvEnESA64+T9Mft2cudb/bS1SHPradxLaT1mVz1JarJSv3/TXPv46d468I4ooz37T5ARvyqbxnPX/wo2pbZ"
    "xHWJVFzKpCoFDkJqJGfyCIhjZEZNKVhxJ8/ydQVv9Y9CotzONVc/yYj95xaBKvAYoSFRptqL7ySJQoEWh+wtbfty16rT6Oik"
    "baiZlmYVJMykrsacnIRmHF4FEtIoHFcQjF4idA/GBP6Rk42U2xGOyX9VpZBqE871TWKl8GYZLe7gN/eOxC2mThHZPtmTK/9E"
    "ZvhpUrNdtA74wJnO4je5pC+fwh/bZpWzFhXHv3ONoq1N0ph8yLYyU3M8tAptO+dGm7YrZZZEY4IwGEQGX2fHAxnYYifO1bhN"
    "Nqy+tX8uzc2/xWv6CmEaQtO1EHEHorpe0PWgFraxbrKidB28RsiMniQ98SC/+9Lp+G8H8buWqcLLb+Nv6NqAlI8hnTtwffPy"
    "krqaMH/LCCEMLqDZzdnL23jxgUw++JkErE1GRWg2vuCjZi0nEivRYatNYTWPgdBxC0j2odyjqNFD7P7iZfuVkblpcldka3Sd"
    "/91RwiyWwmnqf37mwT5Ou91iAAAAAElFTkSuQmCC"
)


if __name__ == "__main__":
    main()
