"""Folium 地圖渲染：站點標記、路線繪製、換車點標示。"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import folium
import pandas as pd
from branca.element import MacroElement
from jinja2 import Template

from core.route_optimizer import RoutePlan

LOW_THRESHOLD = 3


def _station_color(available_bikes: int, available_docks: int) -> str:
    """依車輛 / 空位數回傳 marker 顏色。"""
    if available_bikes == 0 or available_docks == 0:
        return "red"
    if available_bikes < LOW_THRESHOLD or available_docks < LOW_THRESHOLD:
        return "orange"
    return "green"


# 底圖樣式：介面顯示名稱 → folium tiles 名稱
MAP_STYLES: dict[str, str] = {
    "標準": "OpenStreetMap",
    "淺色（看路線較清楚）": "CartoDB positron",
    "深色": "CartoDB dark_matter",
}

_LEGEND_HTML = """
<style>@media (max-width: 640px) { .yb-legend { display: none; } }</style>
<div class="yb-legend" style="position: fixed; bottom: 26px; right: 12px; z-index: 9999;
     background: rgba(255,255,255,0.92); padding: 6px 10px; border-radius: 6px;
     font-size: 12px; line-height: 1.6; box-shadow: 0 1px 4px rgba(0,0,0,0.25);
     color: #222;">
  <span style="color:#2e8b57;">●</span> 車位充足
  &nbsp;<span style="color:orange;">●</span> 少於 3
  &nbsp;<span style="color:red;">●</span> 沒車或沒位
