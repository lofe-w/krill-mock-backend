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
