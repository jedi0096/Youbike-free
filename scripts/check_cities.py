"""逐縣市實際抓一次即時資料，確認各縣市資料來源可用（需網路）。

用法：python scripts/check_cities.py
印出每個縣市抓到幾站、過濾後剩幾站，以及失敗原因。
新接的縣市（新竹、台中、嘉南高屏、台東等）走 YouBike 官方全台資料，
若某縣市顯示「查無站點」，代表官方資料的 area_code 與 config 設定不符，
請到 config/city_rules.py 調整該縣市的 area_code。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import REGIONS, free_minutes_for, supported_cities  # noqa: E402
from data.fetcher import FetchError, fetch_city_stations  # noqa: E402
from data.preprocessor import filter_invalid_stations, normalize_stations  # noqa: E402


def main() -> int:
    bad = 0
    for region in REGIONS:
        print(f"== {region}")
        for city in supported_cities(region):
            try:
                raw = fetch_city_stations(city, use_cache=False)
            except (FetchError, ValueError) as e:
                bad += 1
                print(f"  ✗ {city}: {e}")
                continue
            df = filter_invalid_stations(normalize_stations(raw, city))
            print(
                f"  ✓ {city}: 原始 {len(raw)} 站、有效 {len(df)} 站；"
                f"免費 一般 {free_minutes_for(city, False)} 分 / TPASS {free_minutes_for(city, True)} 分"
            )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
