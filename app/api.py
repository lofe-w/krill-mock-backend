"""对外接口 = 三表的外部投影，每表一个、各自批量（domain §8）。
使用侧（前端）：/api/value /api/records /api/series —— 需 使用侧 Token。
运维侧（运维）：/api/keys /api/reload —— 需 运维侧 Token。/api/health 公开(存活探测)。
鉴权：.env 写死两个 Token，前端/运维以 Authorization: Bearer <token> 传入。
CORS：浏览器跨域调用需开启（见 CORS_ORIGINS）。"""
import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, List
try:
    from dotenv import load_dotenv
    load_dotenv()                       # 本机运行时加载 .env；docker 用 env_file 注入
except Exception:
    pass
from .registry import load, selfcheck, compatibility_warnings, contract_meta
from .resolver import resolve
from .timegrid import parse_time

# —— 鉴权配置（来自环境变量）——
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
TOKEN_APP = os.getenv("API_TOKEN_APP", "")      # 使用侧（前端）
TOKEN_OPS = os.getenv("API_TOKEN_OPS", "")      # 运维侧
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(title="krill-mock-backend", version="0.3-auth-cors")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
REG = load()


# HTTPBearer 安全方案：让 Swagger /docs 出现「Authorize 🔓」按钮，
# 填一次 token 后所有受保护接口自动带 Authorization 头。
# auto_error=False：自行返回 401 + 中文提示，并兼容 AUTH_ENABLED=false。
_bearer_scheme = HTTPBearer(auto_error=False, description="TOKEN")


def require_app(cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)):
    """使用侧：使用侧 Token 或运维侧 Token 均可（运维侧权限更高）。"""
    if not AUTH_ENABLED:
        return
    tok = cred.credentials.strip() if cred and cred.credentials else None
    if tok and tok in (TOKEN_APP, TOKEN_OPS) and tok != "":
        return
    raise HTTPException(status_code=401, detail="缺少或无效的使用侧 Token（Authorization: Bearer <API_TOKEN_APP>）")


def require_ops(cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)):
    """运维侧：仅运维侧 Token。"""
    if not AUTH_ENABLED:
        return
    tok = cred.credentials.strip() if cred and cred.credentials else None
    if tok and tok == TOKEN_OPS and tok != "":
        return
    raise HTTPException(status_code=401, detail="缺少或无效的运维侧 Token（Authorization: Bearer <API_TOKEN_OPS>）")


# —— 请求体 ——
# 各请求体挂 json_schema_extra.example：用真实 key 名替换 Swagger 对「动态 key 的 map」
# 默认渲染的 additionalProp1/2，使 /docs 的示例即一条可直接联调的真请求。
class ValueQ(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"keys": ["船舶.信息", "工厂.虾油提取生产线.设计能力"]}})
    keys: List[str]


class RecordsQ(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"keys": ["溯源"],
                    "filter": {"溯源": {"产品批号": "2606AKO01"}}}})
    keys: List[str]
    filter: Optional[Dict[str, Any]] = None


class SeriesQ(BaseModel):
    # 三端点同构：keys + 一个按 key 作用域的 map。C 的轴是「时间」，故名 window（非 filter）。
    # window 支持两种形态：
    #   1) 全局：{ "start"?, "end"?, "points"? }，应用到本次 keys 展开的全部 C key。
    #   2) 逐 key：{ "<key>": { "start"?, "end"?, "points"? } }，每个 key 独立配置。
    model_config = ConfigDict(json_schema_extra={"example": {
        "keys": ["船舶.海况.海水温度", "船舶.能耗.累计耗油量", "船舶.航行"],
        "window": {
            "船舶.海况.海水温度": {"start": "2026-01-01 00:00:00",
                                "end": "2026-12-31 00:00:00", "points": 12},
            "船舶.能耗.累计耗油量": {"start": "2026-01-01 00:00:00",
                                "end": "2026-06-25 00:00:00"},
        }}})
    keys: List[str]
    window: Optional[Dict[str, Any]] = None


def _filter_for(filter: Optional[Dict], key: str):
    """B 表过滤：filter 按 key 作用域，形如 {key: {字段: 值}}（domain §8 批量语义）。
    形状由 _validate_records_filter 保证，这里直接取本 key 的过滤条件。"""
    return (filter or {}).get(key)


def _same_table_descendants(key: str, want_table: str):
    """返回同表后代叶子 key。

    key 可以是任意路径前缀。比如请求 `船舶` 到 /api/series 时，
    只展开 C 表下 `船舶.*` 的叶子 key，不混入 A/B。
    """
    candidates = [k for k in REG.keys if k.startswith(key + ".")]
    return [k for k in candidates
            if (REG.keys.get(k) or {}).get("表") == want_table]


