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

    # /api/series 支持把父系前缀展开成叶子 key，响应形状等同直接传所有叶子 key；显示 随叶子 key 返回。
    from app.api import api_series, api_value, api_records, list_keys, SeriesQ, ValueQ, RecordsQ
    ship_group = api_series(SeriesQ(keys=["船舶信息模型"],
                                    window={"船舶信息模型": {"start": T, "end": T}}))
    ship_children = [
        "船舶信息模型.船舶方位-经度",
        "船舶信息模型.船舶方位-维度",
        "船舶信息模型.艏向",
        "船舶信息模型.航速",
    ]
    ship_direct = api_series(SeriesQ(keys=ship_children,
                                     window={k: {"start": T, "end": T} for k in ship_children}))
    ship_global = api_series(SeriesQ(keys=ship_children,
                                     window={"start": T, "end": T}))
    ship_group_global = api_series(SeriesQ(keys=["船舶信息模型"],
                                           window={"start": T, "end": T}))
    assert set(ship_group["data"]) == set(ship_children), "父系前缀应展开为叶子 key 数据，不返回包装"
    assert ship_group["data"] == ship_direct["data"], "父系前缀查询应等同直接传所有叶子 key"
    assert ship_global["data"] == ship_direct["data"], "全局 window 应等同逐 key window"
    assert ship_group_global["data"] == ship_direct["data"], "父系前缀 + 全局 window 应继承到全部叶子 key"
    assert ship_group["data"]["船舶信息模型.船舶方位-经度"]["显示"] == "船舶方位-经度"
    sea_prefix = api_series(SeriesQ(keys=["船舶.海况"], window={"船舶.海况": {"start": T, "end": T}}))
    assert "船舶.海况.海水温度" in sea_prefix["data"]
    pump_prefix = api_series(SeriesQ(keys=["船舶.桁杆泵吸系统"],
                                     window={"船舶.桁杆泵吸系统": {"start": T, "end": T}}))
    assert "船舶.桁杆泵吸系统.运行参数.拖网航速" in pump_prefix["data"]
    assert all(k.startswith("船舶.桁杆泵吸系统.") for k in pump_prefix["data"])
    catch_value = api_value(ValueQ(keys=["船舶捕捞"]))
    assert "船舶捕捞.桁杆连续泵吸捕捞系统.主要参数" in catch_value["data"]
    assert not any(k.startswith("3.") for k in catch_value["data"])
    onboard_value = api_value(ValueQ(keys=["虾粉生产线信息", "虾粉生产线",
                                      "冻虾生产线", "虾肉生产线"]))
    assert "虾粉生产线信息.典型原虾日加工量" in onboard_value["data"]
    assert "虾粉生产线.称重输送带（流量称）" in onboard_value["data"]
    assert "冻虾生产线.水平自动冷冻机" in onboard_value["data"]
    assert "虾肉生产线.脱壳机" in onboard_value["data"]
    onboard_series = api_series(SeriesQ(keys=["虾粉生产线", "冻虾产品检测数据"],
                                        window={"start": T, "end": T}))
    assert "虾粉生产线.称重输送带（流量称）.实时读数" in onboard_series["data"]
    assert "冻虾产品检测数据.冻虾冻块中心温度" in onboard_series["data"]
    passed.append("series/value 父系前缀展开(等同叶子keys) + 显示")

    try:
        api_series(SeriesQ(keys=ship_children, window={
            "start": T,
            "船舶信息模型.船舶方位-经度": {"start": T, "end": T},
        }))
        raise AssertionError("window 混用全局字段和逐 key 配置应报错")
    except Exception as ex:
        assert getattr(ex, "status_code", None) == 400
        assert "不能混用" in str(getattr(ex, "detail", ""))
    passed.append("series 全局 window + 混用校验")

    # 固定点数：区间分桶。查一年用 points=12 → 恰 12 点（自适应比例尺）
    sr = resolve(reg, "船舶.海况.海水温度",
                 start="2026-01-01 00:00:00", end="2026-12-31 00:00:00", points=12)
    assert len(sr["values"]) == 12, f"分桶点数={len(sr['values'])}"
    # 全局默认点数=20：同跨度不传 points → ≤20 点
    sr20 = resolve(reg, "船舶.海况.海水温度",
                   start="2026-01-01 00:00:00", end="2026-12-31 00:00:00")
    assert len(sr20["values"]) <= 20, f"全局默认点数={len(sr20['values'])}"
    passed.append(f"固定点数分桶(年查 points=12→{len(sr['values'])}点, 全局默认→{len(sr20['values'])}点)")

    # 联调兼容层：内存构造旧 key alias，不改真实 YAML。旧 key 可解析到新 key，并产生 deprecated warning。
    reg.add({
        "key": "测试.旧海水温度",
        "表": "C",
        "alias_of": "船舶.海况.海水温度",
        "deprecated": {
            "since": "1.1.0",
            "remove_after": "2026-08-15",
            "replaced_by": "船舶.海况.海水温度",
            "reason": "联调兼容层测试",
        },
    })
    old = resolve(reg, "测试.旧海水温度", start=T, end=T)
    new = resolve(reg, "船舶.海况.海水温度", start=T, end=T)
    assert old["status"] == 200 and old["values"] == new["values"], "alias_of 应透明解析到目标 key"
    warns = compatibility_warnings(reg, "测试.旧海水温度")
    meta = contract_meta(reg.get("测试.旧海水温度", "C"))
    assert warns and warns[0]["type"] == "deprecated_key"
    assert meta["deprecated"] is True and meta["replaced_by"] == "船舶.海况.海水温度"
    passed.append("联调兼容层(alias_of/deprecated 元信息)")

    # 跨表同名：外部 key 可以复用，接口表决定解析到哪一条；/api/keys 用 qualified_key 做唯一身份。
    import app.api as api_mod
    api_mod.REG.add({"key": "测试.跨表同名", "表": "A", "成熟度": "人工固定", "value": {"来源": "A"}})
    api_mod.REG.add({"key": "测试.跨表同名", "表": "B", "成熟度": "人工固定",
                     "filter": ["类型"], "条目": [{"filter": {"类型": "B"}, "value": {"来源": "B"}}]})
    api_mod.REG.add({"key": "测试.跨表同名", "表": "C", "成熟度": "规则生成",
                     "量语义": "瞬时", "网格": "0 * * * *", "单位": "℃",
                     "规则": {"kind": "有界瞬时", "基线": 10, "幅度": 0, "区间": [10, 10]}})
    same_a = api_value(ValueQ(keys=["测试.跨表同名"]))
    same_b = api_records(RecordsQ(keys=["测试.跨表同名"], filter={"测试.跨表同名": {"类型": "B"}}))
    same_c = api_series(SeriesQ(keys=["测试.跨表同名"], window={"start": T, "end": T}))
    assert same_a["data"]["测试.跨表同名"]["来源"] == "A"
    assert same_b["data"]["测试.跨表同名"][0]["value"]["来源"] == "B"
    assert same_c["data"]["测试.跨表同名"]["values"][0]["value"] == 10
    key_rows = [r for r in list_keys()["data"] if r["key"] == "测试.跨表同名"]
    assert sorted(r["qualified_key"] for r in key_rows) == [
        "A:测试.跨表同名", "B:测试.跨表同名", "C:测试.跨表同名"]
    passed.append("跨表同名 key(接口按表解析 + qualified_key)")

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
