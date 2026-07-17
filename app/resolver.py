"""取数解析器（配置与取数契约 §5）。
核心 resolve() 按表分派；派生(得率/百分比)经 constraints 表达式求值。
对外接口（api.py）拆为每表一个：/api/value(A) /api/records(B) /api/series(C)。"""
import ast
import operator
import re
from datetime import datetime
from .timegrid import (parse_time, grid_minutes, align, bucket_samples,
                       to_min, EPOCH, DEFAULT_POINTS, MAX_POINTS, now_local)
from .generators import GENERATORS

# —— 安全算术求值（仅 + - * / ** 和括号，无名字/调用）——
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg, ast.Pow: operator.pow}
_IDENT = re.compile(r"[一-鿿A-Za-z_][一-鿿A-Za-z0-9_.]*")


def _safe_arith(expr: str):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.BinOp):
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp):
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("非法表达式")
    return ev(ast.parse(expr, mode="eval"))


# —— 时间起点（累计型）——
def _origin_min(rule, reg, dt):
    qi = (rule or {}).get("起点", "当天起点")
    if qi == "当天起点":
        o = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif qi == "渔季起点":
        o = _season_start(reg, dt)
    elif qi == "年初":
        o = datetime(dt.year, 1, 1)
    else:
        o = EPOCH
    return to_min(o)


def _current_season(reg, dt):
    """按当前时间选出所处渔季（单个 {名称,开始,结束}）。
    命中返回该渔季；未命中则回退到「最近一个已开始」的渔季，再兜底首个。
    渔季边界为 date（naive），与 now_local() 的 aware dt 比较前先去掉时区。"""
    if dt is None:
        dt = now_local()
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    seasons = (reg.keys.get("渔季") or {}).get("value", []) or []
    best_past, best_past_st = None, None
    for s in seasons:
        try:
            st, en = parse_time(s["开始"]), parse_time(s["结束"])
        except Exception:
            continue
        if st is None or en is None:
            continue
        if en < st:                       # 跨年环绕边界
            if dt >= st or dt <= en:
                return s
        elif st <= dt <= en:
            return s
        if st <= dt and (best_past_st is None or st > best_past_st):
            best_past, best_past_st = s, st
    return best_past or (seasons[0] if seasons else None)


def _season_start(reg, dt):
    s = _current_season(reg, dt)
    if s:
        try:
            st = parse_time(s.get("开始"))
            if st is not None:
                return st
        except Exception:
            pass
    return EPOCH


def _rule_of(spec):
    """取一个 C 条目的规则：单 规则 / fallback。"""
    rule = spec.get("规则")
    if rule is None and spec.get("fallback"):
        fb = spec["fallback"]
        rule = fb.get("规则") or fb
    return rule


def _gen_point(rule, key, dt, gmin, reg):
    fn = GENERATORS.get((rule or {}).get("kind"))
    if not fn:
        return None
    t_min = to_min(align(dt, gmin))
    ctx = {"grid_min": gmin, "origin_min": _origin_min(rule, reg, dt), "dt": dt}
    return fn(rule, key, t_min, ctx)


def _c_point(reg, key, dt):
    """直接算一个 C 规则点（供派生/引用取标量；不递归派生）。"""
    spec = reg.keys.get(key)
    if not spec or spec.get("表") != "C":
        return None
    gmin = grid_minutes(spec.get("网格"))
    rule = _rule_of(spec)
    if not rule:
        return None
    return _gen_point(rule, key, dt, gmin, reg)


def _resolve_ident(reg, derived_key, idn, dt):
    """派生表达式里的标识符 → 数值：①全限定 key；②按父系前缀查兄弟项。"""
    if idn in reg.keys:
        return _c_point(reg, idn, dt)
    parts = derived_key.split(".")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:i] + [idn])
        if candidate in reg.keys:
            return _c_point(reg, candidate, dt)
    return None


def eval_derived(reg, parent_key, expr, dt, rng=None):
    """求派生值：替换标识符为数值后安全算术求值。"""
    if not expr:
        return None
    vals = {}
    for idn in sorted(set(_IDENT.findall(expr)), key=len, reverse=True):
        v = _resolve_ident(reg, parent_key, idn, dt)
        if v is None:
            return None
        vals[idn] = v
    expr2 = _IDENT.sub(lambda m: f"({vals.get(m.group(0), m.group(0))})", expr)
    try:
        out = _safe_arith(expr2)
    except Exception:
        return None
    if rng and len(rng) == 2 and out is not None:
        out = max(rng[0], min(rng[1], out))
    return round(out, 3) if isinstance(out, (int, float)) else out


# —— override / B 命中 ——
def _check_override(reg, key, dt):
    for ov in reg.overrides:
        if ov.get("key") != key:
            continue
        rng = ov.get("range")
        if not rng or rng == ["*", "*"]:
            return ov.get("value")
        try:
            if parse_time(rng[0]) <= dt <= parse_time(rng[1]):
                return ov.get("value")
        except Exception:
            continue
    return None


