"""薄查询入口（domain §8：接口=三表的外部投影，最上层、最可替换）。
统一 POST /api/query，按表自然分派。"""
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Dict, Any
from .registry import load, selfcheck
from .resolver import resolve

app = FastAPI(title="krill-mock-backend", version="0.1-slice")
REG = load()


class Query(BaseModel):
    key: str
    filter: Optional[Dict[str, Any]] = None
    time: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None


@app.post("/api/query")
def api_query(q: Query):
    return resolve(REG, q.key, filter=q.filter, time=q.time, start=q.start, end=q.end)


@app.get("/api/health")
def health():
    rep = selfcheck(REG)
    return {"status": 200, "keys": rep["key_count"], "by_table": rep["by_table"],
            "by_maturity": rep["by_maturity"], "selfcheck_ok": rep["ok"], "unresolved": rep["unresolved"]}


@app.get("/api/keys")
def list_keys():
    """列出全部已注册 key（便于验收时挑着查）。"""
    return {"status": 200, "data": [{"key": k, "表": s.get("表"), "成熟度": s.get("成熟度")}
                                    for k, s in REG.keys.items()]}


@app.post("/api/reload")
def reload_registry():
    """改完 config/*.yaml 后调用，无需重启即重新加载 + 自检。"""
    global REG
    REG = load()
    rep = selfcheck(REG)
    return {"status": 200, "reloaded": True, "keys": rep["key_count"],
            "selfcheck_ok": rep["ok"], "unresolved": rep["unresolved"]}