def _expand_for_table(key: str, want_table: str):
    """把请求 key 展开为本接口应返回的实际 key 列表。

    - 精确叶子 key：返回自身，保持旧调用兼容。
    - 父系前缀：返回所有同表后代 key。
    - 找不到：返回原 key，让后续 _wrap 给出原有错误形状。
    """
    spec = REG.keys.get(key)
    if spec and spec.get("表") == want_table:
        return [key]
    descendants = _same_table_descendants(key, want_table)
    if descendants:
        return descendants
    return [key]


def _expanded_request_pairs(keys: List[str], want_table: str):
    """保留请求 key 与展开后 key 的对应关系，供 window/filter 继承使用。"""
    out = []
    seen = set()
    for requested in keys:
        for actual in _expand_for_table(requested, want_table):
            ident = (requested, actual)
            if ident in seen:
                continue
            seen.add(ident)
            out.append((requested, actual))
    return out


def _validate_records_filter(filter: Optional[Dict]):
    """/api/records 是批量接口（keys:[...]）+ 单个共享 filter，故 filter 必须按 key 作用域：
    {key: {字段: 值}}。每个顶层项都得是已注册 key、值是该 key 的过滤条件对象；
    否则 400，避免无作用域的过滤条件被静默套到所有 key（或退化成不过滤、返回全部）。"""
    if not filter:
        return
    bad = []
    for k, v in filter.items():
        spec = REG.keys.get(k)
        is_b_key = spec is not None and spec.get("表") == "B"
        is_b_prefix = bool(_same_table_descendants(k, "B"))
        if not isinstance(v, dict) or not (is_b_key or is_b_prefix):
            bad.append(k)
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(f"filter 须按 key 作用域，形如 {{\"<key>\": {{字段: 值}}}}；"
                    f"以下顶层项不是已注册 B 表 key/B 表父系前缀，或值非对象：{bad}。"))


_WINDOW_FIELDS = {"start", "end", "points"}


def _entry(e):
    """window 条目归一为 {start,end,points} dict。"""
    if isinstance(e, dict):
        return e
    if hasattr(e, "model_dump"):
        return e.model_dump()
    return {f: getattr(e, f, None) for f in _WINDOW_FIELDS}


def _window_mode(window: Optional[Dict[str, Any]]):
    """识别 window 形态：none / global / per_key；混用全局字段与 key 字段直接 400。"""
    if not window:
        return "none"
    if not isinstance(window, dict):
        raise HTTPException(status_code=400, detail="window 须为对象。")
    fields = set(window)
    global_fields = fields & _WINDOW_FIELDS
    if global_fields:
        if fields <= _WINDOW_FIELDS:
            return "global"
        raise HTTPException(
            status_code=400,
            detail="window 不能混用全局字段(start/end/points)和逐 key 配置；请二选一。")
    return "per_key"


def _validate_window_entry(scope: str, raw):
    """校验单个 window 条目，scope 用于错误提示。"""
    if not isinstance(raw, dict) and not hasattr(raw, "model_dump"):
        raise HTTPException(status_code=400, detail=f"{scope} 须为对象，且仅含 {_WINDOW_FIELDS}。")
    if isinstance(raw, dict):
        extra = set(raw) - _WINDOW_FIELDS
        if extra:
            raise HTTPException(status_code=400, detail=f"{scope} 含非法字段 {extra}；仅许 {_WINDOW_FIELDS}。")
    e = _entry(raw)
    s, en = e.get("start"), e.get("end")
    if (s is None) != (en is None):
        raise HTTPException(status_code=400,
                            detail=f"{scope} 的 start/end 须成对：都不传=当前时刻；相等=单点；end>start=区间。")
    if s is not None:
        try:
            sp, ep = parse_time(s), parse_time(en)
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex))
        if ep < sp:
            raise HTTPException(status_code=400, detail=f"{scope} end 不得早于 start：{s}~{en}。")
    p = e.get("points")
    if p is not None and (not isinstance(p, int) or isinstance(p, bool) or p <= 0):
        raise HTTPException(status_code=400, detail=f"{scope} points 须为正整数：{p}。")


