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
        # 桃園一般會員前 30 分鐘免費；持 TPASS 月票延長為前 60 分鐘免費。
        "free_rules": {"general": 30, "tpass": 60},
        "free_minutes": 30,
        "eligibility": "一般 30 分；TPASS 60 分",
        "api_url": "https://opendata.tycg.gov.tw/api/v1/dataset.api_access?rid=08274d61-edbe-419d-8fcc-7a643831283d&format=json",
        "living_circle": "北北桃",
        "note": "一般會員前 30 分鐘免費；TPASS 用戶前 60 分鐘免費",
    },
    # ---- 以下縣市使用 YouBike 官方全台站點資料（單一網址），依 area_code 篩出該縣市 ----
    # area_code 對照取自 https://apis.youbike.com.tw/json/area-all.json。
    # 免費規則查證日：2026-11-13。verified=True 表示已對照 YouBike 官網該區收費頁；
    # False 表示僅有第三方整理（money101 / cashfeel）或查無資料，請以官網公告為準。
    # 查無「一般會員免費」資料的縣市一律保守設 0 分（等於不規劃免費路線），
    # 避免把要付費的路段誤標為免費。
    "新竹市": {
        "free_rules": {"general": 30, "tpass": 30},
        "free_minutes": 30,
        "eligibility": "會員即享；TPASS 桃竹竹苗票種 2.0/2.0E 皆免費",
        "source": "youbike_national", "area_code": "09",
        "living_circle": "竹竹苗",
        "verified": True,
        "note": "新竹市府補助前 30 分鐘（不含竹科站點）",
    },
    "新竹縣": {
        "free_rules": {"general": 0, "tpass": 0},
        "free_minutes": 0,
        "eligibility": "查無補助資料",
        "source": "youbike_national", "area_code": "0B",
        "living_circle": "竹竹苗",
        "verified": False,
        "note": "第三方整理稱無補助；未取得官網資料，暫視為無免費時段",
    },
    "新竹科學園區": {
        "free_rules": {"general": 0, "tpass": 0},
        "free_minutes": 0,
        "eligibility": "查無補助資料",
        "source": "youbike_national", "area_code": "10",
        "living_circle": "竹竹苗",
        "verified": False,
        "note": "新竹市補助明訂不含竹科；暫視為無免費時段",
    },
    "苗栗縣": {
        "free_rules": {"general": 30, "tpass": 30},
        "free_minutes": 30,
        "eligibility": "會員即享（待官網確認）",
        "source": "youbike_national", "area_code": "0A",
        "living_circle": "竹竹苗",
        "verified": False,
        "note": "第三方整理（cashfeel 2025/10）稱前 30 分鐘免費",
    },
    "台中市": {
        "free_rules": {"general": 30, "tpass": 30},
        "free_minutes": 30,
        "eligibility": "會員即享",
        "source": "youbike_national", "area_code": "01",
        "living_circle": "台中",
        "verified": False,
        "note": "多個第三方來源一致：前 30 分鐘免費",
    },
    "嘉義市": {
        "free_rules": {"general": 30, "tpass": 30},
        "free_minutes": 30,
        "eligibility": "會員即享（待官網確認）",
        "source": "youbike_national", "area_code": "08",
        "living_circle": "嘉南高屏",
        "verified": False,
        "note": "第三方整理（cashfeel 2025/10）稱前 30 分鐘免費",
    },
    "嘉義縣": {
        "free_rules": {"general": 0, "tpass": 0},
        "free_minutes": 0,
        "eligibility": "查無補助資料",
        "source": "youbike_national", "area_code": "11",
        "living_circle": "嘉南高屏",
        "verified": False,
        "note": "未取得補助資料，暫視為無免費時段",
    },
    "台南市": {
        "free_rules": {"general": 0, "tpass": 30},
        "free_minutes": 0,
        "eligibility": "TPASS（台南票種）前 30 分鐘免費；一般會員無補助",
        "source": "youbike_national", "area_code": "13",
        "living_circle": "嘉南高屏",
        "verified": True,
        "note": "市民卡為全時段半價（非免費）；TPASS 前 30 分鐘免費",
    },
    "高雄市": {
        "free_rules": {"general": 0, "tpass": 30},
        "free_minutes": 0,
        "eligibility": "TPASS / MeNGo 前 30 分鐘免費；一般會員僅補助 5 元",
        "source": "youbike_national", "area_code": "12",
        "living_circle": "嘉南高屏",
        "verified": True,
        "note": "學生證、兒童卡、1 小時內轉乘也有前 30 分鐘免費（本工具未細分）",
    },
    "屏東縣": {
        "free_rules": {"general": 30, "tpass": 30},
        "free_minutes": 30,
        "eligibility": "會員即享",
        "source": "youbike_national", "area_code": "14",
        "living_circle": "嘉南高屏",
        "verified": False,
        "note": "會員第一個 30 分鐘由縣府補助（第三方整理一致）",
    },
    "台東縣": {
        "free_rules": {"general": 0, "tpass": 0},
        "free_minutes": 0,
        "eligibility": "查無補助資料",
        "source": "youbike_national", "area_code": "15",
        "living_circle": "台東",
        "verified": False,
        "note": "未取得補助資料，暫視為無免費時段",
    },
}

