"""种子化纯函数 + 相干噪声（造数引擎 §2-§3）。
值只依赖 (key, 网格时间)，与 now/调用次序无关 → 可复现。"""
import hashlib
import math


def rand_unit(key: str, index: int, channel: str = "") -> float:
    """确定性 [-1,1)：同输入永远同输出。"""
    h = hashlib.sha256(f"{key}|{channel}|{index}".encode("utf-8")).digest()
    u = int.from_bytes(h[:8], "big") / 2 ** 64
    return 2.0 * u - 1.0


def smoothstep(x: float) -> float:
    return x * x * (3.0 - 2.0 * x)


def coherent_noise(key: str, t_min: float, period_min: float, channel: str = "") -> float:
    """锚点 + smoothstep 插值 → 平滑 [-1,1] 噪声。O(1)。"""
    if period_min <= 0:
        period_min = 1.0
    pos = t_min / period_min
    i = math.floor(pos)
    frac = pos - i
    a = rand_unit(key, i, channel)
    b = rand_unit(key, i + 1, channel)
    return a + (b - a) * smoothstep(frac)


def fbm(key: str, t_min: float, base_period: float = 360.0, octaves: int = 4,
        lacunarity: float = 2.0, gain: float = 0.5, channel: str = "") -> float:
    """分形叠加，归一到 [-1,1]。"""
    total = 0.0
    amp = 1.0
    period = float(base_period)
    norm = 0.0
    for o in range(int(octaves)):
        total += amp * coherent_noise(key, t_min, period, f"{channel}#oct{o}")
        norm += amp
        amp *= gain
        period /= lacunarity
    return total / norm if norm else 0.0
