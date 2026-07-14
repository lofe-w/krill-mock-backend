"""端到端纵切冒烟测试（不依赖 fastapi）：
- config 加载 + 静态自检
- 每表 1 个 key 端到端：A / B / C(有界瞬时) / C(累计) / C(离散状态) / 航迹
- 确定性（同 key,time 两次相等）+ 区间断言
运行：python -m tests.smoke    或    python tests/smoke.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.registry import load, selfcheck, compatibility_warnings, contract_meta
from app.resolver import resolve

T = "2026-06-18 12:00:00"
reg = load()


def show(title, resp):
    print(f"\n── {title} ──")
    print(json.dumps(resp, ensure_ascii=False, default=str)[:600])


def main():
    passed = []

    # 0. 自检
    rep = selfcheck(reg)
    print("【自检】", json.dumps(rep, ensure_ascii=False))
    assert rep["key_count"] >= 50, "key 数异常"
    assert rep["ok"], f"存在未解析引用: {rep['unresolved']}"
    passed.append("自检通过/引用完整")

    # A
    a = resolve(reg, "船舶.信息")
    show("A 船舶.信息", a)
    assert a["status"] == 200 and a["value"]["IMO"] == "9849332"
    passed.append("A 端到端")

    # A·渔季：按当前时间返回「当前渔季」单对象（非列表）
    from app.timegrid import now_local, parse_time
    fs = resolve(reg, "渔季")
    show("A 渔季(当前)", fs)
    assert fs["status"] == 200
    assert isinstance(fs["value"], dict), "渔季应返回单个当前渔季对象，而非列表"
    _st, _en = parse_time(fs["value"]["开始"]), parse_time(fs["value"]["结束"])
    _now = now_local().replace(tzinfo=None)
    assert _st <= _now <= _en, f"当前时间 {_now} 不在返回渔季 [{_st},{_en}] 内"
    passed.append(f"A 渔季按时间返回当前({fs['value']['名称']})")

    # B
    b = resolve(reg, "溯源", filter={"产品批号": "2606AKM01"})
    show("B 溯源/2606AKM01", b)
    assert b["status"] == 200 and len(b["data"]) == 1
    assert b["data"][0]["value"]["类别"] == "全脂虾粉溯源"
    chain = b["data"][0]["value"]["溯源链条"]
    assert isinstance(chain, list), "溯源链条应为有序数组"
    assert chain[0]["环节"] == "磷虾捕捞" and chain[-1]["环节"] == "全脂虾粉产品检测"   # 顺序：捕捞→…→检测（环节名照甲方6.1）
    assert set(chain[1].keys()) == {"环节", "数据"}                            # 元素=标签+数据两层，环节是字段非键
    assert "直接蒸煮器加热温度" in chain[1]["数据"]                            # 具体值在 数据 内
    assert not any("引用" in str(seg) for seg in chain), "不应再有内部 key 引用"
    passed.append("B 溯源(有序数组·{环节,数据}·具体值·无引用)")

    # C 有界瞬时（点查 + 区间 + 确定性 + 区间断言）
    c1 = resolve(reg, "船舶.能耗.剩余燃油", start=T, end=T)
    show("C 有界瞬时 船舶.能耗.剩余燃油 @点查", c1)
    v = c1["values"][0]["value"]
    assert 0 <= v <= 1000, f"超区间: {v}"
    c1b = resolve(reg, "船舶.能耗.剩余燃油", start=T, end=T)
    assert c1b["values"][0]["value"] == v, "不确定性！同输入应同输出"
    passed.append("C 有界瞬时(点查/确定性/区间)")

    c1r = resolve(reg, "船舶.能耗.剩余燃油", start="2026-06-18 00:00:00", end="2026-06-18 06:00:00")
    assert len(c1r["values"]) == 7, f"区间网格点数={len(c1r['values'])}"   # 每小时, 0..6 含端点
    passed.append(f"C 区间枚举({len(c1r['values'])}点)")

    # C 累计（单调 + 确定性）
    c2a = resolve(reg, "船舶.捕捞系统.累计产量.泵吸", start="2026-06-18 12:00:00", end="2026-06-18 12:00:00")
    c2b = resolve(reg, "船舶.捕捞系统.累计产量.泵吸", start="2026-06-18 18:00:00", end="2026-06-18 18:00:00")
    show("C 累计 12:00", c2a); show("C 累计 18:00", c2b)
    assert c2b["values"][0]["value"] >= c2a["values"][0]["value"], "累计非单调！"
    passed.append("C 累计(单调)")

    # C 离散状态
    c3 = resolve(reg, "船舶.桁杆泵吸系统.拖网绞车.状态", start=T, end=T)
    show("C 离散状态 拖网绞车", c3)
    assert c3["values"][0]["value"] in ["运行", "待机", "停止"]
    passed.append("C 离散状态")

    # 航迹（4 输出派生一致）
    nav = resolve(reg, "船舶.航行", start=T, end=T)
    show("C 航迹 船舶.航行", nav)
    pv = nav["values"][0]["value"]
    assert set(["经度", "纬度", "航速", "航向"]).issubset(pv.keys())
    assert -180 <= pv["经度"] <= 180 and 0 <= pv["航速"] <= 60
    assert -65 <= pv["纬度"] <= -60, f"船位未落在南极磷虾作业海域: {pv}"
    passed.append("航迹(派生4量)")

    # override 生效（设备故障剧情）
    ov = resolve(reg, "设备.台账", filter={"所属产线": "虾肉生产线"})
    assert ov["status"] == 200
    passed.append("B 设备台账多记录")

    # 派生：虾油得率应≈18%（金蝶真实出油率，=成品油/虾粉）——扁平化后直接查叶子 key
    d = resolve(reg, "工厂.虾油提取生产线.生产数据.虾油得率", start=T, end=T)
    yv = d["values"][0]["value"]
    show("派生 虾油得率", d)
    assert 13 <= yv <= 27, f"得率 {yv} 不在真实区间"
    passed.append(f"派生·虾油得率={yv}%(真实出油率)")

    # 同一事实只建一处：车间外大气温度不再单独建模，前端直接查 工厂.天气.温度（扁平 key）
    w = resolve(reg, "工厂.天气.温度", start=T, end=T)
    assert -10 <= w["values"][0]["value"] <= 45
    passed.append("同一事实只建一处(车间外大气=工厂.天气)")

    # 扁平化：指标 dict 父节点降为「分组」容器，给出子 key 供发现（不再传 metrics）
    g = resolve(reg, "船舶.海况", start=T, end=T)
    assert g.get("分组") and "船舶.海况.海水温度" in g.get("子", [])
    passed.append(f"分组容器(海况→{len(g['子'])}子key)")

    # 固定点数：区间分桶。查一年用 points=12 → 恰 12 点（自适应比例尺）
    sr = resolve(reg, "船舶.海况.海水温度",
                 start="2026-01-01 00:00:00", end="2026-12-31 00:00:00", points=12)
    assert len(sr["values"]) == 12, f"分桶点数={len(sr['values'])}"
    # 默认点数=20：同跨度不传 points → ≤20 点
    sr20 = resolve(reg, "船舶.海况.海水温度",
                   start="2026-01-01 00:00:00", end="2026-12-31 00:00:00")
    assert len(sr20["values"]) <= 20, f"默认点数={len(sr20['values'])}"
    passed.append(f"固定点数分桶(年查 points=12→{len(sr['values'])}点, 默认→{len(sr20['values'])}点)")

    # 联调兼容层：内存构造旧 key alias，不改真实 YAML。旧 key 可解析到新 key，并产生 deprecated warning。
    reg.keys["测试.旧海水温度"] = {
        "key": "测试.旧海水温度",
        "表": "C",
        "alias_of": "船舶.海况.海水温度",
        "deprecated": {
            "since": "1.1.0",
            "remove_after": "2026-08-15",
            "replaced_by": "船舶.海况.海水温度",
            "reason": "联调兼容层测试",
        },
    }
    old = resolve(reg, "测试.旧海水温度", start=T, end=T)
    new = resolve(reg, "船舶.海况.海水温度", start=T, end=T)
    assert old["status"] == 200 and old["values"] == new["values"], "alias_of 应透明解析到目标 key"
    warns = compatibility_warnings(reg, "测试.旧海水温度")
    meta = contract_meta(reg.keys["测试.旧海水温度"])
    assert warns and warns[0]["type"] == "deprecated_key"
    assert meta["deprecated"] is True and meta["replaced_by"] == "船舶.海况.海水温度"
    passed.append("联调兼容层(alias_of/deprecated 元信息)")

    # 累计型分桶取桶右端：单调不减
    cum = resolve(reg, "船舶.捕捞系统.累计产量.泵吸",
                  start="2026-06-18 00:00:00", end="2026-06-18 23:59:00", points=8)
    vs = [p["value"] for p in cum["values"]]
    assert vs == sorted(vs), "累计分桶应单调不减"
    passed.append(f"累计分桶单调({len(vs)}点)")

    # "当前时刻"按业务时区（APP_TZ），不随容器系统时区（Docker slim 默认 UTC）漂移。
    # 锁死 now_local() 语义：① 等价于在 APP_TZ 取 now 再 strip tzinfo；
    # ② 与系统 UTC 的差 = 该时区偏移（默认北京 +8h），从而修复"UTC 容器返回 02:00 而非 10:00"。
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from app.timegrid import now_local, APP_TZ
    expect = datetime.now(ZoneInfo(APP_TZ)).replace(tzinfo=None)
    assert abs((now_local() - expect).total_seconds()) < 5, "now_local 应等于 APP_TZ 墙上时钟"
    off_h = (now_local() - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 3600
    assert 7.9 < off_h < 8.1, f"默认 APP_TZ=Asia/Shanghai 应比 UTC 早约 8h，实测 {off_h:.2f}h"
    passed.append(f"now_local 业务时区(APP_TZ={APP_TZ}, 较UTC{off_h:+.0f}h)")

    print("\n" + "=" * 50)
    for p in passed:
        print("  ✅", p)
    print(f"\n全部 {len(passed)} 项通过 ✅  端到端纵切打通。")


if __name__ == "__main__":
    main()
