"""网格对齐 / 区间枚举 / 时间语义（配置与取数契约 §5）。
简化版 cron 解析：仅支持本项目用到的几种网格。"""
import os
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

EPOCH = datetime(2026, 1, 1)            # 噪声/网格索引基准
FMT_DT = "%Y-%m-%d %H:%M:%S"
FMT_D = "%Y-%m-%d"

# —— 业务时区 ——
# 全项目的时间语义（网格对齐、累计起点、"当前时刻"）都按业务本地时区理解，
# 而非容器/宿主的系统时区（Docker slim 默认 UTC）。统一在此读取 APP_TZ。
# 默认 Asia/Shanghai（北京时间 UTC+8）；需 tzdata（见 requirements.txt）。
APP_TZ = os.getenv("APP_TZ", "Asia/Shanghai")
# 兜底偏移：tzdata 缺失/APP_TZ 非法时用它，保住"业务意图的固定偏移"，
# 而非退回系统时区（在 UTC 容器里会悄悄把要修的 8 小时差重新引入）。默认 +8（北京）。
_FALLBACK_OFFSET_HOURS = float(os.getenv("APP_TZ_FALLBACK_OFFSET_HOURS", "8"))


def now_local() -> datetime:
    """业务本地时区的"当前时刻"，返回 naive datetime（与全项目 naive 约定一致）。

    刻意去掉 tzinfo：本项目所有时间（EPOCH、parse_time、align）都是 naive 且按业务
    本地时区解释，混入 aware datetime 会在比较/相减处报错。这里先在 APP_TZ 取得带时区的
    当前时刻，再 strip tzinfo，得到"墙上时钟"意义的本地 naive 时间。"""
    try:
        tz = ZoneInfo(APP_TZ)
    except Exception:
        # tzdata 缺失或 APP_TZ 非法 → 用固定偏移兜底（默认 +8），并告警；
        # 绝不静默退回系统时区，否则 UTC 容器会重新出现 8 小时偏差。
        _log.warning("无法加载时区 APP_TZ=%r（tzdata 缺失或名称非法），"
                     "退回固定偏移 UTC%+g 小时。", APP_TZ, _FALLBACK_OFFSET_HOURS)
        tz = timezone(timedelta(hours=_FALLBACK_OFFSET_HOURS))
    return datetime.now(tz).replace(tzinfo=None)


def parse_time(s):
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s
    s = str(s).strip()
    for fmt in (FMT_DT, FMT_D):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间: {s}")


def grid_minutes(cron) -> int:
    """'*/3 * * * *'→3  '0 * * * *'→60  '0 0 * * *'→1440  默认 60。"""
    if not cron:
        return 60
    parts = str(cron).split()
    minute = parts[0] if parts else "*"
    hour = parts[1] if len(parts) > 1 else "*"
    if minute.startswith("*/"):
        return int(minute[2:])
    if minute.isdigit():
        if hour == "*":
            return 60
        return 1440           # 分、时都固定 → 每日
    return 60


def to_min(dt: datetime) -> float:
    return (dt - EPOCH).total_seconds() / 60.0


def align(dt: datetime, gmin: int) -> datetime:
    m = to_min(dt)
    aligned = (m // gmin) * gmin
    return EPOCH + timedelta(minutes=aligned)


DEFAULT_POINTS = 20            # 全局硬默认点数（配置/请求都不给时）
MAX_POINTS = 2000             # clamp 上限（兼内存护栏）


def grid_count(start: datetime, end: datetime, gmin: int) -> int:
    """[start,end] 内网格点数（算术，不枚举）。"""
    first = to_min(align(start, gmin))
    if first < to_min(start):
        first += gmin
    last = to_min(end)
    if first > last:
        return 0
    return int((last - first) // gmin) + 1


def bucket_samples(start: datetime, end: datetime, gmin: int, n,
                   量语义: str = "瞬时"):
    """固定点数 / 自适应比例尺（配置与取数契约 §5.1）：
    把 [start,end] 切成 N 桶，step=(end−start)/N 随跨度自动缩放；每桶取一个采样时刻——
      累计 → 桶右端（累计曲线 C(桶末)）；瞬时/离散 → 桶中心。
    采样时刻 align 到网格，保证落在确定性网格点上、与点查同值。
    若网格点数 ≤ N，直接逐网格点返回（小跨度不降采样，与历史行为一致）。"""
    n = max(1, min(int(n or DEFAULT_POINTS), MAX_POINTS))
    if grid_count(start, end, gmin) <= n:
        return enumerate_grid(start, end, gmin)
    span = to_min(end) - to_min(start)
    cumulative = (量语义 == "累计")
    out, last = [], None
    for i in range(n):
        frac = (i + 1) / n if cumulative else (i + 0.5) / n
        at = align(EPOCH + timedelta(minutes=to_min(start) + span * frac), gmin)
        if at != last:
            out.append(at)
            last = at
    return out


def enumerate_grid(start: datetime, end: datetime, gmin: int, cap: int = 2000):
    """枚举 [start,end] 内网格点；超过 cap 等间隔降采样（造数引擎降采样契约）。"""
    cur = align(start, gmin)
    pts = []
    while cur <= end and len(pts) < 200000:
        if cur >= start:
            pts.append(cur)
        cur = cur + timedelta(minutes=gmin)
    if len(pts) > cap:
        step = len(pts) / cap
        pts = [pts[int(i * step)] for i in range(cap)]
    return pts
