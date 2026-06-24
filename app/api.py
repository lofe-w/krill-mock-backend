"""对外接口 = 三表的外部投影，每表一个、各自批量（domain §8）。
使用侧（前端）：/api/value /api/records /api/series —— 需 使用侧 Token。
运维侧（运维）：/api/keys /api/reload —— 需 运维侧 Token。/api/health 公开(存活探测)。
鉴权：.env 写死两个 Token，前端/运维以 Authorization: Bearer <token> 传入。
CORS：浏览器跨域调用需开启（见 CORS_ORIGINS）。"""
import os
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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


def _bearer(authorization: Optional[str]):
    if authorization and authorization.startswith("Bearer "):
        return authorization[len("Bearer "):].strip()
    return None


def require_app(authorization: Optional[str] = Header(None)):
    """使用侧：使用侧 Token 或运维侧 Token 均可（运维侧权限更高）。"""
    if not AUTH_ENABLED:
        return
    tok = _bearer(authorization)
    if tok and tok in (TOKEN_APP, TOKEN_OPS) and tok != "":
        return
    raise HTTPException(status_code=401, detail="缺少或无效的使用侧 Token（Authorization: Bearer <API_TOKEN_APP>）")


def require_ops(authorization: Optional[str] = Header(None)):
    """运维侧：仅运维侧 Token。"""
    if not AUTH_ENABLED:
        return
    tok = _bearer(authorization)
    if tok and tok == TOKEN_OPS and tok != "":
        return
    raise HTTPException(status_code=401, detail="缺少或无效的运维侧 Token（Authorization: Bearer <API_TOKEN_OPS>）")


# —— 请求体 ——
class ValueQ(BaseModel):
    keys: List[str]


class RecordsQ(BaseModel):
    keys: List[str]
    filter: Optional[Dict[str, Any]] = None


class SeriesQ(BaseModel):
    keys: List[str]
    metrics: Optional[Dict[str, List[str]]] = None   # 按 key 作用域：{key: [指标名,...]}
    start: Optional[str] = None
    end: Optional[str] = None


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


def _metrics_for(metrics: Optional[Dict], key: str):
    """C 表指标选取：metrics 按 key 作用域，形如 {key: [指标名,...]}（与 records.filter 对称）。
    形状由 _validate_series_metrics 保证，这里直接取本 key 选中的指标列表。"""
    return (metrics or {}).get(key)


def _validate_series_metrics(metrics: Optional[Dict]):
    """/api/series 是批量接口（keys:[...]）+ 单个共享 metrics，故 metrics 必须按 key 作用域：
    {key: [指标名,...]}。每个顶层项都得是已注册 key、值是指标名列表；否则 400，
    避免无作用域的指标选择被静默套到所有 key。"""
    if not metrics:
        return
    bad = [k for k in metrics if k not in REG.keys or not isinstance(metrics[k], list)]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(f"metrics 须按 key 作用域，形如 {{\"<key>\": [指标名,...]}}；"
                    f"以下顶层项不是已注册 key 或值非列表：{bad}。"))


def _validate_series_range(start: Optional[str], end: Optional[str]):
    """时间模式由 start/end 表达：都不传=当前时刻单点；start==end=该时刻单点；end>start=区间。
    start/end 必须成对出现，且 end 不得早于 start——落单或倒挂直接 400，不静默退化成点查。"""
    if (start is None) != (end is None):
        raise HTTPException(
            status_code=400,
            detail="start/end 必须成对出现：都不传=当前时刻；二者相等=该时刻单点；end>start=区间。")
    if start is not None and end is not None:
        try:
            s, e = parse_time(start), parse_time(end)
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex))
        if e < s:
            raise HTTPException(status_code=400, detail=f"end 不得早于 start：start={start}, end={end}。")


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
    _validate_series_metrics(q.metrics)
    _validate_series_range(q.start, q.end)
    data = {}
    for k in q.keys:
        sel = _metrics_for(q.metrics, k)
        flt = {"指标": sel} if sel else None
        r = _wrap(k, resolve(REG, k, filter=flt, start=q.start, end=q.end), "C")
        if "error" in r:
            data[k] = r
        elif "values" in r:
            data[k] = {"单位": r.get("单位"), "values": r["values"], **({"派生": r["派生"]} if "派生" in r else {})}
        else:
            data[k] = r.get("data", r)
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
                                     "成熟度": s.get("成熟度")}
                                    for k, s in REG.keys.items()]}


@app.post("/api/reload", dependencies=[Depends(require_ops)])
def reload_registry():
    global REG
    REG = load()
    rep = selfcheck(REG)
    return {"status": 200, "reloaded": True, "keys": rep["key_count"],
            "selfcheck_ok": rep["ok"], "unresolved": rep["unresolved"]}