def _validate_series_window(window: Optional[Dict], keys: List[str]):
    """/api/series 与 /records 同构：keys + 按 key 作用域的 map（这里是 window）。
    支持两种形态：
      ① 全局 window: {start?, end?, points?}，作用于全部展开后的 C key；
      ② 逐 key window: {key: {start?, end?, points?}}，key 须在 keys 内且是 C 表 key/父系前缀。
    两种形态不能混用。"""
    mode = _window_mode(window)
    if mode == "none":
        return
    if mode == "global":
        _validate_window_entry("window", window)
        return
    for k, raw in window.items():
        if k not in keys:
            raise HTTPException(status_code=400, detail=f"window 的 key 不在 keys 内：{k}。")
        if not _expand_for_table(k, "C") or (
            _expand_for_table(k, "C") == [k]
            and ((REG.keys.get(k) or {}).get("表") != "C")
        ):
            raise HTTPException(status_code=400, detail=f"window 仅作用于 C 表 key 或 C 表父系前缀：{k}。")
        _validate_window_entry(f"window[{k}]", raw)


def _window_for(window: Optional[Dict[str, Any]], requested: str, actual: str):
    """取实际 key 的 window：全局形态直接复用；逐 key 形态按请求 key/展开后 key 继承。"""
    if _window_mode(window) == "global":
        return _entry(window)
    return _entry((window or {}).get(requested) or (window or {}).get(actual) or {})


# 表 → 对外接口（与 _wrap 的 A→/value、B→/records、C→/series 一致）
_接口_BY_表 = {"A": "/api/value", "B": "/api/records", "C": "/api/series"}


def _wrap(key, r, want_table):
    if r.get("status") == 404:
        return {"error": "key 未注册"}
    if r.get("表") and r["表"] != want_table:
        return {"error": f"{key} 属 {r['表']} 表，请用对应接口（A→/value, B→/records, C→/series）"}
    return r


def _warnings_for(keys):
    out = []
    seen = set()
    for k in keys:
        for w in compatibility_warnings(REG, k):
            ident = (w.get("type"), w.get("key"), w.get("replaced_by"))
            if ident in seen:
                continue
            seen.add(ident)
            out.append(w)
    return out


def _response(data, keys):
    resp = {"status": 200, "data": data}
    warnings = _warnings_for(keys)
    if warnings:
        resp["warnings"] = warnings
    return resp


def _series_payload(r):
    payload = {"显示": r.get("显示"), "单位": r.get("单位"), "values": r.get("values", [])}
    if "派生" in r:
        payload["派生"] = r["派生"]
    return payload


# —— 使用侧（前端）——
@app.post("/api/value", dependencies=[Depends(require_app)])
def api_value(q: ValueQ):
    data = {}
    for requested, k in _expanded_request_pairs(q.keys, "A"):
        r = _wrap(k, resolve(REG, k), "A")
        data[k] = r if "error" in r else r.get("value")
    return _response(data, q.keys)


@app.post("/api/records", dependencies=[Depends(require_app)])
def api_records(q: RecordsQ):
    _validate_records_filter(q.filter)
    data = {}
    for requested, k in _expanded_request_pairs(q.keys, "B"):
        r = _wrap(k, resolve(REG, k, filter=_filter_for(q.filter, requested) or _filter_for(q.filter, k)), "B")
        data[k] = r if "error" in r else r.get("data")
    return _response(data, q.keys)


@app.post("/api/series", dependencies=[Depends(require_app)])
def api_series(q: SeriesQ):
    _validate_series_window(q.window, q.keys)
    data = {}
    for requested, k in _expanded_request_pairs(q.keys, "C"):
        e = _window_for(q.window, requested, k)
        r = _wrap(k, resolve(REG, k, start=e.get("start"), end=e.get("end"),
                             points=e.get("points")), "C")
        if "error" in r:
            data[k] = r
        elif "values" in r:
            data[k] = _series_payload(r)
        else:
            data[k] = r              # note / 占位项
    return _response(data, q.keys)


# —— 公开：存活探测 ——
@app.get("/api/health")
def health():
    rep = selfcheck(REG)
    return {"status": 200, "keys": rep["key_count"], "by_table": rep["by_table"],
            "by_maturity": rep["by_maturity"], "selfcheck_ok": rep["ok"],
            "unresolved": rep["unresolved"], "derivations": len(REG.derivations),
            "auth_enabled": AUTH_ENABLED}


# —— 运维侧 ——
@app.get("/api/keys", dependencies=[Depends(require_ops)])
def list_keys():
    return {"status": 200, "data": [{"key": k, "表": s.get("表"),
                                     "接口": _接口_BY_表.get(s.get("表")),
                                     "成熟度": s.get("成熟度"),
                                     **contract_meta(s)}
                                    for k, s in REG.keys.items()]}


@app.post("/api/reload", dependencies=[Depends(require_ops)])
def reload_registry():
    global REG
    REG = load()
    rep = selfcheck(REG)
    return {"status": 200, "reloaded": True, "keys": rep["key_count"],
            "selfcheck_ok": rep["ok"], "unresolved": rep["unresolved"]}
