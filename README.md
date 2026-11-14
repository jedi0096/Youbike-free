# YouBike 最省錢騎乘攻略

在地圖上選定出發點與終點，系統自動規劃**全程免費**的 YouBike 騎乘路線。
核心策略：利用各縣市的免費騎乘時間（台北/新北 30 分鐘、桃園 60 分鐘；
電輔車 2.0E 不適用免費時段，不列入可借車數），透過中途換車，讓每一段騎乘時間都控制在免費上限內。

## 解決什麼問題

YouBike 對較長距離的單趟騎乘會開始收費。但只要在免費時間結束前到達某站
還車、再立即借另一輛車繼續騎，就可以全程不花錢。本工具自動找出**最佳的
換車站序列**，讓你不用自己掐表算路線。

## 功能特性

- **多縣市資料整合**：即時抓取台北、新北、桃園 YouBike 開放資料，另以 YouBike 官方全台資料
  支援竹竹苗、台中、嘉南高屏、台東（含 JSON 快取）；側欄先選區域、一次只載入一個區域；
  fetcher 支援巢狀回傳格式（如 CKAN `result.records`），補上 API URL 即可擴充縣市
- **兩種規劃策略**
  - `fewest_swaps`：最少換車次數（BFS）
  - `shortest_time`：最短總騎乘時間（Dijkstra）
- **兩 case 免費時長**：依「**起站縣市 + 是否持 TPASS 月票**」逐段判斷免費上限。
  跨縣市路線（如桃園↔台北）每一段各自套用其借車地的規則
- **即時車況過濾**：可選擇只規劃「有車可借／有位可還」的站，避免推薦到空站或滿站
- **同站續借冷卻處理**：換車點還車後於同站再借需等 10~15 分鐘冷卻。一般模式會列出
  300m 內有車可改借的鄰站；另提供 opt-in 的 **node-split 冷卻模型**，把「步行到鄰站
  借車」與「原站等冷卻」建模成可被最佳化的路段，地圖以虛線 / 時鐘標示
- **地址 / 地標查詢**：可直接輸入「台北車站」「淡水捷運站」等，自動轉成經緯度
  （geopy Nominatim，含節流與快取）
- **Google Maps 時間校正**（選用）：以真實道路距離校正各段騎乘時間，並標示是否
  超出免費上限。採 bicycling → driving 道路距離換算 → 直線估算 三層回退
- **AI 行程建議**（選用）：客觀事實由程式計算，再由 Gemini 潤飾成親切好讀的建議；
  無金鑰時自動回退本地文字摘要
- **生活圈邊界保護**：預設不規劃跨生活圈路線，避免被收 600~1135 元調度費
- **互動式地圖**：Streamlit + Folium 視覺化，站點顏色顯示車況、路線標示換車點與
  每段預估時間

## 技術架構

```
config/
  city_rules.py   各縣市免費規則（兩 case）、API 端點、生活圈定義
  settings.py     零相依 .env 載入 + API 金鑰集中管理
data/             API 擷取（含巢狀格式容錯）+ 欄位正規化
core/
  graph_builder.py   站點圖建構（逐段依借車地縣市套用免費上限）
  route_optimizer.py BFS / Dijkstra 路徑演算法 + 冷卻改借建議
  cooldown.py        node-split 冷卻模型（borrow/return 節點 + walk/wait 邊）
  geocoder.py        地址 → 經緯度（Nominatim）
  gmaps.py           Google Maps 道路時間校正（含回退）
  feedback.py        AI 回饋：事實收集 + Gemini 語氣潤飾
visualization/    Folium 地圖渲染
tests/            pytest 單元測試（目前 73 passed）
app.py            Streamlit 入口
```

**演算法本質**：帶約束的圖論最短路徑問題。節點 = YouBike 站點，
邊 = 兩站間的騎乘時間估算（haversine × 1.3 路徑修正 ÷ 12 km/h），
約束 = 每段邊權重 ≤ 該段借車地的免費上限 − 安全餘裕。

## 安裝

```bash
pip install -r requirements.txt
```

需求：Python 3.10+

## 設定（選用功能的 API 金鑰）

地圖時間校正與 AI 建議為選用功能，需要對應金鑰。複製範本後填入即可
（`.env` 已列入 `.gitignore`，不會被提交）：

```bash
cp .env.example .env
```

```ini
# Google Maps Platform —— 用於以真實道路距離校正騎乘時間（需啟用 Distance Matrix API）
GOOGLE_MAPS_API_KEY=你的金鑰

# Google Gemini —— AI 回饋語氣潤飾；申請：https://aistudio.google.com/app/apikey
GEMINI_API_KEY=你的金鑰
GEMINI_MODEL=gemini-2.5-flash
```

未設定金鑰時，App 仍可正常規劃路線：時間以直線估算、AI 建議改用本地文字摘要。

## 執行

互動介面：

```bash
streamlit run app.py
```

側欄可切換城市、是否持 TPASS、規劃策略、即時車況過濾、以及用地址或經緯度
輸入起終點。規劃後可按「用 Google Maps 校正騎乘時間」與「產生 AI 建議」。

CLI 煙霧測試（驗證資料管線與路徑演算法）：

```bash
python scripts/smoke_plan_route.py    # 台北車站 → 淡水捷運站
python scripts/smoke_render_map.py    # 產出 route_preview.html
```

## 部署

部署到 Streamlit Community Cloud 的完整步驟（含 Secrets 設定）見 [DEPLOY.md](DEPLOY.md)。
`.streamlit/config.toml` 為主題與伺服器設定；金鑰透過 Streamlit Secrets 提供，
啟動時會自動橋接到環境變數。

## 開發進度

詳細待辦事項見 [TODO.md](TODO.md)。

- [x] Phase 1：資料擷取 + 快取
- [x] Phase 2：欄位正規化 + 圖建構
- [x] Phase 3：路徑演算法（BFS / Dijkstra）
- [x] Phase 4：Folium 地圖視覺化
- [x] Phase 5：Streamlit 互動介面（含地址 geocoding）
- [~] Phase 6：進階優化（即時車況、跨縣市、冷卻提醒、最近站向量化已完成；
  多縣市 URL、KD-Tree、Streamlit Cloud 部署待補）
- [x] Phase 7：兩 case 免費時長、.env 設定、Google Maps 校正、AI 行程建議

## 真實案例

台北車站 → 淡水捷運站（北北桃生活圈、約 19 km）：

| 策略 | 換車次數 | 總騎乘時間 |
|---|---|---|
| 最少換車 | 3 次 | 99.3 分 |
| 最短時間 | 4 次 | 97.2 分 |

每段皆控制在 27 分鐘內（30 分鐘免費 − 3 分鐘安全餘裕），全程免費。