</div>
"""


@dataclass(frozen=True)
class ViewPadding:
    """地圖四周被浮動卡片蓋住的範圍，調整視野時要避開。

    桌機用像素（left / top / right / bottom）；手機（寬度 <= mobile_max_width）上下
    改用 mobile_top 像素與 mobile_bottom_ratio（佔畫面高度的比例）。
    以上都是估計值；有給 search_selector / sheet_selector 時，瀏覽器端會量卡片實際大小。
    """
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    mobile_max_width: int = 640
    mobile_top: int = 0
    mobile_bottom_ratio: float = 0.0
    # 網頁上浮動卡片的 CSS 選擇器；瀏覽器端量得到實際大小時，以實際大小為準
    search_selector: str = ""
    sheet_selector: str = ""


class _OverlayAwareView(MacroElement):
    """在瀏覽器端依實際視窗大小調整地圖：

    - 把縮放按鈕放到右上角（左上角被搜尋卡蓋住；手機用兩指縮放，不放按鈕）。
    - 有路線時，把視野縮放到整條路線都落在「沒被卡片蓋住」的範圍內；
      視窗大小改變（例如手機轉向）時重新調整，但使用者動過地圖就不再自動移動。
    - 站點說明框打開時，自動移到不被卡片蓋住的地方。

    視窗大小只有瀏覽器知道，所以這段必須用 JS 做；Python 端先用 view_for_route
    給一個大致的中心與縮放，JS 沒跑到時地圖仍然看得到路線。
    """

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
            var map = {{ this._parent.get_name() }};
            var pad = {{ this.pad|tojson }};
            var bounds = {{ this.bounds|tojson }};
            var GAP = 24;        // 路線和卡片之間留的空隙（px）
            var MIN_FREE = 120;  // 路線至少要有這麼大的可視範圍（px），卡片再大也不能擠掉
            // 讓地圖撐滿外框（外框高度由頁面 CSS 決定，不是 st_folium 給的固定像素）
            for (var el = map.getContainer(); el && el !== document.body; el = el.parentElement) {
                el.style.height = "100vh";
            }
            map.invalidateSize(false);

            // 卡片實際蓋住多少：盡量量網頁上的卡片，量不到就用 Python 給的估計值。
            // 地圖的 iframe 鋪滿整個視窗，所以網頁座標和地圖座標相同。
            function rectOf(sel) {
                try {
                    var e = window.parent.document.querySelector(sel);
                    if (!e) { return null; }
                    var r = e.getBoundingClientRect();
                    return (r.width && r.height) ? r : null;
                } catch (err) { return null; }
            }
            function overlayPadding(mobile) {
                var size = map.getSize();
                var search = rectOf(pad.search_selector), sheet = rectOf(pad.sheet_selector);
                var tl, br;
                if (mobile) {
                    var top = search ? search.bottom : pad.mobile_top;
                    var bottom = sheet ? size.y - sheet.top
                                       : Math.round(size.y * pad.mobile_bottom_ratio);
                    tl = [GAP, top + GAP];
                    br = [GAP, Math.max(0, bottom) + GAP];
                } else {
                    var left = pad.left;
                    if (search || sheet) {
                        left = Math.max(search ? search.right : 0, sheet ? sheet.right : 0);
                    }
                    tl = [left + GAP, pad.top + GAP];
                    br = [pad.right + GAP, pad.bottom + GAP];
                }
                // 視窗太小時按比例縮小留白，至少留 MIN_FREE 給路線
                [0, 1].forEach(function (i) {
                    var total = tl[i] + br[i], room = (i ? size.y : size.x) - MIN_FREE;
                    if (total > room) {
                        var k = Math.max(0, room) / total;
                        tl[i] = Math.floor(tl[i] * k); br[i] = Math.floor(br[i] * k);
                    }
                });
                return {tl: tl, br: br};
            }

            var zoomCtl = null, lastMobile = null, userMoved = false;
            map.on("dragstart", function () { userMoved = true; });
            map.getContainer().addEventListener("wheel", function () { userMoved = true; });
            map.getContainer().addEventListener("touchstart", function (e) {
                if (e.touches && e.touches.length > 1) { userMoved = true; }
            });

            function fit(mobile) {
                if (!bounds || userMoved) { return; }
                var p = overlayPadding(mobile);
                map.fitBounds(bounds, {
                    paddingTopLeft: p.tl, paddingBottomRight: p.br, maxZoom: 17, animate: false
                });
            }
            function apply(force) {
                var mobile = window.innerWidth <= pad.mobile_max_width;
                map.invalidateSize(false);
                // 縮放按鈕放右上角（左上角被搜尋卡蓋住）；手機用兩指縮放，不放按鈕
                if (!mobile && !zoomCtl) {
                    zoomCtl = L.control.zoom({position: "topright"}).addTo(map);
                } else if (mobile && zoomCtl) {
                    map.removeControl(zoomCtl); zoomCtl = null;
                }
                if (force || mobile !== lastMobile) { fit(mobile); }
                lastMobile = mobile;
            }
            apply(true);
            // 卡片可能比地圖晚畫好，稍後再量一次（使用者已經動過地圖就不動）
            setTimeout(function () { apply(true); }, 600);
            var resizeTimer = null;
            window.addEventListener("resize", function () {
                clearTimeout(resizeTimer);
                resizeTimer = setTimeout(function () { apply(true); }, 150);
            });

            // 點站點跳出的說明框，自動移到不被卡片蓋住的地方
            map.on("popupopen", function (e) {
                var p = overlayPadding(window.innerWidth <= pad.mobile_max_width);
                e.popup.options.autoPanPaddingTopLeft = L.point(p.tl[0], p.tl[1]);
                e.popup.options.autoPanPaddingBottomRight = L.point(p.br[0], p.br[1]);
                e.popup.update();
                e.popup._adjustPan && e.popup._adjustPan();
            });
        })();
        {% endmacro %}
    """)

    def __init__(self, pad: ViewPadding, bounds: list[list[float]] | None = None):
        super().__init__()
        self._name = "OverlayAwareView"
        self.pad = asdict(pad)
        self.bounds = bounds


def fit_view_to_overlays(
    m: folium.Map,
    pad: ViewPadding,
    points: list[tuple[float, float]] | None = None,
) -> folium.Map:
    """讓地圖避開浮動卡片：縮放按鈕移到右上，並（有 points 時）把視野對準這些點。

    搭配 render_base_map(..., zoom_control=False) 使用，否則會出現兩組縮放按鈕。
    """
    bounds = None
    if points:
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
    m.add_child(_OverlayAwareView(pad, bounds))
    return m