# YouBike 官方全台站點即時資料（一次涵蓋所有營運縣市，以 area_code 區分）。
YOUBIKE_NATIONAL_URL = "https://apis.youbike.com.tw/json/station-yb2.json"

# 區域：介面上的「一次載入範圍」。只會載入並建圖所選區域的縣市，
# 避免一次把全台站點建成一張圖（記憶體會暴增）。
LIVING_CIRCLES = {
    "北北桃": ["台北市", "新北市", "桃園市"],
    "竹竹苗": ["新竹市", "新竹縣", "新竹科學園區", "苗栗縣"],
    "台中": ["台中市"],
    "嘉南高屏": ["嘉義市", "嘉義縣", "台南市", "高雄市", "屏東縣"],
    "台東": ["台東縣"],
}
REGIONS = LIVING_CIRCLES

# 跨區還車調度費（YouBike 2.0，新台幣）。取自 YouBike 官網各區收費頁的
# 「跨區調度費」表（2026-11-13 查閱）。表中以下列群組為單位：
_FEE_GROUP = {
    "台北市": "A", "新北市": "A",
    "桃園市": "B",
    "新竹市": "C", "新竹縣": "C", "新竹科學園區": "C",
    "苗栗縣": "D",
    "台中市": "E",
    "嘉義市": "F", "嘉義縣": "F", "台南市": "F", "高雄市": "F", "屏東縣": "F",
    "台東縣": "G",
}
# (借車群組, 還車群組) -> 調度費；表中空白（免費）的組合不列。
_RETURN_FEE_NTD = {
    ("A", "C"): 630, ("A", "D"): 630, ("A", "E"): 815, ("A", "F"): 910, ("A", "G"): 1040,
    ("B", "D"): 600, ("B", "E"): 750, ("B", "F"): 910, ("B", "G"): 1075,
    ("C", "A"): 630, ("C", "F"): 660, ("C", "G"): 1135,
    ("D", "A"): 630, ("D", "B"): 600, ("D", "F"): 660, ("D", "G"): 1130,
    ("E", "A"): 815, ("E", "B"): 750, ("E", "F"): 660, ("E", "G"): 1065,
    ("F", "A"): 910, ("F", "B"): 910, ("F", "C"): 660, ("F", "D"): 660,
    ("F", "E"): 660, ("F", "G"): 750,
    ("G", "A"): 1040, ("G", "B"): 1075, ("G", "C"): 1135, ("G", "D"): 1130,
    ("G", "E"): 1065, ("G", "F"): 750,
}

# 跨生活圈調度費範圍（NTD）—— 用於找不到確切金額時的警告訊息
CROSS_CIRCLE_FEE_NTD = (600, 1135)


def return_fee_ntd(from_city: str, to_city: str) -> int:
    """在 from_city 借車、to_city 還車需付的跨區調度費（0 = 免調度費）。

    未知縣市一律回傳 CROSS_CIRCLE_FEE_NTD 上限，寧可多警告也不要漏警告。
    """
    if from_city == to_city:
        return 0
    a, b = _FEE_GROUP.get(from_city), _FEE_GROUP.get(to_city)
    if a is None or b is None:
        return CROSS_CIRCLE_FEE_NTD[1]
    return _RETURN_FEE_NTD.get((a, b), 0)


def city_to_circle(city: str) -> str | None:
    """回傳該縣市所屬區域名稱；找不到回傳 None。"""
    for circle, members in LIVING_CIRCLES.items():
        if city in members:
            return circle
    return None


# 預設區域（介面初次開啟時選取）。
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


def has_data_source(city: str) -> bool:
    """該縣市是否已設定可用的資料來源。"""
    cfg = CITY_CONFIG.get(city, {})
    if cfg.get("source") == "youbike_national":
        return bool(cfg.get("area_code"))
    url = cfg.get("api_url") or ""
    return bool(url) and not url.startswith("TODO")


def supported_cities(region: str | None = None) -> list[str]:
    """有資料來源的縣市；指定 region 時只回傳該區域內的縣市（依區域內順序）。"""
    if region is None:
        pool = [c for members in LIVING_CIRCLES.values() for c in members]
    else:
        pool = LIVING_CIRCLES.get(region, [])
    return [c for c in pool if c in CITY_CONFIG and has_data_source(c)]


def active_cities() -> list[str]:
    """預設區域（ACTIVE_CIRCLE）中已設定資料來源的縣市。"""
    return supported_cities(ACTIVE_CIRCLE)

# 騎乘參數預設值
DEFAULT_CYCLING_SPEED_KMH = 12      # 平均騎乘速度
ROUTE_DETOUR_FACTOR = 1.3           # 實際路徑 vs 直線距離修正係數
SAFETY_MARGIN_MINUTES = 3           # 安全餘裕（建議提前幾分鐘還車）
