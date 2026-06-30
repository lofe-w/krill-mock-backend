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
from .registry import load, selfcheck
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
        "example": {"keys": ["船舶.信息", "工厂.虾油线.设计能力"]}})
    keys: List[str]


class RecordsQ(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"keys": ["溯源"],
                    "filter": {"溯源": {"产品批号": "2606AKO01"}}}})
    keys: List[str]
    filter: Optional[Dict[str, Any]] = None


class WindowEntry(BaseModel):
    """C·series 逐 key 的时间窗 + 采样点数。三者皆可选、逐条目自洽（见 _validate_series_window）。
    用具名模型而非裸 dict，使 OpenAPI/Swagger 正确展示 start/end/points 字段。"""
    model_config = ConfigDict(extra="forbid")   # 多余字段 → 422（避免静默放行）
    start: Optional[str] = None                  # "YYYY-MM-DD HH:mm:ss"；与 end 成对
    end: Optional[str] = None
    points: Optional[int] = None                 # 区间重采样点数；缺省→配置 默认点数→全局 20


class SeriesQ(BaseModel):
    # 三端点同构：keys + 一个按 key 作用域的 map。C 的轴是「时间」，故名 window（非 filter）。
    # window: { "<key>": { "start"?, "end"?, "points"? } } —— 逐 key 自洽；缺省即回退。
    model_config = ConfigDict(json_schema_extra={"example": {
        "keys": ["船舶.海况.海水温度", "船舶.能耗.累计耗油量", "船舶.航行"],
        "window": {
            "船舶.海况.海水温度": {"start": "2026-01-01 00:00:00",
                                "end": "2026-12-31 00:00:00", "points": 12},
            "船舶.能耗.累计耗油量": {"start": "2026-01-01 00:00:00",
                                "end": "2026-06-25 00:00:00"},
        }}})
    keys: List[str]
    window: Optional[Dict[str, WindowEntry]] = None


def _filter_for(filter: Optional[Dict], key: str):
    """B 表过滤：filter 按 key 作用域，形如 {key: {字段: 值}}（domain §8 批量语义）。
    形状由 _validate_records_filter 保证，这里直接取本 key 的过滤条件。"""
    return (filter or {}).get(key)


def _validate_records_filter(filter: Optional[Dict]):
    """/api/records 是批量接口（keys:[...]）+ 单个共享 filter，故 filter 必须按 key 作用域：
    {key: {字段: 值}}。每个顶层项都得是已注册 key、值是该 key 的过滤条件对象；
    否则 400，避免无作用域的过滤条件被静默套到所有 key（或退化成不过滤、返回全部）。"""
    if not filter:
        return
    bad = [k for k in filter if k not in REG.keys or not isinstance(filter[k], dict)]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(f"filter 须按 key 作用域，形如 {{\"<key>\": {{字段: 值}}}}；"
                    f"以下顶层项不是已注册 key 或值非对象：{bad}。"))


_WINDOW_FIELDS = {"start", "end", "points"}


def _entry(e):
    """window 条目归一为 {start,end,points} dict：兼容 pydantic WindowEntry 与裸 dict。"""
    if isinstance(e, dict):
        return e
    if hasattr(e, "model_dump"):
        return e.model_dump()
    return {f: getattr(e, f, None) for f in _WINDOW_FIELDS}


def _validate_series_window(window: Optional[Dict], keys: List[str]):
    """/api/series 与 /records 同构：keys + 按 key 作用域的 map（这里是 window）。
    window: {key: {start?, end?, points?}}。逐条目自洽校验（无顶层共享默认、无 merge）：
      ① key 须在 keys 内、且为已注册 C 表；② 条目仅许 {start,end,points}（多余字段 pydantic 已拒为 422）；
      ③ start/end 成对且 end≥start；④ points 为正整数。任一不满足 → 400/422。"""
    if not window:
        return
    for k, raw in window.items():
        if k not in keys:
            raise HTTPException(status_code=400, detail=f"window 的 key 不在 keys 内：{k}。")
        spec = REG.keys.get(k)
        if spec is None or spec.get("表") != "C":
            raise HTTPException(status_code=400, detail=f"window 仅作用于 C 表 key：{k} 非 C 表或未注册。")
        if isinstance(raw, dict):
            extra = set(raw) - _WINDOW_FIELDS
            if extra:
                raise HTTPException(status_code=400, detail=f"window[{k}] 含非法字段 {extra}；仅许 {_WINDOW_FIELDS}。")
        e = _entry(raw)
        s, en = e.get("start"), e.get("end")
        if (s is None) != (en is None):
            raise HTTPException(status_code=400,
                                detail=f"window[{k}] 的 start/end 须成对：都不传=当前时刻；相等=单点；end>start=区间。")
        if s is not None:
            try:
                sp, ep = parse_time(s), parse_time(en)
            except ValueError as ex:
                raise HTTPException(status_code=400, detail=str(ex))
            if ep < sp:
                raise HTTPException(status_code=400, detail=f"window[{k}] end 不得早于 start：{s}~{en}。")
        p = e.get("points")
        if p is not None and (not isinstance(p, int) or isinstance(p, bool) or p <= 0):
            raise HTTPException(status_code=400, detail=f"window[{k}] points 须为正整数：{p}。")


# 表 → 对外接口（与 _wrap 的 A→/value、B→/records、C→/series 一致）
_接口_BY_表 = {"A": "/api/value", "B": "/api/records", "C": "/api/series"}


def _wrap(key, r, want_table):
    if r.get("status") == 404:
        return {"error": "key 未注册"}
    if r.get("表") and r["表"] != want_table:
        return {"error": f"{key} 属 {r['表']} 表，请用对应接口（A→/value, B→/records, C→/series）"}
    return r


# —— 使用侧（前端）——
@app.post("/api/value", dependencies=[Depends(require_app)])
def api_value(q: ValueQ):
    data = {}
    for k in q.keys:
        r = _wrap(k, resolve(REG, k), "A")
        data[k] = r if "error" in r else r.get("value")
    return {"status": 200, "data": data}


@app.post("/api/records", dependencies=[Depends(require_app)])
def api_records(q: RecordsQ):
    _validate_records_filter(q.filter)
    data = {}
    for k in q.keys:
        r = _wrap(k, resolve(REG, k, filter=_filter_for(q.filter, k)), "B")
        data[k] = r if "error" in r else r.get("data")
    return {"status": 200, "data": data}


@app.post("/api/series", dependencies=[Depends(require_app)])
def api_series(q: SeriesQ):
    _validate_series_window(q.window, q.keys)
    data = {}
    for k in q.keys:
        e = _entry((q.window or {}).get(k) or {})
        r = _wrap(k, resolve(REG, k, start=e.get("start"), end=e.get("end"),
                             points=e.get("points")), "C")
        if "error" in r:
            data[k] = r
        elif "values" in r:
            data[k] = {"单位": r.get("单位"), "values": r["values"],
                       **({"派生": r["派生"]} if "派生" in r else {})}
        else:
            data[k] = r              # note / 分组容器（含 子 列表）
    return {"status": 200, "data": data}


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
                                     **({"分组": True, "子": s.get("子", [])} if s.get("分组") else {})}
                                    for k, s in REG.keys.items()]}


@app.post("/api/reload", dependencies=[Depends(require_ops)])
def reload_registry():
    global REG
    REG = load()
    rep = selfcheck(REG)
    return {"status": 200, "reloaded": True, "keys": rep["key_count"],
            "selfcheck_ok": rep["ok"], "unresolved": rep["unresolved"]}
