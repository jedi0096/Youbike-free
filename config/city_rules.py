"""各縣市 YouBike 免費騎乘規則與 API 端點設定"""

CITY_CONFIG = {
    "台北市": {
        # free_rules：依是否持 TPASS 月票判斷每段免費騎乘上限（分鐘）。
        # 台北一般會員與 TPASS 皆為前 30 分鐘免費。數值如有調整可逕改此處。
        "free_rules": {"general": 30, "tpass": 30},
        "free_minutes": 30,  # = general，供顯示與向後相容
        "eligibility": "會員即享",
        "api_url": "https://tcgbusfs.blob.core.windows.net/dotapp/youbike/v2/youbike_immediate.json",
        "living_circle": "北北桃",
        "note": "2024/2/28 起前 30 分鐘免費（市府補助 10 元）",
    },
    "新北市": {
        "free_rules": {"general": 30, "tpass": 30},
        "free_minutes": 30,
        "eligibility": "會員即享",
        "api_url": "https://data.ntpc.gov.tw/api/datasets/010e5b15-3823-4b20-b401-b1cf000550c5/json?size=2000",
        "verify_ssl": False,  # NTPC 憑證缺 Subject Key Identifier，新版 Python ssl 驗證會失敗
        "living_circle": "北北桃",
        "note": "2025/3/1 起前 30 分鐘免費",
    },
    # 接入待辦：填入實際 api_url 即可。若 API 把站點清單包在巢狀 dict
    # （如 CKAN：{"result": {"records": [...]}}），加上
    #   "data_path": ["result", "records"]
    # fetcher 會自動取出清單；欄位名稱差異由 preprocessor.FIELD_MAP 處理。
    # 若 API 憑證有瑕疵，加上 "verify_ssl": False。
    "桃園市": {
        # 桃園 2023/4/1 起會員租借 YouBike 2.0 前 60 分鐘免費（TPASS 同樣 60 分）。
        # 來源：https://www.youbike.com.tw/region/tycg/rate/
        "free_rules": {"general": 60, "tpass": 60},
        "free_minutes": 60,
        "eligibility": "會員即享前 60 分",
        "api_url": "https://opendata.tycg.gov.tw/api/v1/dataset.api_access?rid=08274d61-edbe-419d-8fcc-7a643831283d&format=json",
        "living_circle": "北北桃",
        "note": "2023/4/1 起會員前 60 分鐘免費",
    },
    "高雄市": {
        "free_minutes": 30,
        "eligibility": "TPASS / MeN Go",
        "api_url": "https://openapi.kcg.gov.tw/Api/Service/Get/b4dd9c40-9027-4125-8666-06bef1756092",
        "living_circle": "嘉南高屏",
        "note": "TPASS/MeNGo 用戶前 30 分鐘免費；轉乘大眾運輸 1hr 內借車亦免費",
    },
    "屏東縣": {
        "free_minutes": 30,
        "eligibility": "會員即享",
        "api_url": "TODO_NEED_TO_FIND",
        "living_circle": "嘉南高屏",
        "note": "會員第一個 30 分鐘由縣府補助",
    },
}

# 同一生活圈內跨縣市借還車免調度費
# 跨生活圈歸還需支付 600～1135 元調度費
LIVING_CIRCLES = {
    "北北桃": ["台北市", "新北市", "桃園市"],
    "嘉南高屏": ["嘉義市", "嘉義縣", "台南市", "高雄市", "屏東縣"],
}

# 跨生活圈調度費（NTD）—— 用於警告訊息
CROSS_CIRCLE_FEE_NTD = (600, 1135)


def city_to_circle(city: str) -> str | None:
    """回傳該縣市所屬生活圈名稱；找不到回傳 None。"""
    for circle, members in LIVING_CIRCLES.items():
        if city in members:
            return circle
    return None


# 目前專案聚焦的生活圈（其餘縣市暫不在 app 主流程支援）。
ACTIVE_CIRCLE = "北北桃"


def free_minutes_for(city: str, has_tpass: bool) -> int:
    """回傳某縣市在指定持票狀態下的免費騎乘上限（分鐘）。

    依 free_rules 的 "tpass" / "general" 取值；若該縣市未定義 free_rules，
    退回頂層 free_minutes。
    """
    cfg = CITY_CONFIG.get(city, {})
    rules = cfg.get("free_rules")
    if rules:
        return int(rules["tpass" if has_tpass else "general"])
    return int(cfg.get("free_minutes", 30))


def free_minutes_by_city(cities: list[str], has_tpass: bool) -> dict[str, int]:
    """回傳 {縣市: 免費上限} 對照表，供逐段（依起點縣市）判斷免費時限用。"""
    return {c: free_minutes_for(c, has_tpass) for c in cities}


def active_cities() -> list[str]:
    """目前 app 主流程支援的縣市：屬 ACTIVE_CIRCLE 且已設定有效 API URL。"""
    return [
        c for c, cfg in CITY_CONFIG.items()
        if cfg.get("living_circle") == ACTIVE_CIRCLE
        and cfg.get("api_url")
        and not cfg["api_url"].startswith("TODO")
    ]

# 騎乘參數預設值
DEFAULT_CYCLING_SPEED_KMH = 12      # 平均騎乘速度
ROUTE_DETOUR_FACTOR = 1.3           # 實際路徑 vs 直線距離修正係數
SAFETY_MARGIN_MINUTES = 3           # 安全餘裕（建議提前幾分鐘還車）
