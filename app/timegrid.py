"""网格对齐 / 区间枚举 / 时间语义（配置与取数契约 §5）。
简化版 cron 解析：仅支持本项目用到的几种网格。"""
from datetime import datetime, timedelta

EPOCH = datetime(2026, 1, 1)            # 噪声/网格索引基准
FMT_DT = "%Y-%m-%d %H:%M:%S"
FMT_D = "%Y-%m-%d"


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
