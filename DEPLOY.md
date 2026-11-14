# 部署

有兩種方式：

| | 自己架設（Docker） | Streamlit Community Cloud |
|---|---|---|
| App 跑在哪裡 | 你自己的電腦或主機 | Streamlit 的雲端 |
| 會不會休眠 | 不會，電腦開著就一直在 | 閒置約 12 小時會休眠，下一位訪客要按按鈕喚醒並等待 |
| 需要什麼 | 一台一直開著的電腦、Docker | GitHub 帳號 |
| 給外面的人用 | 用 Cloudflare Tunnel 取得公開網址 | 內建 `*.streamlit.app` 網址 |

- [方式一：自己架設（Docker）](#方式一自己架設docker)
- [方式二：Streamlit Community Cloud](#方式二streamlit-community-cloud)

---

## 方式一：自己架設（Docker）

Docker 會把 Python、所有套件和 App 包成一個「容器」，所以電腦上不必另外安裝 Python。
以下以 Windows 為例；macOS、Linux 裝好 Docker 之後，指令完全相同。

### 需要準備什麼

- 一台可以一直開著的電腦，記憶體 **8 GB 以上**（16 GB 以上更好，原因見[記憶體設定](#記憶體設定)）。
- Windows 10（22H2）或 Windows 11（23H2 以上）、64 位元。Docker 官方文件列出的是
  Pro、Enterprise、Education 版；家用版（Home）文件上寫的是「只能執行 Linux 容器」，
  而這個 App 正是 Linux 容器。
- [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)。個人使用、教育用途免費。

### 第一次安裝

1. **安裝 Docker Desktop**：從上面的連結下載安裝檔並執行。安裝畫面的
   「Use WSL 2 instead of Hyper-V」保持勾選。裝完後從開始功能表開啟 Docker Desktop
   （安裝完不會自己啟動），等視窗左下角顯示 Engine running。
2. **取得程式碼**，兩種方法擇一：
   - 最簡單：在 GitHub 專案頁面按綠色的 **Code → Download ZIP**，解壓縮到你找得到的地方。
   - 之後更新比較方便：安裝 [Git](https://git-scm.com/download/win) 後執行
     `git clone https://github.com/<帳號>/Youbike-free.git`。
3. **在專案資料夾開啟終端機**：用檔案總管進到專案資料夾（看得到 `docker-compose.yml` 的那一層），
   在上方的網址列輸入 `powershell` 後按 Enter。
4. **啟動**：

   ```powershell
   docker compose up -d --build
   ```

   第一次要下載 Python 與套件，視網路速度大約 3 到 10 分鐘。之後再啟動只要幾秒。
5. **打開 App**：用瀏覽器開 <http://localhost:8501>。第一次開啟要抓站點資料並建站點圖，會轉圈 10 到 30 秒。

不需要任何金鑰就能使用。金鑰是選用功能，見[金鑰與其他設定](#金鑰與其他設定)。

### 讓它一直開著

App 本身已經設定好：程式當掉、記憶體超過上限，或 Docker 重新啟動時，都會自動再跑起來。
你只需要處理電腦這一邊：

1. **讓 Docker 開機後自動啟動**：Docker Desktop → Settings → General，勾選
   「Start Docker Desktop when you sign in to your computer」（預設沒有勾）。
2. **不要讓電腦睡眠**：Windows 設定 → 系統 → 電源（筆電是「電源與電池」，Windows 10 是「電源與睡眠」），
   把插電時進入睡眠的時間設為「永不」。螢幕可以照常關閉，不影響 App。
3. **重新開機之後要登入 Windows 一次**。Docker Desktop 是登入後才啟動的，所以
   Windows 更新自動重開機之後，App 會停在那裡，直到有人登入。

### 給外面的人用：公開網址

預設只有這台電腦自己連得到 App。要讓其他人從外面的網路使用，用
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)：
由這台電腦主動連到 Cloudflare，訪客再經由 Cloudflare 連進來。好處是不必設定路由器，
也不會讓訪客看到你家的 IP。

> 這一節的設定是依 Cloudflare 官方文件寫的，撰寫時的開發環境連不到 Cloudflare，
> **還沒有實際連線測試過**。第一次使用若遇到問題，請回報或直接修正這份文件。

#### 公開之前先知道

- **拿到網址的人都能用**，App 沒有登入機制。
- **有設定金鑰的話，訪客的每次查詢都會用到你的額度**。公開時建議不要設定
  `GOOGLE_MAPS_API_KEY` 與 `GEMINI_API_KEY`，或先到 Google Cloud 設好每日用量上限。
- **電腦關機、睡眠或斷網，網址就連不上**。
- 這是一個新的網址，和原本的 `*.streamlit.app` 網址無關。

#### 作法 A：臨時網址（不用註冊，適合先試試看）

1. 在專案資料夾建立 `.env` 檔（已經有就直接編輯），加入一行：

   ```ini
   COMPOSE_PROFILES=public
   ```

2. 套用設定並找出網址：

   ```powershell
   docker compose up -d
   docker compose logs tunnel | findstr trycloudflare
   ```

   會看到一個 `https://隨機字串.trycloudflare.com` 的網址，把它給其他人即可。
   （macOS、Linux 請把 `findstr` 換成 `grep`。）

限制（Cloudflare 的說明）：這種網址是給測試用的，**每次重新啟動都會換一個**（包含電腦重開機），
不保證可用時間，同時處理中的連線請求最多 200 個。要長期給人用，請用作法 B。

#### 作法 B：固定網址（需要 Cloudflare 帳號與一個網域）

需要先有一個已加入 Cloudflare 的網域（網域要另外購買，Cloudflare 帳號本身免費）。

1. 登入 [Cloudflare 控制台](https://dash.cloudflare.com/)，進入 **Networking → Tunnels**，
   按 **Create a tunnel**，取一個名字後建立。
2. 畫面會顯示一段安裝指令，裡面有一長串以 `eyJ` 開頭的金鑰（token）。**只需要複製那串金鑰**，
   不必照畫面在電腦上安裝任何東西。
3. 在 `.env` 加入這兩行，然後執行 `docker compose up -d`：

   ```ini
   COMPOSE_PROFILES=public-fixed
   TUNNEL_TOKEN=貼上第 2 步複製的金鑰
   ```

   幾秒後 Cloudflare 的畫面應該會顯示這個 tunnel 已連線。
4. 回到 Cloudflare 的這個 tunnel，開啟 **Routes** 分頁 → **Add route** → **Published application**。
   填上想要的子網域並選擇你的網域；**Service URL 填 `http://app:8501`**
   （`app` 是 App 在 Docker 內部的名字，不要填 localhost）。
5. 用第 4 步設定的網址就能連進來。

`TUNNEL_TOKEN` 等同這條通道的鑰匙，不要貼到公開的地方；`.env` 已列在 `.gitignore`，不會被提交。

從作法 A 換到作法 B（或要關掉公開網址）時，**先執行 `docker compose down`**，
改好 `.env` 之後再 `docker compose up -d`，舊的通道才會確實停掉。

### 記憶體設定

這個 App 最吃記憶體的是「站點圖」：側欄每一種設定組合（區域、騎乘速度、緩衝分鐘數等）
都要各建一張，騎得越快圖越大。以模擬的北北桃站點（4,340 站）實測：

| 側欄設定 | 一張站點圖 |
|---|---|
| 悠閒 10 km/h | 約 0.4 GB |
| 一般 12 km/h（預設） | 約 0.6 GB |
| 輕快 15 km/h | 約 0.9 GB |
| 運動 18 km/h | 約 1.2 GB |
| 運動 18 km/h，加上「避開同站冷卻」 | 約 1.7 GB |

App 本身另外約佔 0.25 GB。真實站點的分布和模擬資料不同，數字會有出入。

為了不讓記憶體用量失控，App 有三道保護，都可以在 `.env` 調整：

| 設定 | 預設 | 作用 |
|---|---|---|
| `APP_MEM_LIMIT` | `3g` | 容器的記憶體上限。超過時 Docker 會強制重啟 App，避免拖垮整台電腦 |
| `GRAPH_BUILD_HEADROOM_MB` | `2048` | 建新圖之前，離上限若不到這麼多 MB，就先把舊圖清掉再建。`0` 表示停用 |
| `GRAPH_CACHE_MAX_ENTRIES` | `2` | 最多同時保留幾張圖 |
| `GRAPH_CACHE_TTL_SECONDS` | `600` | 一張圖最多用多久（秒）。到期後重建，才會用到最新的車況 |

建議：

| 電腦記憶體 | 建議 |
|---|---|
| 8 GB | 用預設值。記憶體只夠同時留一到兩張圖，有人換了設定常常要重建，等 10 到 20 秒 |
| 16 GB 以上 | 設 `APP_MEM_LIMIT=6g`、`GRAPH_CACHE_MAX_ENTRIES=4`，常用的幾種設定可以同時留著 |

在 Windows 上，Docker 預設最多只能用到電腦記憶體的一半（這是 WSL 2 的預設值），
所以 8 GB 的電腦給 App 3 GB 已經是上限。

改完 `.env` 後執行 `docker compose up -d` 就會套用，不必重新建置。

### 金鑰與其他設定

所有設定都放在專案資料夾的 `.env` 檔，可以從範本複製後修改：

```powershell
copy .env.example .env
notepad .env
```

- `GOOGLE_MAPS_API_KEY`、`GEMINI_API_KEY`：選用。沒有金鑰的話把那一行刪掉或保留範本的
  `YOUR_KEY` 都可以，App 會當成沒有設定：時間以直線估算、AI 建議改用本地文字摘要。
- `APP_PORT`：預設 `8501`。這個連接埠被別的程式佔用時可以換一個。
- `APP_BIND`：預設 `127.0.0.1`，只有這台電腦自己連得到。設成 `0.0.0.0` 之後，
  同一個網路（同一台 Wi-Fi 分享器）下的手機和電腦可以用 `http://這台電腦的IP:8501` 連線；
  若連不上，檢查 Windows 防火牆是否允許 Docker Desktop。

### 常用指令

都在專案資料夾裡執行：

| 想做的事 | 指令 |
|---|---|
| 看 App 是否正常（應顯示 `healthy`） | `docker compose ps` |
| 看記錄（按 Ctrl+C 離開） | `docker compose logs -f app` |
| 暫停 / 繼續 | `docker compose stop` / `docker compose start` |
| 完全移除容器 | `docker compose down` |
| 改了 `.env` 之後套用 | `docker compose up -d` |

### 更新到新版

```powershell
git pull
docker compose pull --ignore-buildable
docker compose up -d --build
```

- 用 ZIP 下載的話，把新版解壓縮覆蓋舊資料夾（`.env` 會保留），再執行後兩行。
- 第二行是更新 Cloudflare Tunnel 的程式；沒有啟用公開網址時可以省略。
- 想改成跟著另一個 GitHub 帳號的版本更新（例如原作者的 repo），先執行一次
  `git remote set-url origin https://github.com/<帳號>/Youbike-free.git`，之後照常 `git pull`。

### 遇到問題時

| 狀況 | 怎麼辦 |
|---|---|
| 瀏覽器連不上 `localhost:8501` | 執行 `docker compose ps`。沒有列出 `youbike-free` 表示沒啟動，重新執行 `docker compose up -d`；狀態不是 `healthy` 就用 `docker compose logs app` 看錯誤訊息 |
| 出現 `port is already allocated` | 8501 被別的程式佔用，在 `.env` 設 `APP_PORT=8600`（或其他數字）後再啟動 |
| App 常常自己重新整理、`docker compose ps` 顯示剛啟動沒多久 | 多半是記憶體超過上限被重啟。照[記憶體設定](#記憶體設定)調高 `APP_MEM_LIMIT`，或維持 `GRAPH_BUILD_HEADROOM_MB` 的預設值不要關掉 |
| 記錄出現「可用記憶體少於…先清除快取中的舊圖」 | 這是保護機制正常運作，不是錯誤。出現得很頻繁，表示記憶體偏緊，可以照上面調整 |
| 畫面顯示站點資料載入失敗 | 政府或 YouBike 的開放資料暫時連不上，稍後按側欄的「重抓即時車況」再試 |
| 執行 `docker` 出現找不到指令或無法連線 | Docker Desktop 還沒開，或還在啟動中 |

---

## 方式二：Streamlit Community Cloud

### 前置

- 專案已推到 GitHub（public 或將 Streamlit 帳號授權存取 private repo）
- 主程式：`app.py`
- 相依：`requirements.txt`（已含 streamlit、folium、pandas、numpy…）
- 設定：`.streamlit/config.toml`（主題與伺服器設定，會自動套用）

本專案不需系統套件，因此**沒有** `packages.txt`；相依皆為純 Python wheel。

### 步驟

1. 到 <https://share.streamlit.io> 用 GitHub 登入。
2. **Create app → Deploy a public app from a repo**。
3. 填寫：
   - Repository：`<你的帳號>/Youbike-free`
   - Branch：`main`（或你要部署的分支）
   - Main file path：`app.py`
   - Python version：3.10 以上（建議 3.11）
4. 展開 **Advanced settings → Secrets**，貼上金鑰（TOML 格式）：

   ```toml
   GOOGLE_MAPS_API_KEY = "你的金鑰"
   GEMINI_API_KEY = "你的金鑰"
   GEMINI_MODEL = "gemini-2.5-flash"
   ```

   > 不填也能部署：App 仍可規劃路線，只是時間用直線估算、AI 建議改本地摘要。

5. **Deploy**。首次安裝相依約 1~3 分鐘。

### 注意事項

- 閒置約 12 小時後 App 會休眠，下一位訪客要按「Yes, get this app back up!」並等它重新啟動。
  不想休眠請改用[方式一](#方式一自己架設docker)。
- 新北市 API 憑證有瑕疵，程式以 `verify_ssl=False` 連線，雲端可正常運作。
- 站點資料與地圖會用到 `@st.cache_data` / `@st.cache_resource`，雲端重啟後
  首次載入較慢屬正常。
- 若部署後改了 Secrets，需在 App 選單按 **Reboot** 才會生效。

---

## 金鑰如何被讀取

`config/settings.py` 一律從環境變數讀取金鑰與設定，來源有三種，程式碼不必改：

- **Docker**：`docker-compose.yml` 把專案資料夾的 `.env` 交給容器當環境變數。
- **Streamlit Community Cloud**：App 啟動時 `_bridge_secrets_to_env()` 把 `st.secrets` 的值寫入環境變數。
- **本機直接執行**：`config/settings.py` 自己讀取專案根目錄的 `.env`。

## 本機預覽（不用 Docker）

```bash
pip install -r requirements.txt
cp .env.example .env        # 然後填入金鑰（本機用 .env 即可）
streamlit run app.py
```

> 本機只需根目錄的 `.env`；`.streamlit/secrets.toml` 是給雲端用的，本機不必建。
> 若仍想在本機用 secrets 方式測試，可自行建立 `.streamlit/secrets.toml`（已被 gitignore）。