def _match(entry_filter, query):
    if not query:
        return True
    for k, v in query.items():
        if str(entry_filter.get(k)) != str(v):
            return False
    return True


# —— 时序发射器：给一个 value_fn(dt)->值，按点查/区间产出 ——
# 区间走固定点数分桶（§5.1）：N 桶、step 自适应、每桶按 量语义 取采样时刻。
def _emit(reg, key, value_fn, dt_point, rng, gmin, n=None, 量语义=None):
    def one(dt):
        ov = _check_override(reg, key, dt)
        val = ov if ov is not None else value_fn(dt)
        return {"time": align(dt, gmin).strftime("%Y-%m-%d %H:%M:%S"), "value": val}
    if dt_point is not None or (rng[0] is None and rng[1] is None):
        return [one(dt_point or now_local())]   # "当前时刻"按业务时区，非容器 UTC
    return [one(dt) for dt in bucket_samples(rng[0], rng[1], gmin, n, 量语义 or "瞬时")]


def _resolve_points(points):
    """N = 请求 points ?? 全局默认；clamp 到 [1, MAX_POINTS]。"""
    n = points if points is not None else DEFAULT_POINTS
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_POINTS
    return max(1, min(n, MAX_POINTS))


def _follow_alias(reg, key):
    """alias_of 兼容旧 key：旧 key 可透明解析到新 key，避免前端联调期突然 404。"""
    seen = []
    cur = key
    while True:
        if cur in seen:
            return cur, None, f"alias_of 存在循环: {' -> '.join(seen + [cur])}"
        seen.append(cur)
        spec = reg.keys.get(cur)
        if spec is None:
            return cur, None, None
        nxt = spec.get("alias_of")
        if not nxt:
            return cur, spec, None
        cur = nxt


def resolve(reg, key, filter=None, start=None, end=None, points=None):
    key, spec, alias_error = _follow_alias(reg, key)
    if alias_error:
        return {"status": 404, "key": key, "msg": alias_error}
    if spec is None:
        return {"status": 404, "key": key, "msg": f"key 未注册: {key}"}
    table = spec.get("表")

    if table == "A":
        # 渔季：按当前时间返回「当前渔季」单对象（而非整张区间列表）。
        if key == "渔季":
            return {"status": 200, "key": key, "表": "A",
                    "value": _current_season(reg, now_local())}
        return {"status": 200, "key": key, "表": "A", "value": spec.get("value")}

    if table == "B":
        out = [{"filter": e.get("filter", {}), "value": e.get("value")}
               for e in spec.get("条目", []) if _match(e.get("filter", {}), filter)]
        return {"status": 200, "key": key, "表": "B", "data": out}

    if table == "C":
        gmin = grid_minutes(spec.get("网格"))
        量语义 = spec.get("量语义")
        n = _resolve_points(points)
        # 时间模式由 start/end 表达（无独立 time 参数）：
        #   都不传 → 当前时刻单点；start==end → 该时刻单点；end>start → 区间。
        s, e = parse_time(start), parse_time(end)
        if s is None and e is None:
            dt_point, rng = None, (None, None)          # 当前时刻
        elif s == e:
            dt_point, rng = s, (None, None)             # 单点（对齐后发射，避免区间枚举落空）
        else:
            dt_point, rng = None, (s, e)                # 区间
        maturity = spec.get("成熟度")

        # 单规则（标量型 C / 航迹复合 value）
        rule = _rule_of(spec)
        if rule:
            vf = lambda dt: _gen_point(rule, key, dt, gmin, reg)
            resp = {"status": 200, "key": key, "表": "C", "显示": spec.get("显示"),
                    "单位": spec.get("单位"),
                    "values": _emit(reg, key, vf, dt_point, rng, gmin, n, 量语义)}
            if maturity == "真值采集":
                resp["note"] = "真值采集·当前用 fallback 规则（待真实源就绪切换）"
            return resp
        # 派生（得率/百分比；表达式标识符按全限定 key 或同组兄弟项解析）
        if spec.get("派生") or key in reg.derivations:
            expr = reg.derivations.get(key) or spec.get("派生")
            rngc = spec.get("区间")
            vf = lambda dt: eval_derived(reg, key, expr, dt, rngc)
            return {"status": 200, "key": key, "表": "C", "显示": spec.get("显示"),
                    "单位": spec.get("单位"),
                    "派生": expr, "values": _emit(reg, key, vf, dt_point, rng, gmin, n, 量语义)}

        return {"status": 200, "key": key, "表": "C",
                "note": "占位项，未配置规则（或真值采集待对接）"}

    return {"status": 500, "key": key, "msg": f"未知表: {table}"}
