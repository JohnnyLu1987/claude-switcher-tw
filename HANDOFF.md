# Claude Switcher - 交接文件

> 專案狀態快照：2026-06-07
> 供後續開發者（或未來的自己）快速上下文

---

## 專案定位

**Claude 模式切換器** — 讓終端機的 `claude` 指令在「原版訂閱」與「Free Claude Code」之間一鍵切換，並可更換免費模型。

- **作用對象**：PowerShell / cmd 終端機的 `claude` 指令
- **不影響**：Claude Desktop 內建的 Cowork（強制走官方 API）
- **核心原理**：改寫 User 層級環境變數 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`（寫入 `HKCU\Environment`），並廣播 `WM_SETTINGCHANGE` 通知系統

---

## 目前已完成功能

| 功能 | 狀態 | 備註 |
|------|------|------|
| 系統匣圖示（藍=官方、綠=Free） | ✅ | `pystray` + PIL 繪圖 |
| 左鍵點圖示 → 狀態視窗 | ✅ | Tkinter，顯示模式/fcc-server/proxy/模型 |
| 右鍵選單：快速切換 / 結束 | ✅ | |
| 切換前二次確認彈窗 | ✅ | 含「需開新終端機」提醒 |
| 下拉選單換免費模型（可手輸完整名） | ✅ | 讀 `.fcc/.env` + 呼叫 `/v1/models` API |
| 套用模型 → 重啟 fcc-server | ✅ | 不影響目前模式設定 |
| 開啟 Admin 設定頁 | ✅ | `http://127.0.0.1:8082/admin` |
| 自動啟動 fcc-server（隱藏視窗） | ✅ | 切到 Free 時若未啟動自動拉起 |
| 開機自啟說明 | ✅ | README 有捷徑放法 |
| 修復記錄文件化 | ✅ | `修復記錄-2026-06-05.md` |

---

## 關鍵路徑與設定（目前寫死在程式碼頂部）

```python
FCC_SERVER_EXE = r"C:\Users\JohnnyLu\.local\bin\fcc-server.exe"
FCC_ENV_PATH   = r"C:\Users\JohnnyLu\.fcc\.env"
TIKTOKEN_CACHE_DIR = r"C:\Users\JohnnyLu\.fcc\tiktoken-cache"

PROXY_BASE_URL = "http://127.0.0.1:8082"
AUTH_TOKEN     = "freecc"
HEALTH_URL     = "http://127.0.0.1:8082/health"
MODELS_API     = "http://127.0.0.1:8082/v1/models"
ADMIN_URL      = "http://127.0.0.1:8082/admin"

FREE_MODELS = ["nvidia_nim/nvidia/nemotron-3-super-120b-a12b"]
```

> ✅ **已完成（2026-06-07）**：路徑改為自動偵測 + 外部 `config.json`。
> 上方常數現由 `load_config()` 從腳本同目錄的 `config.json` 載入；找不到則自動偵測本機路徑並寫出一份。
> `port` / `auth_token` 也在 config 內，URL 由 `port` 推導。

---

## fcc-server 關鍵修復紀錄（2026-06-05 完成）

詳見 `修復記錄-2026-06-05.md`，摘要：

| # | 問題 | 修復 |
|---|------|------|
| 1 | Python 3.14.0 OpenSSL applink 崩潰 | 改用 **Python 3.14.5** 重裝 fcc |
| 2 | 壞掉的 `SSL_CERT_FILE` 環境變數 | 移除 User 層級該變數 |
| 3 | tiktoken 啟動下載被 TLS 攔截 | 設 `TIKTOKEN_CACHE_DIR` 指向永久快取 |
| 4 | TLS 攔截導致 NVIDIA 連不上 | fcc venv 裝 **truststore** + `.pth` 自動注入 `truststore.inject_into_ssl()` |

> **注意**：第 4 點是**在 fcc 的 venv 裡操作**，切換器本體無法單獨完成。發布時需提供 `fix-fcc-tls.bat` 或文件引導使用者自行執行。

---

## 專案檔案結構

```
claude-switcher/
├── claude_switcher.py        # 核心程式（單檔 ~540 行）
├── config.json               # 設定檔（首次執行自動產生，可手動調整路徑/port/token）
├── install.bat               # 一鍵安裝：偵測 Python/fcc + 產生 config.json + 開機自啟捷徑
├── 2-啟動切換器.vbs          # 背景啟動 pythonw.exe claude_switcher.py
├── fix-fcc-tls.bat           # 在 fcc venv 裝 truststore + 寫 .pth 注入（解 TLS 攔截）
├── 啟動fcc-server.bat        # 手動啟動 fcc-server（備援）
├── 修復記錄-2026-06-05.md    # fcc 疑難雜症完整紀錄
├── README.md                 # 使用說明
├── HANDOFF.md                # 本檔
├── .claude/
│   └── settings.local.json   # Claude Code 權限允許清單
└── __pycache__/
```

---

## 依賴套件

