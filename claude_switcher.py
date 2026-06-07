# -*- coding: utf-8 -*-
"""
Claude 模式切換器（終端機 claude 指令適用）
在「原版訂閱」與「Free Claude Code」之間一鍵切換，並可換免費模型。

作用對象：終端機（PowerShell / cmd）裡執行的 claude 指令。
切換原理是改寫 User 層級環境變數 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN，
所以切換後要「開一個新的終端機視窗」再跑 claude 才會生效（舊視窗不會自動更新）。
註：Claude Desktop 內建的 Claude Code（Cowork）會強制走官方，無法用此方式改。

功能：
- 系統匣圖示（藍 = 原版訂閱，綠 = Free Claude Code）
- 點圖示跳出狀態視窗（模式 / fcc-server / proxy 連線 / 免費模型）
- 下拉選單換免費模型（可直接輸入完整模型名）
- 切換前二次確認

所有路徑與設定存於同目錄的 config.json（首次執行自動偵測產生），可手動修改。
"""

import os
import json
import time
import shutil
import threading
import subprocess
import urllib.request
import webbrowser
import winreg
import ctypes
from ctypes import wintypes

import psutil
import pystray
from PIL import Image, ImageDraw
import tkinter as tk
from tkinter import ttk, messagebox

# ===================== 設定區（首次執行自動產生 config.json）=====================
# 設定來源優先序：腳本同目錄的 config.json > 自動偵測。
# 首次執行（找不到 config.json）會自動偵測本機路徑並寫出 config.json，方便日後手動調整。
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


def auto_detect_config():
    """偵測本機 fcc 相關路徑與設定，回傳預設設定 dict。"""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")

    # fcc-server.exe：先試 uv tool 預設位置（~\.local\bin），再回退到 PATH
    exe = os.path.join(home, ".local", "bin", "fcc-server.exe")
    if not os.path.isfile(exe):
        found = shutil.which("fcc-server")
        if found:
            exe = found

    return {
        "fcc_server_exe": exe,
        "fcc_env_path": os.path.join(home, ".fcc", ".env"),
        # 避免啟動時下載 tiktoken（被 TLS 攔截擋住）
        "tiktoken_cache_dir": os.path.join(home, ".fcc", "tiktoken-cache"),
        "port": 8082,
        "auth_token": "freecc",
    }


def load_config():
    """讀 config.json；不存在或損壞則自動偵測，並寫出一份新的。"""
    defaults = auto_detect_config()
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise ValueError("config.json 不是 JSON 物件")
            for k, v in defaults.items():  # 補上缺漏的鍵（向後相容）
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass  # 壞掉（格式錯/非物件等）就改用偵測值並重寫
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(defaults, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return defaults


_cfg = load_config()

FCC_SERVER_EXE     = _cfg["fcc_server_exe"]
FCC_ENV_PATH       = _cfg["fcc_env_path"]
TIKTOKEN_CACHE_DIR = _cfg["tiktoken_cache_dir"]  # 避免啟動時下載 tiktoken（被 TLS 攔截擋住）
try:
    PORT = int(_cfg["port"])                      # 容錯：手動把 port 填成字串時也能正常比對
except (TypeError, ValueError):
    PORT = 8082
AUTH_TOKEN         = str(_cfg["auth_token"])      # 會寫進 ANTHROPIC_AUTH_TOKEN（強制字串，供 winreg 寫入）

PROXY_BASE_URL = f"http://127.0.0.1:{PORT}"       # 會寫進 ANTHROPIC_BASE_URL
HEALTH_URL     = f"{PROXY_BASE_URL}/health"
MODELS_API     = f"{PROXY_BASE_URL}/v1/models"
ADMIN_URL      = f"{PROXY_BASE_URL}/admin"

# 下拉選單預設可選的免費模型（可自行增減；下拉框也允許直接輸入完整模型名）
FREE_MODELS = [
    "nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
]

ENV_BASE  = "ANTHROPIC_BASE_URL"
ENV_TOKEN = "ANTHROPIC_AUTH_TOKEN"

GREEN = (46, 204, 113)
BLUE  = (52, 152, 219)
# =============================================================================


# ---------- 環境變數（User 層級，寫入註冊表 HKCU\Environment）----------
def _open_env_key(access):
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, access)


