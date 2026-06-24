"""取数解析器（配置与取数契约 §5）。
resolve(reg, key, filter, time|start/end) → 响应。
表分派 → 成熟度取值（人工固定读配置 / 规则生成调引擎 / 真值采集走 fallback）。"""
from datetime import datetime, timedelta
from .timegrid import parse_time, grid_minutes, align, enumerate_grid, to_min, EPOCH
from .generators import GENERATORS


def _origin_min(spec_rule, reg, dt: datetime) -> float:
    qi = (spec_rule or {}).get("起点", "当天起点")
    if qi == "当天起点":
        o = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif qi == "渔季起点":
        o = _season_start(reg, dt)
    elif qi == "年初":
        o = datetime(dt.year, 1, 1)
    else:
        o = EPOCH
    return to_min(o)


def _season_start(reg, dt: datetime) -> datetime:
    seasons = (reg.keys.get("渔季") or {}).get("value", [])
    for s in seasons:
        try:
            st = parse_time(s["开始"]); en = parse_time(s["结束"])
        except Exception:
            continue
        if en < st:                      # 跨年环绕
            if dt >= st or dt <= en:
                return st
        elif st <= dt <= en:
            return st
    if seasons:
        try:
            return parse_time(seasons[0]["开始"])
        except Exception:
            pass
    return EPOCH


def _gen_point(rule, key, dt, gmin, reg, spec):
    kind = rule.get("kind")
    fn = GENERATORS.get(kind)
    if not fn:
        return None
    t_min = to_min(align(dt, gmin))
    ctx = {"grid_min": gmin, "origin_min": _origin_min(rule, reg, dt), "dt": dt}
    return fn(rule, key, t_min, ctx)


def _check_override(reg, key, dt):
    for ov in reg.overrides:
        if ov.get("key") != key:
            continue
        rng = ov.get("range")
        if not rng or rng == ["*", "*"]:
            return ov.get("value")
        try:
            s, e = parse_time(rng[0]), parse_time(rng[1])
            if s <= dt <= e:
                return ov.get("value")
        except Exception:
            continue
    return None


def _match(entry_filter, query):
    """条目是否命中：query 的每个键在 entry.filter 中存在且相等。"""
    if not query:
        return True
    for k, v in query.items():
        if str(entry_filter.get(k)) != str(v):
            return False
    return True


def resolve(reg, key, filter=None, time=None, start=None, end=None):
    spec = reg.keys.get(key)
    if spec is None:
        return {"status": 404, "msg": f"key 未注册: {key}"}
    table = spec.get("表")

    # ---- A：单一站立 ----
    if table == "A":
        return {"status": 200, "key": key, "value": spec.get("value")}

    # ---- B：可选站立 ----
    if table == "B":
        out = []
        for ent in spec.get("条目", []):
            ef = ent.get("filter", {})
            if _match(ef, filter):
                out.append({"filter": ef, "value": ent.get("value")})
        return {"status": 200, "key": key, "data": out}

    # ---- C：时序 ----
    if table == "C":
        maturity = spec.get("成熟度")
        gmin = grid_minutes(spec.get("网格"))
        # 选规则：单 规则 / 指标[选中] / 真值采集 fallback
        rule = spec.get("规则")
        if rule is None and spec.get("fallback"):
            rule = spec["fallback"].get("规则") or spec["fallback"]
        # 指标多选
        指标 = spec.get("指标")
        dt_point = parse_time(time)
        rng = (parse_time(start), parse_time(end))

        def emit(r, k):
            if dt_point is not None or (rng[0] is None and rng[1] is None):
                dt = dt_point or datetime.now()
                ov = _check_override(reg, key, dt)
                val = ov if ov is not None else _gen_point(r, k, dt, gmin, reg, spec)
                return [{"time": align(dt, gmin).strftime("%Y-%m-%d %H:%M:%S"), "value": val}]
            pts = enumerate_grid(rng[0], rng[1], gmin)
            seq = []
            for dt in pts:
                ov = _check_override(reg, key, dt)
                val = ov if ov is not None else _gen_point(r, k, dt, gmin, reg, spec)
                seq.append({"time": dt.strftime("%Y-%m-%d %H:%M:%S"), "value": val})
            return seq

        if rule:                              # 单规则（含真值采集 fallback、航迹）
            note = "真值采集·当前用fallback规则" if maturity == "真值采集" else None
            resp = {"status": 200, "key": key, "单位": spec.get("单位"), "values": emit(rule, key)}
            if note:
                resp["note"] = note
            return resp
        if isinstance(指标, dict):            # 多指标：filter.指标 选取，否则全部
            want = (filter or {}).get("指标")
            data = {}
            for name, m in 指标.items():
                if want and name != want:
                    continue
                r = m.get("规则")
                if not r:                     # 占位/派生/引用项
                    data[name] = {"note": "派生/引用/占位，未直接生成"}
                    continue
                data[name] = {"单位": m.get("单位"), "values": emit(r, f"{key}.{name}")}
            return {"status": 200, "key": key, "data": data}
        return {"status": 200, "key": key, "note": "C 但无规则/指标（可能真值采集待对接）"}

    return {"status": 500, "msg": f"未知表: {table}"}
