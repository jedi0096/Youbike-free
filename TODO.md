# YouBike 最省錢攻略 - TODO

## Phase 1: 環境建置與資料取得
- [x] 建立專案結構與虛擬環境
- [x] 撰寫 requirements.txt
- [x] 串接台北市 YouBike API，確認回傳格式
- [x] 串接新北市 YouBike API
- [x] 建立 JSON 快取機制（檔案快取 + TTL 過期）
- [x] 撰寫 fetcher.py 單元測試
- [ ] 在有網路的環境跑一次真實 API 煙霧測試（沙箱無網路）

## Phase 2: 資料清洗與圖建構
- [x] 統一各縣市 API 回傳欄位名稱
- [x] 過濾異常站點（座標為 0、無名稱）
- [x] 實作 haversine 距離計算
- [x] 實作騎乘時間估算函數
- [x] 用 NetworkX 建立站點圖
- [x] 只連接「可用騎乘時間」半徑內的站點對（緯度視窗預過濾優化）
- [x] 撰寫 graph_builder 單元測試
- [x] 用真實台北資料跑一次建圖（1740 站、95 萬邊，6.5s）

## Phase 3: 路徑演算法實作
- [x] 實作最近站點搜尋（暴力搜尋；KD-Tree 留待 Phase 6）
- [x] 實作 BFS 最短路徑（最少換車次數）
- [x] 實作 Dijkstra 最短路徑（最短總時間）
- [x] 處理無解情況並回傳友善訊息
- [x] 驗證：台北車站→淡水 的路線是否合理（fewest_swaps 3 換車/99.3 分，shortest_time 4 換車/97.2 分）
- [x] 撰寫 route_optimizer 單元測試

## Phase 4: 地圖視覺化
- [x] 用 Folium 繪製所有站點（顏色依車輛狀態）
- [x] 繪製推薦路線 PolyLine
- [x] 標記換車站點（含 popup 資訊）
- [x] 標記起終點
- [x] 每段路線標示預估時間
- [x] 撰寫 map_renderer 單元測試

## Phase 5: Streamlit 互動介面
- [x] 建立基本頁面佈局（sidebar + 主區域）
- [x] 實作城市選擇下拉選單（multiselect）
- [x] 實作起終點輸入（先支援經緯度數值輸入；地址 geocoding 留待加強）
- [x] 實作路線策略切換
- [x] 整合 streamlit-folium 顯示地圖
- [x] 顯示路線摘要卡片（4 個 metric）
- [x] 顯示詳細行程表（含每段免費餘裕）
- [x] @st.cache_data / cache_resource 加速重複規劃
- [x] 地址 → 經緯度 geocoding（geopy Nominatim，含節流 + 快取；UI 可切換地址/經緯度輸入）

## Phase 6: 進階功能（加分項）
- [x] 即時車輛數判斷（避免推薦無車/滿位的站）— graph build 依即時車輛數過濾邊，UI 可切換
- [~] 多縣市支援（桃園、高雄）
  - [x] 資料層 plug-in ready：fetcher 支援巢狀回傳（data_path，如 CKAN result.records）+ 測試
  - [x] config 標註接入方式（data_path / verify_ssl 說明）
  - [x] 桃園接入；其餘縣市（竹竹苗、台中、嘉南高屏、台東）改用 YouBike 官方全台資料
        （apis.youbike.com.tw/json/station-yb2.json）依 area_code 篩選
  - [x] 介面改為「先選區域、再選縣市」，一次只載入一個區域以控制記憶體
  - [x] 跨區還車改用官網調度費表逐對判斷（如桃園↔新竹免費、桃園→苗栗 600 元）
  - [ ] 在有網路的環境跑 `python scripts/check_cities.py` 確認各縣市 area_code 都抓得到站點
  - [ ] 新竹縣、竹科、苗栗、台中、嘉義市、嘉義縣、屏東、台東的免費規則待對照官網確認
- [x] 跨縣市路線規劃（同生活圈如台北↔新北自動連邊；跨生活圈以 allow_cross_circle 開關 + 調度費警示）
      — 逐段免費上限已改為依起站縣市 + 是否持 TPASS（見 Phase 7）
- [x] 同站續借冷卻時間處理（實測約需等待 10–15 分鐘才能再借同一站）
  - [x] 演算法：node-split 模型（借/還節點）加入 walk（步行鄰站借車）與 wait（原站等冷卻）邊，
        讓冷卻成本被納入最佳化；UI 可切換「啟用步行換車」、地圖以虛線/時鐘標示、行程表分類型顯示
  - [x] UI 提示：每個換車點顯示冷卻警示，並列出 300m 內有車可改借的鄰站（一般模式）
  - [ ] 對應 GitHub issue：同站換車冷卻時間（10–15 分鐘）
- [x] 效能優化：最近站搜尋向量化（numpy 一次算完所有站距，取代逐站 Python 迴圈；環境無 scipy/sklearn，KD-Tree 待加裝後再評估）
- [x] 部署到 Streamlit Cloud：備妥 .streamlit/config.toml、secrets 範本與 st.secrets→env 橋接、DEPLOY.md 指南（實際按部署鈕需你的帳號）

## Phase 7: 真實時間、兩 case 免費、.env 與 AI 回饋
- [x] .env 載入（零相依解析）+ settings：集中管理 API 金鑰（.env 已在 .gitignore，附 .env.example）
- [x] 兩 case 免費騎乘時長：依「起站縣市 + 是否持 TPASS」決定逐段免費上限（聚焦北北桃）
- [x] Google Maps 騎乘時間校正：bicycling → driving 道路距離換算 → haversine 回退；UI 逐段比較並警示超時
- [x] AI 行程回饋：客觀事實收集 → Gemini 語氣潤飾（無金鑰自動回退本地模板摘要）
- [ ] Google bicycling 在台灣的實際支援狀況待有網路時驗證（目前已備妥 driving 距離回退）
- [x] AI 回饋納入即時車況作為事實：起點借車站缺車、終點還車站滿位、換車點即時車況皆併入並產生警示
- [x] AI 回饋納入 Google 校正結果作為事實：跑過校正後，逐段 Google 時間與超時路段併入 AI 事實（以路線指紋確保對應）
