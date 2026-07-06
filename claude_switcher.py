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
import concurrent.futures
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
        # 下拉選單是否只顯示「測試過可用」的模型（此狀態會被記住，開機沿用）
        "show_only_available": False,
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


def save_config():
    """把目前的 _cfg 寫回 config.json（用於記住『只顯示可用模型』等狀態）。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


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
MESSAGES_API   = f"{PROXY_BASE_URL}/v1/messages"   # 驗證模型可用性時打這個端點
ADMIN_URL      = f"{PROXY_BASE_URL}/admin"

# 下拉選單預設可選的免費模型（可自行增減；下拉框也允許直接輸入完整模型名）
# 註：這些是「已實測你的帳號可用」的預設候選，避免一開就選到沒權限的模型。
FREE_MODELS = [
    "nvidia_nim/nvidia/nemotron-3-super-120b-a12b",   # 已實測可用
    "nvidia_nim/meta/llama-3.1-70b-instruct",         # 已實測可用
    "open_router/google/gemma-4-31b-it:free",         # OpenRouter 免費，已實測可用
    "open_router/openai/gpt-oss-120b:free",           # OpenRouter 免費
    "open_router/openai/gpt-oss-20b:free",            # OpenRouter 免費
]

# 「測試全部模型」的結果快取檔（存在腳本同目錄）。內容：
#   {"scanned_at": "2026-07-06 14:30", "results": {"<model>": "ok|busy|unavailable|error|unknown"}}
# 因為完整掃描 600+ 個模型很耗時，掃一次就存檔，之後開啟直接讀取；需要時再手動重掃。
AVAIL_CACHE_PATH = os.path.join(SCRIPT_DIR, "model_availability.json")

# 判定為「可用」的狀態（busy 只是暫時限流，模型本身可用）
USABLE_STATUS = ("ok", "busy")


def load_availability():
    """讀取模型可用性快取，回傳 {model: status}；讀不到就回空 dict。"""
    try:
        with open(AVAIL_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("results"), dict):
            return data["results"]
    except (OSError, ValueError):
        pass
    return {}


def save_availability(results, scanned_at):
    """把整份掃描結果寫入快取檔。"""
    try:
        with open(AVAIL_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"scanned_at": scanned_at, "results": results},
                      f, ensure_ascii=False, indent=2)
    except OSError:
        pass


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


# ---------- 模型可用性驗證（probe）----------
# 背景：fcc 的 /v1/models 會列出整個上游型錄（NVIDIA 200+、OpenRouter 400+），
# 但你的 API 金鑰通常只被授權其中一小部分。選到沒權限的模型時，上游會回
# HTTP 404「Not found for account」，而 fcc 會把這段錯誤「當成模型的回答文字」
# 用 200 OK 串流吐回來——所以表面上像成功、實際上模型只會胡言亂語，切換器
# 若不主動驗證就無從得知。以下函式送一個極短請求並判讀結果。
#
# 分類回傳 (status, note)：
#   "ok"          可用
#   "unavailable" 你的帳號沒有此模型權限（404 / Not found for account）
#   "busy"        暫時限流（429 / rate limit）——模型其實可用，稍後再試即可
#   "error"       其他上游錯誤
#   "unknown"     連不上或回應為空，無法判斷

def classify_probe(raw):
    """把 /v1/messages 的原始回應字串分類成 (status, note)。"""
    low = raw.lower()
    if "not found for account" in low or "returned http 404" in low:
        return ("unavailable", "你的帳號沒有此模型的使用權限")
    if "returned http 429" in low or "rate_limit" in low or "rate limit" in low:
        return ("busy", "此模型暫時忙碌（限流），稍後再試即可")
    if ("upstream provider" in low or "returned http 5" in low
            or '"category": "api_error"' in low or '"type": "error"' in low):
        return ("error", "上游回傳錯誤，換一個模型較保險")
    # 有正式文字、思考(thinking)內容、或工具呼叫，都代表模型活著且有授權。
    # 思考型模型可能在極短 max_tokens 下只吐 thinking 還沒吐正式文字，仍算可用。
    if ('"text_delta"' in raw or '"type": "text"' in raw
            or "thinking" in low or "tool_use" in low):
        return ("ok", "可用")
    return ("unknown", "無法判斷（回應為空或連不上）")


def _probe_request(model_field, timeout):
    """對指定 model 欄位送一個極短請求，回傳 (status, note)。"""
    body = json.dumps({
        "model": model_field,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode("utf-8")
    req = urllib.request.Request(
        MESSAGES_API, data=body, method="POST",
        headers={
            "x-api-key": AUTH_TOKEN,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception:
        return ("unknown", "無法判斷（回應為空或連不上）")
    return classify_probe(raw)


def probe_current(timeout=60):
    """驗證『目前 .env 設定的模型』。用 claude 模型名，走的路徑和 Claude Code 一致。"""
    return _probe_request("claude-sonnet-4", timeout)


def probe_candidate(display_name, timeout=60):
    """驗證某個候選模型（不改 .env）。fcc 的路由 id = 'anthropic/' + display_name。"""
    return _probe_request("anthropic/" + display_name, timeout)


# 「定案」狀態：續跑時不再重測；其餘（busy=暫時限流、unknown=沒回應）下次會補測。
DEFINITIVE = ("ok", "unavailable", "error")


def build_scan_list(scope="all"):
    """依範圍組出要掃描的模型清單。
    - "all"  ：整個型錄（NVIDIA + OpenRouter 全部）
    - "free" ：NVIDIA 全部 + OpenRouter 免費（:free）——省額度、且是最適合免費用的
    """
    full = list_models()
    if scope == "free":
        return [m for m in full
                if m.startswith("nvidia_nim/")
                or (m.startswith("open_router/") and ":free" in m.lower())]
    return full


def scan_all_models(models=None, progress_cb=None, stop_event=None, timeout=30,
                    resume=True, save_every=15):
    """探測模型可用性，結果邊測邊寫進全域 _availability 與快取檔（可續跑）。

    - models    ：要測的清單；None = 依 build_scan_list("all")。
    - resume    ：True 時跳過已「定案」(ok/unavailable/error)的，只補未測與可重試(busy/unknown)。
    - save_every：每測這麼多個就存檔一次，中途停掉也不白費。
    - stop_event：外部可 set() 來中斷；尚未開始的會被略過。
    回傳 (整體結果 dict, 這批清單總數 total, 這次實際測了幾個 tested)。
    因 fcc 對每個 provider 有速率限制，client 端併發設低即可（設太高只會被 429）。
    """
    if models is None:
        models = build_scan_list("all")
    total = len(models)
    ts = time.strftime("%Y-%m-%d %H:%M")
    if resume:
        # 續跑：只補「還沒測過」與「暫時限流(busy/429)」的。
        # unknown（30 秒沒回應）視為已測、不再自動重試，確保掃描會收斂、不會鬼打牆。
        todo = [m for m in models
                if _availability.get(m) is None or _availability.get(m) == "busy"]
    else:
        todo = list(models)
    lock = threading.Lock()
    counter = {"done": 0, "since_save": 0}

    def one(m):
        if stop_event is not None and stop_event.is_set():
            return
        status, _ = probe_candidate(m, timeout=timeout)
        with lock:
            _availability[m] = status
            counter["done"] += 1
            counter["since_save"] += 1
            done = counter["done"]
            if counter["since_save"] >= save_every:
                counter["since_save"] = 0
                save_availability(dict(_availability), ts)
        if progress_cb:
            progress_cb(done, len(todo), m, status)

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    try:
        futs = [ex.submit(one, m) for m in todo]
        for _ in concurrent.futures.as_completed(futs):
            if stop_event is not None and stop_event.is_set():
                break
    finally:
        # 中斷時取消還沒開始的工作，不等待仍在跑的
        ex.shutdown(wait=False, cancel_futures=True)
    save_availability(dict(_availability), ts)   # 收尾存檔
    return dict(_availability), total, counter["done"]


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
_last_probe = {}  # {model_name: (status, note)}，記住最近一次驗證結果供狀態視窗顯示
_availability = load_availability()          # {model: status}，開機時從快取檔載入
_show_only = bool(_cfg.get("show_only_available", False))  # 下拉是否只顯示可用模型（記憶狀態）
_scan_stop = threading.Event()               # set() 可中斷「測試全部模型」
_scanning = False                            # 是否正在掃描中


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


# 付費模型標註：OpenRouter 非 :free 的都算付費（NVIDIA 走免費層，不標）。
# 標註只用於「顯示」，套用前一律用 strip_annotation() 去掉，避免寫進 .env。
PAID_SUFFIX = "　(付費模型)"


def is_paid(model):
    m = model.lower()
    return m.startswith("open_router/") and ":free" not in m


def annotate_model(model):
    """顯示用：付費模型在名稱後加「(付費模型)」。"""
    return model + PAID_SUFFIX if is_paid(model) else model


def strip_annotation(text):
    """把顯示用的標註去掉，還原成真正的模型名。"""
    return text[:-len(PAID_SUFFIX)] if text.endswith(PAID_SUFFIX) else text


def dropdown_values(full_models):
    """依『只顯示可用模型』狀態決定下拉要列哪些，並替付費模型加上標註（顯示用）。

    開啟時：只留掃描快取判定為可用（ok/busy）的；但一定保留目前設定的模型，
    避免看不到自己現在用的是哪個。若還沒有任何快取，就退回顯示全部。
    """
    if _show_only and _availability:
        raw = [m for m in full_models if _availability.get(m) in USABLE_STATUS]
        cur = strip_annotation(model_var.get().strip()) if model_var else ""
        if cur and cur not in raw:
            raw = [cur] + raw
        raw = raw or full_models
    else:
        raw = full_models
    return [annotate_model(m) for m in raw]


def update_toggle_btn():
    """更新切換鈕的文字，反映目前是『只顯示可用』還是『顯示全部』。"""
    btn = widgets.get("toggle_btn")
    if btn:
        btn.config(text="顯示全部模型" if _show_only else "只顯示可用模型")


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
            # 若最近驗證過這個模型，帶上可用性標記；沒驗證過就標「(未驗證)」
            mark_map = {
                "ok": ("　✅ 可用", "#2ecc71"),
                "busy": ("　⏳ 暫時忙碌", "#f1c40f"),
                "unavailable": ("　⛔ 帳號無權限", "#e74c3c"),
                "error": ("　⛔ 上游錯誤", "#e74c3c"),
                "unknown": ("　❔ 驗證失敗", "#e67e22"),
            }
            default_fg = "#dddddd"
            # 先看本次套用的即時驗證結果，再退回全量掃描的快取
            status = None
            if model in _last_probe:
                status = _last_probe[model][0]
            elif model in _availability:
                status = _availability[model]
            if status and model not in ("(未設定)", "(讀不到)"):
                suffix, color = mark_map.get(status, ("", default_fg))
                widgets["model"].config(text=model + suffix, fg=color)
            else:
                mark = "　(未驗證)" if model not in ("(未設定)", "(讀不到)") else ""
                widgets["model"].config(text=model + mark, fg=default_fg)
            widgets["switch_btn"].config(
                text=("切換到原版訂閱" if is_free else "切換到 Free Claude Code")
            )
            widgets["model_box"]["values"] = dropdown_values(models)
            if model and model not in ("(未設定)", "(讀不到)"):
                model_var.set(annotate_model(model))
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
    model = strip_annotation(model_var.get().strip())   # 去掉「(付費模型)」標註再套用
    if not model:
        return
    ok = messagebox.askokcancel(
        "套用模型",
        f"將免費模型設定為：\n\n{model}\n\n"
        "會重新啟動 fcc-server，並自動驗證這個模型你的帳號能不能用"
        "（不影響目前的模式設定）。確定嗎？",
        parent=win,
    )
    if not ok:
        return
    set_busy("套用模型並驗證中，請稍候…")

    def work():
        try:
            set_model_in_env(model)
            restart_fcc_server()
            status, note = probe_current()
            _last_probe[model] = (status, note)
            root.after(0, lambda: _show_probe_result(model, status, note))
        except FileNotFoundError:
            _fcc_error_dialog()
        finally:
            root.after(0, refresh)

    threading.Thread(target=work, daemon=True).start()


def _show_probe_result(model, status, note):
    """套用模型後，依驗證結果跳對應視窗（只提醒，不自動改設定）。"""
    if status == "ok":
        messagebox.showinfo(
            "模型可用 ✅",
            f"已套用並驗證成功：\n\n{model}\n\n"
            "記得『開一個新的終端機視窗』再執行 claude 才會生效。",
            parent=win if (win and win.winfo_exists()) else None,
        )
    elif status == "busy":
        messagebox.showwarning(
            "模型暫時忙碌 ⏳",
            f"{model}\n\n{note}\n\n"
            "設定已套用；此模型其實可用，只是上游暫時限流。\n"
            "可稍後再試，或先換另一個模型。",
            parent=win if (win and win.winfo_exists()) else None,
        )
    else:  # unavailable / error / unknown
        messagebox.showwarning(
            "模型可能無法使用 ⛔",
            f"{model}\n\n{note}\n\n"
            "設定已套用，但驗證未通過——用這個模型時 claude 可能只會回錯誤訊息。\n"
            "建議按『測試全部模型』找出能用的，再挑一個換上。",
            parent=win if (win and win.winfo_exists()) else None,
        )


def on_toggle_available():
    """切換下拉選單『只顯示可用模型』⇄『顯示全部模型』，狀態會被記住。"""
    global _show_only
    turning_on = not _show_only
    if turning_on and not any(s in USABLE_STATUS for s in _availability.values()):
        # 還沒有掃描結果，無從篩選——問要不要現在掃
        go = messagebox.askokcancel(
            "尚未測試模型",
            "還沒有可用模型的測試結果，無法篩選。\n\n"
            "要現在開始『測試全部模型』嗎？（約 20～40 分鐘，可中途停止）",
            parent=win,
        )
        if go:
            on_scan_all()
        return
    _show_only = turning_on
    _cfg["show_only_available"] = _show_only
    save_config()                      # 記住狀態，下次開啟沿用
    update_toggle_btn()
    refresh()


def on_scan_all():
    """測試全部模型（NVIDIA + OpenRouter），結果存快取檔。再按一次可中途停止。"""
    global _scanning
    if _scanning:                      # 掃描中再按 → 停止
        _scan_stop.set()
        widgets["scan_btn"].config(text="停止中…", state="disabled")
        return

    ok = messagebox.askokcancel(
        "測試全部模型",
        "即將測試全部模型（NVIDIA + OpenRouter，約 600 多個）。\n\n"
        "• 邊測邊存、可續跑：已測過的不會重來，中途停掉也不白費\n"
        "• 過程中可再按同一顆鈕『停止測試』中斷，下次接著測\n"
        "• 首次測完約 20～40 分鐘，之後開啟直接讀快取\n\n"
        "確定現在開始（或繼續）嗎？",
        parent=win,
    )
    if not ok:
        return

    _scanning = True
    _scan_stop.clear()
    # 掃描期間停用其他鈕，只留掃描鈕可按（用來停止）
    for b in widgets.get("buttons", []):
        if b is not widgets.get("scan_btn"):
            b.config(state="disabled")
    widgets["scan_btn"].config(text="停止測試", state="normal")

    def prog(done, total, _m, _st):
        root.after(0, lambda: widgets["status_line"].config(
            text=f"測試中 {done}/{total}…（可按『停止測試』中斷）"))

    def work():
        global _scanning
        # resume=True：跳過已定案的，只補未測與可重試的；邊測邊存
        results, total, did = scan_all_models(
            progress_cb=prog, stop_event=_scan_stop, resume=True)

        def done_ui():
            global _scanning
            _scanning = False
            widgets["scan_btn"].config(text="測試全部模型", state="normal")
            usable = sum(1 for s in results.values() if s in USABLE_STATUS)
            tested = sum(1 for s in results.values() if s)
            busy_n = sum(1 for s in results.values() if s == "busy")
            remaining = max(0, total - tested)
            word = "已停止" if _scan_stop.is_set() else "完成"
            widgets["status_line"].config(
                text=f"測試{word}：本次補測 {did}，累計已測 {tested}/{total}，可用 {usable} 個")
            refresh()
            parts = [f"本次補測 {did} 個；累計已測 {tested}/{total}，其中 {usable} 個你的帳號可用。"]
            if remaining > 0:
                parts.append(f"還有 {remaining} 個未測，再按一次『測試全部模型』可續測。")
            elif busy_n > 0:
                parts.append(f"全部已測；其中 {busy_n} 個暫時限流(429)，稍後再按一次可補測。")
            else:
                parts.append("全部已測完 🎉")
            parts.append("可按『只顯示可用模型』只保留能用的。")
            messagebox.showinfo(f"測試{word}", "\n\n".join(parts),
                                parent=win if (win and win.winfo_exists()) else None)

        root.after(0, done_ui)

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

    btn_row = tk.Frame(win, bg=bg)
    btn_row.pack(fill="x", padx=14, pady=(2, 8))
    toggle_btn = tk.Button(btn_row, command=on_toggle_available,
                           font=("Microsoft JhengHei", 9))
    toggle_btn.pack(side="left")
    widgets["toggle_btn"] = toggle_btn
    update_toggle_btn()               # 依記憶的狀態設定文字
    scan_btn = tk.Button(btn_row, text="測試全部模型", command=on_scan_all,
                         font=("Microsoft JhengHei", 9))
    scan_btn.pack(side="left", padx=(6, 0))
    widgets["scan_btn"] = scan_btn
    apply_model_btn = tk.Button(btn_row, text="套用此模型", command=on_apply_model,
                                font=("Microsoft JhengHei", 9))
    apply_model_btn.pack(side="right")

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

    widgets["buttons"] = [switch_btn, admin_btn, toggle_btn, scan_btn,
                          apply_model_btn, close_btn]

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
