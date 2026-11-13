"""資料來源網址的設定檢查。"""
from urllib.parse import parse_qs, urlparse

from config import CITY_CONFIG


def test_taoyuan_api_requests_all_stations():
    # 桃園開放資料 API 不帶 limit 時只回傳 20 筆，會讓桃園幾乎沒有站可用
    query = parse_qs(urlparse(CITY_CONFIG["桃園市"]["api_url"]).query)
    assert int(query["limit"][0]) >= 2000