def route_points(
    plan: RoutePlan,
    stations: pd.DataFrame,
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> list[tuple[float, float]]:
    """起終點加上路線經過的每個站的座標。"""
    pts = [origin, destination]
    if plan.feasible and plan.segments:
        coords = stations.set_index("station_id")[["lat", "lon"]].to_dict("index")
        for s in plan.segments:
            for sid in (s.from_station_id, s.to_station_id):
                c = coords.get(sid)
                if c:
                    pts.append((c["lat"], c["lon"]))
    return pts


def render_base_map(
    stations: pd.DataFrame,
    center: tuple[float, float] | None = None,
    zoom_start: int = 13,
    tiles: str = "OpenStreetMap",
    show_legend: bool = True,
    zoom_control: bool = True,
) -> folium.Map:
    """建立含站點 CircleMarker 的底圖。

    stations 可傳入全部站點，或只傳路線附近的站（減少手機上的繪製負擔）；
    傳入空 DataFrame 時只畫底圖。
    """
    if center is None:
        if stations.empty:
            center = (25.0478, 121.5170)
        else:
            center = (float(stations["lat"].mean()), float(stations["lon"].mean()))
    m = folium.Map(
        location=center, zoom_start=zoom_start, tiles=tiles, zoom_control=zoom_control
    )
    if stations.empty:
        return m
    fg = folium.FeatureGroup(name="站點")
    # itertuples 比 iterrows 快很多（站點上千個時差異明顯）
    for row in stations.itertuples(index=False):
        bikes = int(row.available_bikes or 0)
        docks = int(row.available_docks or 0)
        color = _station_color(bikes, docks)
        ebikes = getattr(row, "available_ebikes", None)
        if ebikes is None or pd.isna(ebikes):
            bike_text = f"可借: {bikes}（可能含電輔車）"
        else:
            bike_text = f"可借一般車: {bikes}<br>電輔車: {int(ebikes)}（不免費）"
        folium.CircleMarker(
            location=(row.lat, row.lon),
            radius=4,
            color=color,
            fill=True,
            fill_opacity=0.7,
            popup=folium.Popup(
                f"<b>{row.name}</b><br>"
                f"{bike_text}<br>可還: {docks}<br>"
                f"{row.city}",
                max_width=250,
            ),
        ).add_to(fg)
    fg.add_to(m)
    if show_legend:
        m.get_root().html.add_child(folium.Element(_LEGEND_HTML))
    return m


def stations_near_route(
    stations: pd.DataFrame,
    plan: RoutePlan,
    origin: tuple[float, float],
    destination: tuple[float, float],
    padding_km: float = 0.5,
) -> pd.DataFrame:
    """只留下路線（含起終點）外框附近的站，讓地圖更清爽、手機更順。"""
    if stations.empty:
        return stations
    lats = [origin[0], destination[0]]
    lons = [origin[1], destination[1]]
    if plan.feasible and plan.segments:
        coords = stations.set_index("station_id")[["lat", "lon"]]
        ids = {s.from_station_id for s in plan.segments} | {
            s.to_station_id for s in plan.segments
        }
        sub = coords.loc[coords.index.intersection(list(ids))]
        lats += sub["lat"].tolist()
        lons += sub["lon"].tolist()
    pad = padding_km / 111.0
    lat_lo, lat_hi = min(lats) - pad, max(lats) + pad
    lon_lo, lon_hi = min(lons) - pad * 1.1, max(lons) + pad * 1.1
    mask = stations["lat"].between(lat_lo, lat_hi) & stations["lon"].between(lon_lo, lon_hi)
    return stations[mask]


def view_for_route(
    plan: RoutePlan,
    stations: pd.DataFrame,
    origin: tuple[float, float],
    destination: tuple[float, float],
    map_px: tuple[int, int] = (480, 400),
) -> tuple[tuple[float, float], int]:
    """算出剛好容納整條路線（含起終點）的地圖中心與縮放等級。

    streamlit-folium 會忽略 folium 的 fit_bounds，所以改為直接算 zoom。
    map_px 是扣掉邊距後地圖可視範圍的保守估計（寬, 高 像素），手機上會比桌機窄。
    """
    pts = route_points(plan, stations, origin, destination)
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    center = ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
    # Web Mercator：zoom z 時全世界寬 256·2^z 像素（360 度經度）
    lon_span = max(max(lons) - min(lons), 1e-4)
    lat_span = max(max(lats) - min(lats), 1e-4) / math.cos(math.radians(center[0]))
    z_lon = math.log2(map_px[0] * 360 / (256 * lon_span))
    z_lat = math.log2(map_px[1] * 360 / (256 * lat_span))
    zoom = int(max(10, min(17, math.floor(min(z_lon, z_lat)))))
    return center, zoom


def draw_route(
    m: folium.Map,
    plan: RoutePlan,
    stations: pd.DataFrame,
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> folium.Map:
    """在地圖上繪製推薦路線、換車站、起終點。"""
    folium.Marker(
        location=origin,
        popup="起點",
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
    ).add_to(m)
    folium.Marker(
        location=destination,
        popup="終點",
        icon=folium.Icon(color="red", icon="flag-checkered", prefix="fa"),
    ).add_to(m)

    if not plan.feasible or not plan.segments:
        return m

    coords_by_id = stations.set_index("station_id")[["lat", "lon"]].to_dict("index")

    ride_segments = [s for s in plan.segments if s.mode == "ride"]
    ride_seen = 0
    swap_no = 0
    for seg in plan.segments:
        a = coords_by_id[seg.from_station_id]
        b = coords_by_id[seg.to_station_id]
        mid_lat = (a["lat"] + b["lat"]) / 2
        mid_lon = (a["lon"] + b["lon"]) / 2

        if seg.mode == "wait":
            # 同站續借等冷卻：原地標示時鐘
            folium.Marker(
                location=(a["lat"], a["lon"]),
                popup=folium.Popup(
                    f"<b>同站等冷卻</b><br>{seg.from_name}<br>約 {seg.minutes:.0f} 分",
                    max_width=250,
                ),
                icon=folium.Icon(color="orange", icon="clock-o", prefix="fa"),
            ).add_to(m)
            continue

        is_ride = seg.mode == "ride"
        color = "#1f4e8c" if is_ride else "#2e8b57"
        label = f"{seg.minutes:.0f} 分" if is_ride else f"步行 {seg.minutes:.0f} 分"
        folium.PolyLine(
            locations=[(a["lat"], a["lon"]), (b["lat"], b["lon"])],
            color=color, weight=5 if is_ride else 4, opacity=0.75,
            dash_array=None if is_ride else "6",
        ).add_to(m)
        folium.Marker(
            location=(mid_lat, mid_lon),
            icon=folium.DivIcon(
                icon_size=(90, 20),
                icon_anchor=(45, 10),
                html=(
                    f'<div style="font-size:11px;font-weight:bold;color:{color};'
                    f'background:rgba(255,255,255,0.85);border-radius:4px;'
                    f'padding:1px 4px;text-align:center;">{label}</div>'
                ),
            ),
        ).add_to(m)

        if is_ride:
            ride_seen += 1
            # 騎乘段抵達站若非最後一段騎乘 → 標示換車點
            if ride_seen < len(ride_segments):
                swap_no += 1
                folium.Marker(
                    location=(b["lat"], b["lon"]),
                    popup=folium.Popup(
                        f"<b>換車點 #{swap_no}</b><br>{seg.to_name}<br>"
                        f"上一段: {seg.minutes:.1f} 分 / {seg.distance_km:.2f} km",
                        max_width=250,
                    ),
                    icon=folium.Icon(color="blue", icon="exchange", prefix="fa"),
                ).add_to(m)

    return m
