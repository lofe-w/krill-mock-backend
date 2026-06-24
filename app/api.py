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
    time: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    指标: Optional[Any] = None              # str | [str] | {key:[str]}


def _filter_for(filter: Optional[Dict], key: str):
    if not filter:
        return None
    if key in filter and isinstance(filter[key], dict):
        return filter[key]
    if any(k in REG.keys for k in filter):
        return None
    return filter


def _指标_for(指标, key):
    if 指标 is None:
        return None
    if isinstance(指标, dict):
        return 指标.get(key)
    return 指标


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
    data = {}
    for k in q.keys:
        r = _wrap(k, resolve(REG, k, filter=_filter_for(q.filter, k)), "B")
        data[k] = r if "error" in r else r.get("data")
    return {"status": 200, "data": data}


@app.post("/api/series", dependencies=[Depends(require_app)])
def api_series(q: SeriesQ):
    data = {}
    for k in q.keys:
        sel = _指标_for(q.指标, k)
        flt = {"指标": sel} if sel else None
        r = _wrap(k, resolve(REG, k, filter=flt, time=q.time, start=q.start, end=q.end), "C")
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
    return {"status": 200, "data": [{"key": k, "表": s.get("表"), "成熟度": s.get("成熟度")}
                                    for k, s in REG.keys.items()]}


@app.post("/api/reload", dependencies=[Depends(require_ops)])
def reload_registry():
    global REG
    REG = load()
    rep = selfcheck(REG)
    return {"status": 200, "reloaded": True, "keys": rep["key_count"],
            "selfcheck_ok": rep["ok"], "unresolved": rep["unresolved"]}
