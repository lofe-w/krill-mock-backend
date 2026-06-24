"""对外接口 = 三表的外部投影，每表一个、各自批量（domain §8）。
- POST /api/value    A：{keys:[...]}                      → {key: value}
- POST /api/records  B：{keys:[...], filter:{key:{...}}}  → {key: [{filter,value}]}
- POST /api/series   C：{keys:[...], time | start/end, 指标?} → {key: 序列/多指标}
统一在实现层（一个 resolver），分形状在接口层——不把三种形状塞进一个信封。"""
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from .registry import load, selfcheck
from .resolver import resolve

app = FastAPI(title="krill-mock-backend", version="0.2-3tables")
REG = load()


def _filter_for(filter: Optional[Dict], key: str):
    """filter 支持两种写法：{key:{...}} 按 key 分；或扁平 {...} 应用到全部。"""
    if not filter:
        return None
    if key in filter and isinstance(filter[key], dict):
        return filter[key]
    if any(k in REG.keys for k in filter):     # 是按 key 分的 map，但本 key 没给 → 不过滤
        return None
    return filter                              # 扁平，应用到全部


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
    指标: Optional[str] = None                  # 可选：限定多指标 C key 的某个指标


def _wrap(key, r, want_table):
    """校验表是否匹配端点；不匹配则给出明确错误，避免误用。"""
    if r.get("status") == 404:
        return {"error": "key 未注册"}
    if r.get("表") and r["表"] != want_table:
        return {"error": f"{key} 属 {r['表']} 表，请用对应接口（A→/value, B→/records, C→/series）"}
    return r


@app.post("/api/value")
def api_value(q: ValueQ):
    data = {}
    for k in q.keys:
        r = _wrap(k, resolve(REG, k), "A")
        data[k] = r if "error" in r else r.get("value")
    return {"status": 200, "data": data}


@app.post("/api/records")
def api_records(q: RecordsQ):
    data = {}
    for k in q.keys:
        r = _wrap(k, resolve(REG, k, filter=_filter_for(q.filter, k)), "B")
        data[k] = r if "error" in r else r.get("data")
    return {"status": 200, "data": data}


@app.post("/api/series")
def api_series(q: SeriesQ):
    data = {}
    for k in q.keys:
        flt = {"指标": q.指标} if q.指标 else None
        r = _wrap(k, resolve(REG, k, filter=flt, time=q.time, start=q.start, end=q.end), "C")
        if "error" in r:
            data[k] = r
        elif "values" in r:                    # 单指标/标量型 C
            data[k] = {"单位": r.get("单位"), "values": r["values"], **({"派生": r["派生"]} if "派生" in r else {})}
        else:                                   # 多指标 C
            data[k] = r.get("data", r)
    return {"status": 200, "data": data}


# —— 运维/验收 ——
@app.get("/api/health")
def health():
    rep = selfcheck(REG)
    return {"status": 200, "keys": rep["key_count"], "by_table": rep["by_table"],
            "by_maturity": rep["by_maturity"], "selfcheck_ok": rep["ok"],
            "unresolved": rep["unresolved"], "derivations": len(REG.derivations)}


@app.get("/api/keys")
def list_keys():
    return {"status": 200, "data": [{"key": k, "表": s.get("表"), "成熟度": s.get("成熟度")}
                                    for k, s in REG.keys.items()]}


@app.post("/api/reload")
def reload_registry():
    global REG
    REG = load()
    rep = selfcheck(REG)
    return {"status": 200, "reloaded": True, "keys": rep["key_count"],
            "selfcheck_ok": rep["ok"], "unresolved": rep["unresolved"]}
