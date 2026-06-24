"""取数解析器（配置与取数契约 §5）。
核心 resolve() 按表分派；派生(得率/百分比)经 constraints 表达式求值。
对外接口（api.py）拆为每表一个：/api/value(A) /api/records(B) /api/series(C)。"""
import ast
import operator
import re
from datetime import datetime
from .timegrid import parse_time, grid_minutes, align, enumerate_grid, to_min, EPOCH
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


def _season_start(reg, dt):
    for s in (reg.keys.get("渔季") or {}).get("value", []):
        try:
            st, en = parse_time(s["开始"]), parse_time(s["结束"])
        except Exception:
            continue
        if en < st:
            if dt >= st or dt <= en:
                return st
        elif st <= dt <= en:
            return st
    seasons = (reg.keys.get("渔季") or {}).get("value", [])
    if seasons:
        try:
            return parse_time(seasons[0]["开始"])
        except Exception:
            pass
    return EPOCH


def _rule_of(spec, 指标=None):
    """取一个 C 条目的规则：单 规则 / fallback / 指标[选中]。"""
    rule = spec.get("规则")
    if rule is None and spec.get("fallback"):
        fb = spec["fallback"]
        rule = fb.get("规则") or fb
    if rule is None and 指标 and isinstance(spec.get("指标"), dict):
        m = spec["指标"].get(指标) or {}
        rule = m.get("规则")
    return rule


def _gen_point(rule, key, dt, gmin, reg):
    fn = GENERATORS.get((rule or {}).get("kind"))
    if not fn:
        return None
    t_min = to_min(align(dt, gmin))
    ctx = {"grid_min": gmin, "origin_min": _origin_min(rule, reg, dt), "dt": dt}
    return fn(rule, key, t_min, ctx)


def _c_point(reg, key, dt, 指标=None):
    """直接算一个 C 规则点（供派生/引用取标量；不递归派生）。"""
    spec = reg.keys.get(key)
    if not spec or spec.get("表") != "C":
        return None
    gmin = grid_minutes(spec.get("网格"))
    rule = _rule_of(spec, 指标)
    if not rule:
        return None
    name = key if not 指标 else f"{key}.{指标}"
    return _gen_point(rule, name, dt, gmin, reg)


def _resolve_ident(reg, parent_key, idn, dt):
    """派生表达式里的标识符 → 数值：先按全限定 key，再按父 key 的兄弟指标。"""
    best = None
    for k in reg.keys:
        if idn == k or idn.startswith(k + "."):
            if best is None or len(k) > len(best):
                best = k
    if best:
        sub = idn[len(best) + 1:] if idn != best else None
        return _c_point(reg, best, dt, 指标=sub)
    return _c_point(reg, parent_key, dt, 指标=idn)   # 兄弟指标


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
def _emit(reg, key, value_fn, dt_point, rng, gmin):
    def one(dt):
        ov = _check_override(reg, key, dt)
        val = ov if ov is not None else value_fn(dt)
        return {"time": align(dt, gmin).strftime("%Y-%m-%d %H:%M:%S"), "value": val}
    if dt_point is not None or (rng[0] is None and rng[1] is None):
        return [one(dt_point or datetime.now())]
    return [one(dt) for dt in enumerate_grid(rng[0], rng[1], gmin)]


def resolve(reg, key, filter=None, time=None, start=None, end=None):
    spec = reg.keys.get(key)
    if spec is None:
        return {"status": 404, "key": key, "msg": f"key 未注册: {key}"}
    table = spec.get("表")

    if table == "A":
        return {"status": 200, "key": key, "表": "A", "value": spec.get("value")}

    if table == "B":
        out = [{"filter": e.get("filter", {}), "value": e.get("value")}
               for e in spec.get("条目", []) if _match(e.get("filter", {}), filter)]
        return {"status": 200, "key": key, "表": "B", "data": out}

    if table == "C":
        gmin = grid_minutes(spec.get("网格"))
        dt_point = parse_time(time)
        rng = (parse_time(start), parse_time(end))
        maturity = spec.get("成熟度")

        # 单规则 / 单派生（标量型 C，如 剩余燃油百分比）
        rule = _rule_of(spec)
        if rule:
            vf = lambda dt: _gen_point(rule, key, dt, gmin, reg)
            resp = {"status": 200, "key": key, "表": "C", "单位": spec.get("单位"),
                    "values": _emit(reg, key, vf, dt_point, rng, gmin)}
            if maturity == "真值采集":
                resp["note"] = "真值采集·当前用 fallback 规则（待真实源就绪切换）"
            return resp
        if spec.get("派生"):
            expr = reg.derivations.get(key) or spec.get("派生")
            vf = lambda dt: eval_derived(reg, key, expr, dt)
            return {"status": 200, "key": key, "表": "C", "单位": spec.get("单位"),
                    "派生": expr, "values": _emit(reg, key, vf, dt_point, rng, gmin)}

        # 多指标：filter.指标 选取（str / list 均可），否则全部
        指标 = spec.get("指标")
        if isinstance(指标, dict):
            want = (filter or {}).get("指标")
            if isinstance(want, str):
                wset = {want}
            elif isinstance(want, (list, tuple, set)):
                wset = set(want)
            else:
                wset = None
            data = {}
            for name, m in 指标.items():
                if wset and name not in wset:
                    continue
                fk = f"{key}.{name}"
                r = m.get("规则")
                if r:
                    vf = lambda dt, r=r, fk=fk: _gen_point(r, fk, dt, gmin, reg)
                    data[name] = {"单位": m.get("单位"), "values": _emit(reg, key, vf, dt_point, rng, gmin)}
                elif m.get("派生") or fk in reg.derivations:
                    expr = reg.derivations.get(fk) or m.get("派生")
                    rngc = m.get("区间")
                    vf = lambda dt, e=expr, rc=rngc: eval_derived(reg, key, e, dt, rc)
                    data[name] = {"单位": m.get("单位"), "派生": expr,
                                  "values": _emit(reg, key, vf, dt_point, rng, gmin)}
                else:
                    data[name] = {"note": "占位/说明项，未配置规则"}
            return {"status": 200, "key": key, "表": "C", "data": data}

        return {"status": 200, "key": key, "表": "C", "note": "C 但无规则/指标（真值采集待对接）"}

    return {"status": 500, "key": key, "msg": f"未知表: {table}"}