def get_user_env(name):
    try:
        key = _open_env_key(winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return val
    except FileNotFoundError:
        return None


def set_user_env(name, value):
    key = _open_env_key(winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    winreg.CloseKey(key)


def del_user_env(name):
    try:
        key = _open_env_key(winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, name)
        winreg.CloseKey(key)
    except FileNotFoundError:
        pass


def broadcast_env_change():
    """通知系統環境變數已變更，讓之後啟動的程式能讀到新值。"""
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    result = wintypes.DWORD()
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SETTINGCHANGE, 0,
        ctypes.c_wchar_p("Environment"), SMTO_ABORTIFHUNG, 5000,
        ctypes.byref(result),
    )


# ---------- 狀態查詢 ----------
def current_mode():
    """回傳 'free' 或 'official'。"""
    base = get_user_env(ENV_BASE)
    if base and f":{PORT}" in base:
        return "free"
    return "official"


def proxy_ok():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def get_current_model():
    try:
        with open(FCC_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MODEL="):
                    return line.strip().split("=", 1)[1] or "(未設定)"
    except OSError:
        pass
    return "(讀不到)"


def list_models():
    """組出下拉選單用的模型清單：預設清單 + 線上 API 回傳的 provider 型模型。"""
    models = list(FREE_MODELS)
    try:
        req = urllib.request.Request(MODELS_API, headers={"x-api-key": AUTH_TOKEN})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.load(r)
        for m in data.get("data", []):
            disp = m.get("display_name", "")
            if "/" in disp and "no thinking" not in disp.lower():
                models.append(disp)
    except Exception:
        pass
    # 去重、保留順序
    seen, out = set(), []
    for m in models:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


# ---------- fcc-server 控制 ----------
def get_fcc_proc():
    """找出正在監聽指定 port 的程序（fcc-server）。"""
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.laddr and c.laddr.port == PORT and c.status == psutil.CONN_LISTEN and c.pid:
                try:
                    return psutil.Process(c.pid)
                except psutil.Error:
                    return None
    except (psutil.AccessDenied, OSError):
        return None
    return None


def start_fcc_server():
    """背景啟動 fcc-server（隱藏視窗，不跳任何視窗）。

    啟動環境處理：
    - 移除壞掉的 SSL_CERT_FILE（指向已刪除的憑證會干擾）
    - 設 TIKTOKEN_CACHE_DIR 指向永久快取，避免啟動時下載 tiktoken（會被 TLS 攔截擋住而崩）
    fcc-server 改用 Python 3.14.5（OpenSSL 正常），故可直接隱藏啟動並重導輸出。
    """
    env = dict(os.environ)
    env.pop("SSL_CERT_FILE", None)
    env["TIKTOKEN_CACHE_DIR"] = TIKTOKEN_CACHE_DIR
    subprocess.Popen(
        [FCC_SERVER_EXE],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env,
        close_fds=True,
    )


def wait_for_health(timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if proxy_ok():
            return True
        time.sleep(0.5)
    return False


def restart_fcc_server():
    p = get_fcc_proc()
    if p:
        try:
            p.kill()
            p.wait(timeout=5)
        except psutil.Error:
            pass
    time.sleep(1)
    start_fcc_server()
    wait_for_health()


def set_model_in_env(model):
    """改寫 .fcc/.env 的 MODEL= 那一行（只動 MODEL=，不碰 MODEL_OPUS 等）。"""
    with open(FCC_ENV_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith("MODEL="):
            lines[i] = f"MODEL={model}\n"
            found = True
            break
    if not found:
        lines.append(f"MODEL={model}\n")
    with open(FCC_ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ---------- 切換主流程 ----------
def do_switch_to(target):
    if target == "free":
        set_user_env(ENV_BASE, PROXY_BASE_URL)
        set_user_env(ENV_TOKEN, AUTH_TOKEN)
        broadcast_env_change()
        if not proxy_ok():
            start_fcc_server()
            wait_for_health()
    else:
        del_user_env(ENV_BASE)
        del_user_env(ENV_TOKEN)
        broadcast_env_change()
        # fcc-server 保留執行，不影響原版訂閱
    # 終端機 claude 只在「新開的終端機」啟動時才讀到新環境變數，故不需重啟任何程式。


# ===================== 圖形介面 =====================
icon = None
root = None
win = None
widgets = {}
model_var = None


def make_image(color):
    img = Image.new("RGB", (64, 64), (30, 30, 30))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=color)
    return img


def update_icon():
    m = current_mode()
    icon.icon = make_image(GREEN if m == "free" else BLUE)
    icon.title = "Claude：Free Claude Code" if m == "free" else "Claude：原版訂閱"


def set_busy(text):
    if widgets.get("status_line"):
        widgets["status_line"].config(text=text)
    for b in widgets.get("buttons", []):
        b.config(state="disabled")


def refresh():
    """重新讀取所有狀態並更新視窗。"""
    if not (win and win.winfo_exists()):
        return
    widgets["status_line"].config(text="讀取狀態中…")

    def work():
        mode = current_mode()
        proxy = proxy_ok()
        fcc_up = proxy or (get_fcc_proc() is not None)  # health 通就一定在跑；否則看程序是否在聽
        model = get_current_model()
        models = list_models()

        def apply():
            if not (win and win.winfo_exists()):
                return
            is_free = mode == "free"
            widgets["mode"].config(
                text=("🟩 Free Claude Code" if is_free else "🟦 原版訂閱"),
                fg=("#2ecc71" if is_free else "#3498db"),
            )
            widgets["fcc"].config(
                text=(f"✅ 執行中 ({PORT})" if fcc_up else "⛔ 已停止"),
                fg=("#2ecc71" if fcc_up else "#e74c3c"),
            )
            widgets["proxy"].config(
                text=("✅ 正常" if proxy else "⛔ 不通"),
                fg=("#2ecc71" if proxy else "#e74c3c"),
            )
            widgets["model"].config(text=model)
            widgets["switch_btn"].config(
                text=("切換到原版訂閱" if is_free else "切換到 Free Claude Code")
            )
            widgets["model_box"]["values"] = models
            if model and model not in ("(未設定)", "(讀不到)"):
                model_var.set(model)
            for b in widgets.get("buttons", []):
                b.config(state="normal")
            widgets["status_line"].config(text="就緒")

        root.after(0, apply)

    threading.Thread(target=work, daemon=True).start()


def _fcc_error_dialog():
    """fcc 檔案缺失時，依缺哪個檔跳對應錯誤視窗（可在背景執行緒呼叫）。"""
    if not os.path.isfile(FCC_ENV_PATH):
        title = "找不到 fcc 設定檔 (.env)"
        body = (f"找不到 fcc 的 .env 檔：\n\n{FCC_ENV_PATH}\n\n"
                "請開啟 config.json 修正 fcc_env_path 欄位後再試。")
    else:
        title = "找不到 fcc-server"
        body = (f"找不到 fcc-server 程式：\n\n{FCC_SERVER_EXE}\n\n"
                "請開啟 config.json 修正 fcc_server_exe 欄位後再試。")
    root.after(0, lambda: messagebox.showerror(title, body))


def on_switch():
    cur = current_mode()
    target = "official" if cur == "free" else "free"
    label = "原版訂閱" if target == "official" else "Free Claude Code"
    ok = messagebox.askokcancel(
        "確認切換",
        f"即將切換到【{label}】。\n\n"
        "此設定作用於『終端機的 claude 指令』。\n"
        "切換後請『開一個新的終端機視窗』再執行 claude 才會生效\n"
        "（已經開著的終端機不會自動套用）。\n\n"
        "確定要切換嗎？",
        parent=win,
    )
    if not ok:
        return
    set_busy(f"切換到 {label} 中，請稍候…")

    def work():
        try:
            do_switch_to(target)
        except FileNotFoundError:
            _fcc_error_dialog()
        finally:
            root.after(0, lambda: (update_icon(), refresh()))

    threading.Thread(target=work, daemon=True).start()


def on_apply_model():
    model = model_var.get().strip()
    if not model:
        return
    ok = messagebox.askokcancel(
        "套用模型",
        f"將免費模型設定為：\n\n{model}\n\n"
        "會重新啟動 fcc-server（不影響目前的模式設定）。確定嗎？",
        parent=win,
    )
    if not ok:
        return
    set_busy("套用模型中，請稍候…")

    def work():
        try:
            set_model_in_env(model)
            restart_fcc_server()
        except FileNotFoundError:
            _fcc_error_dialog()
        finally:
            root.after(0, refresh)

    threading.Thread(target=work, daemon=True).start()


def on_open_admin():
    webbrowser.open(ADMIN_URL)


def build_window():
    global win, model_var
    if win and win.winfo_exists():
        win.deiconify()
        win.lift()
        win.focus_force()
        refresh()
        return

    win = tk.Toplevel(root)
    win.title("Claude 模式切換器")
    win.configure(bg="#1e1e1e")
    win.resizable(False, False)

    pad = {"padx": 14, "pady": 4}
    fg = "#dddddd"
    bg = "#1e1e1e"

    def row(label_text, key, value="—"):
        fr = tk.Frame(win, bg=bg)
        fr.pack(fill="x", **pad)
        tk.Label(fr, text=label_text, width=14, anchor="w", bg=bg, fg="#999999",
                 font=("Microsoft JhengHei", 10)).pack(side="left")
        lbl = tk.Label(fr, text=value, anchor="w", bg=bg, fg=fg,
                       font=("Microsoft JhengHei", 10, "bold"))
        lbl.pack(side="left")
        widgets[key] = lbl

    tk.Label(win, text="Claude 模式切換器", bg=bg, fg="#ffffff",
             font=("Microsoft JhengHei", 13, "bold")).pack(pady=(12, 2))
    tk.Label(win, text="作用對象：終端機的 claude 指令", bg=bg, fg="#777777",
             font=("Microsoft JhengHei", 8)).pack(pady=(0, 6))

    row("目前模式：", "mode")
    tk.Frame(win, bg="#333333", height=1).pack(fill="x", padx=14, pady=4)
    row("fcc-server：", "fcc")
    row("proxy 連線：", "proxy")
    row("免費模型：", "model")

    tk.Frame(win, bg="#333333", height=1).pack(fill="x", padx=14, pady=6)

    # 換模型
    mf = tk.Frame(win, bg=bg)
    mf.pack(fill="x", **pad)
    tk.Label(mf, text="切換模型：", width=14, anchor="w", bg=bg, fg="#999999",
             font=("Microsoft JhengHei", 10)).pack(side="left")
    model_var = tk.StringVar()
    box = ttk.Combobox(mf, textvariable=model_var, width=34)
    box.pack(side="left", fill="x", expand=True)
    widgets["model_box"] = box
    apply_model_btn = tk.Button(win, text="套用此模型", command=on_apply_model,
                                font=("Microsoft JhengHei", 9))
    apply_model_btn.pack(padx=14, pady=(2, 8), anchor="e")

    tk.Frame(win, bg="#333333", height=1).pack(fill="x", padx=14, pady=2)

    # 主要按鈕
    switch_btn = tk.Button(win, text="切換", command=on_switch,
                           font=("Microsoft JhengHei", 11, "bold"),
                           bg="#2d6cdf", fg="white", activebackground="#1f4fa8",
                           relief="flat", height=1)
    switch_btn.pack(fill="x", padx=14, pady=(8, 4))
    widgets["switch_btn"] = switch_btn

    admin_btn = tk.Button(win, text="開啟 Admin 設定頁", command=on_open_admin,
                          font=("Microsoft JhengHei", 9))
    admin_btn.pack(fill="x", padx=14, pady=2)

    close_btn = tk.Button(win, text="關閉視窗", command=win.withdraw,
                          font=("Microsoft JhengHei", 9))
    close_btn.pack(fill="x", padx=14, pady=(2, 8))

    status_line = tk.Label(win, text="讀取狀態中…", bg="#141414", fg="#888888",
                           anchor="w", font=("Microsoft JhengHei", 8))
    status_line.pack(fill="x", side="bottom")
    widgets["status_line"] = status_line

    widgets["buttons"] = [switch_btn, admin_btn, apply_model_btn, close_btn]

    win.protocol("WM_DELETE_WINDOW", win.withdraw)
    win.update_idletasks()
    refresh()


# ---------- 系統匣 ----------
def on_show(_icon=None, _item=None):
    root.after(0, build_window)


def on_quick_switch(_icon=None, _item=None):
    root.after(0, lambda: (build_window(), on_switch()))


def on_quit(_icon=None, _item=None):
    icon.stop()
    root.after(0, root.destroy)


def main():
    global icon, root
    root = tk.Tk()
    root.withdraw()

    menu = pystray.Menu(
        pystray.MenuItem("顯示狀態", on_show, default=True),
        pystray.MenuItem("快速切換", on_quick_switch),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("結束", on_quit),
    )
    m = current_mode()
    icon = pystray.Icon(
        "claude_switcher",
        make_image(GREEN if m == "free" else BLUE),
        "Claude：Free Claude Code" if m == "free" else "Claude：原版訂閱",
        menu,
    )
    threading.Thread(target=icon.run, daemon=True).start()
    root.after(500, build_window)  # 啟動時自動彈出狀態視窗
    root.mainloop()


if __name__ == "__main__":
    main()
