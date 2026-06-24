"""造数引擎：4 个 kind（造数引擎 §4.0）。
每个都是纯函数 gen(params, key, t_min, ctx) -> value。"""
import math
from .seed import fbm, rand_unit

_SEASON_PERIOD_MIN = {"年": 365 * 24 * 60, "月": 30 * 24 * 60, "日": 24 * 60}


def _season(p, t_min):
    s = p.get("季节")
    if not s:
        return 0.0
    amp = s.get("振幅", 0.0)
    period = _SEASON_PERIOD_MIN.get(s.get("周期", "年"), 365 * 24 * 60)
    phase = s.get("相位", 0.0)
    return amp * math.sin(2 * math.pi * (t_min / period) + phase)


def _clamp(v, rng):
    if not rng or len(rng) != 2:
        return v
    lo, hi = rng
    return max(lo, min(hi, v))


def 有界瞬时(p, key, t_min, ctx=None):
    base = p.get("基线", 0.0)
    amp = p.get("幅度", 0.0)
    bp = p.get("base_period", 360)
    octaves = p.get("octaves", 4)
    v = base + amp * fbm(key, t_min, bp, octaves) + _season(p, t_min)
    tr = p.get("趋势")
    if tr and tr.get("kind") == "线性":
        v += tr.get("斜率", 0.0) * (t_min / 1440.0)      # 斜率/天
    return round(_clamp(v, p.get("区间")), 3)


def 累计(p, key, t_min, ctx=None):
    ctx = ctx or {}
    origin = ctx.get("origin_min", 0.0)
    gmin = ctx.get("grid_min", 1.0)
    grids = max(0.0, (t_min - origin) / gmin)            # 起点→t 的网格数
    rate = p.get("速率基线", 0.0)
    drift_amp = p.get("漂移幅度", 0.0)
    bp = p.get("base_period", 1440)
    drift = drift_amp * fbm(key, t_min, bp, channel="drift")
    return round(max(0.0, rate * grids + drift), 3)      # 单调非负


def 离散状态(p, key, t_min, ctx=None):
    dwell = p.get("驻留分钟", 30)
    vals = p.get("取值", [])
    wts = p.get("权重") or [1] * len(vals)
    if not vals:
        return None
    block = int(t_min // dwell)
    u = (rand_unit(key, block, "state") + 1) / 2.0
    total = sum(wts)
    acc = 0.0
    for v, w in zip(vals, wts):
        acc += w / total
        if u <= acc:
            return v
    return vals[-1]


def 航迹(p, key, t_min, ctx=None):
    pts = p.get("航点", [])
    if not pts:
        return None
    drift = p.get("漂移", 0.05)
    bp = p.get("base_period", 720)
    delta = p.get("Δ", p.get("delta", 180))

    def pos(tm):
        n = len(pts)
        if n == 1:
            blon, blat = pts[0]["经度"], pts[0]["纬度"]
        else:
            span = 7 * 24 * 60.0                          # 一周走完航线（演示）
            frac = (tm % span) / span * (n - 1)
            i = int(frac)
            f = frac - i
            j = min(i + 1, n - 1)
            blon = pts[i]["经度"] + (pts[j]["经度"] - pts[i]["经度"]) * f
            blat = pts[i]["纬度"] + (pts[j]["纬度"] - pts[i]["纬度"]) * f
        return (blon + drift * fbm(key, tm, bp, channel="lon"),
                blat + drift * fbm(key, tm, bp, channel="lat"))

    lon, lat = pos(t_min)
    lon0, lat0 = pos(t_min - delta)
    R = 6371.0
    dlat = math.radians(lat - lat0)
    dlon = math.radians(lon - lon0)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat0)) * math.cos(math.radians(lat)) * math.sin(dlon / 2) ** 2
    dist_km = 2 * R * math.asin(min(1.0, math.sqrt(a)))
    speed_kn = dist_km / (delta / 60.0) / 1.852
    bearing = (math.degrees(math.atan2(
        math.sin(dlon) * math.cos(math.radians(lat)),
        math.cos(math.radians(lat0)) * math.sin(math.radians(lat)) -
        math.sin(math.radians(lat0)) * math.cos(math.radians(lat)) * math.cos(dlon))) + 360) % 360
    return {"经度": round(lon, 4), "纬度": round(lat, 4),
            "航速": round(speed_kn, 2), "航向": round(bearing, 1)}


GENERATORS = {
    "有界瞬时": 有界瞬时,
    "累计": 累计,
    "离散状态": 离散状态,
    "航迹": 航迹,
}