| 套件 | 用途 | 安裝指令 |
|------|------|----------|
| `pystray` | 系統匣圖示 | `pip install pystray` |
| `pillow` | 圖示繪製 | `pip install pillow` |
| `psutil` | 找 port 8082 程序、殺進程 | `pip install psutil` |
| 標準庫 | `winreg`, `ctypes`, `tkinter`, `threading`, `urllib`, `subprocess`, `json`, `os`, `time`, `webbrowser` | 內建 |

---

## 目前已知限制 / 注意事項

1. **切換後必須開新終端機** — 環境變數廣播只影響後續啟動的進程
2. **硬編碼路徑** — 僅適用 Johnny 自己的電腦
3. **Python 版本** — 切換器用 3.12 跑，fcc 需 3.14+（uv tool 會自動管理）
4. **truststore 注入** — 屬 fcc 端修復，切換器只能設 `TIKTOKEN_CACHE_DIR`、移除 `SSL_CERT_FILE`
5. **NVIDIA 模型首發驗證曾失敗** — 建議使用者先在 Admin 頁確認 API Key、或換模型
6. **無自動更新 / 版本號機制**

---

## 發布到 GitHub 給 fcc 使用者前的必要補充

| 優先度 | 項目 | 說明 |
|--------|------|------|
| ✅ 完成 | **路徑自動偵測 + `config.json`** | 已於 2026-06-07 完成（`load_config()` / `auto_detect_config()`） |
| ✅ 完成 | **`install.bat`** | 已於 2026-06-07 完成（偵測 Python/fcc、產生 config.json、建立 shell:startup 捷徑） |
| ✅ 完成 | **`fix-fcc-tls.bat`** | 已於 2026-06-07 完成（定位 uv tool venv → `uv pip install truststore` → 寫 `.pth` → 驗證） |
| ✅ 完成 | **完善 README** | 已於 2026-06-07 完成（前置需求、install.bat、fix-fcc-tls、config.json、FAQ、解除安裝） |
| 🟡 可選 | **打包單一 `.exe` (PyInstaller)** | 免裝 Python 依賴，雙擊即用 |
| 🟡 可選 | **解除安裝腳本 `uninstall.bat`** | 清環境變數、移捷徑、刪設定檔 |
| 🟢 之後 | **更新檢查 / GitHub Releases 整合** | |

### 建議的自動偵測邏輯

```python
def auto_detect_paths():
    import os, subprocess, winreg
    user = os.environ["USERPROFILE"]
    
    # 1. fcc-server.exe：優先 uv tool list，回退 ~/.local/bin
    # 2. .fcc/.env：~\.fcc\.env
    # 3. tiktoken-cache：~\.fcc\tiktoken-cache
    # 4. Python：用 `py -3.12 -m` 或 `where pythonw`
```

---

## 給下一位開發者的建議

1. **先跑一次 `install.bat` 完成安裝**（裝套件 + 產生 config.json + 開機捷徑），再 `python claude_switcher.py` 手動測試
2. **所有路徑改成讀 `config.json`**，找不到才用預設值、最後才報錯
3. **保持單檔架構**（好打包 exe），但把設定區提到最頂端、加詳盡註解
4. **Karpathy Guidelines 四原則**：
   - Think Before Coding —— 不確定先問/列假設
   - Simplicity First —— 最少必要程式碼
   - Surgical Changes —— 只動必要行、風格跟隨既有
   - Goal-Driven Execution —— 多步驟先列計畫、定驗收
5. **遇到 fcc 連線問題** → 先看 `修復記錄-2026-06-05.md`，通常是 truststore 或 TLS 攔截
6. **`.bat` 一律用 CRLF 換行**（LF 會讓 cmd 把指令攔腰切斷而報一堆莫名錯誤）
7. **`.vbs` 一律用 UTF-16 LE + BOM + CRLF**（Windows Script Host 對 UTF-8 BOM 會報「無效字元」、無 BOM 又會用 CP950 誤判中文註解；存錯會出現「必須要有物件 sh」而無法啟動。注意：一般文字編輯器/工具預設常存成 UTF-8，改完務必確認編碼）
8. **fcc 是 `uv tool` 安裝**：venv 在 `uv tool dir`\\`free-claude-code`，**沒有 pip 模組**，要裝套件用 `uv pip install --python <venv>\\Scripts\\python.exe <pkg>`，不能用 `python -m pip`

---

## 快速驗證清單（改完 code 後）

- [ ] `python claude_switcher.py` 無報錯啟動，右下角出現圖示
- [ ] 點圖示 → 狀態視窗正常顯示目前模式、fcc-server、proxy、模型
- [ ] 切換 → 彈確認視窗 → 確定 → 新終端機 `echo %ANTHROPIC_BASE_URL%` 驗證
- [ ] 換模型 → 重啟 fcc-server → Admin 頁或 API 確認生效
- [ ] 關閉視窗 → 圖示仍在、右鍵選單可用
- [ ] `install.bat` 可在乾淨環境一鍵部署（含啟動捷徑）

---

## 相關連結

- Free Claude Code 專案：https://github.com/Alishahryar1/free-claude-code
- fcc-server 預設 port：8082
- 環境變數鍵：`ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`
- 註冊表位置：`HKCU\Environment`