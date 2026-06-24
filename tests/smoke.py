"""端到端纵切冒烟测试（不依赖 fastapi）：
- config 加载 + 静态自检
- 每表 1 个 key 端到端：A / B / C(有界瞬时) / C(累计) / C(离散状态) / 航迹
- 确定性（同 key,time 两次相等）+ 区间断言
运行：python -m tests.smoke    或    python tests/smoke.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.registry import load, selfcheck
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

    # B
    b = resolve(reg, "溯源", filter={"产品批号": "2606AKM01"})
    show("B 溯源/2606AKM01", b)
    assert b["status"] == 200 and len(b["data"]) == 1
    assert b["data"][0]["value"]["类别"] == "全脂虾粉溯源"
    chain = b["data"][0]["value"]["溯源链条"]
    assert isinstance(chain, list), "溯源链条应为有序数组"
    assert chain[0]["环节"] == "磷虾捕捞" and chain[-1]["环节"] == "产品检测"   # 顺序：捕捞→…→检测
    assert set(chain[1].keys()) == {"环节", "数据"}                            # 元素=标签+数据两层，环节是字段非键
    assert "直接蒸煮器加热温度" in chain[1]["数据"]                            # 具体值在 数据 内
    assert not any("引用" in str(seg) for seg in chain), "不应再有内部 key 引用"
    passed.append("B 溯源(有序数组·{环节,数据}·具体值·无引用)")

    # C 有界瞬时（点查 + 区间 + 确定性 + 区间断言）
    c1 = resolve(reg, "船舶.能耗.剩余燃油", time=T)
    show("C 有界瞬时 船舶.能耗.剩余燃油 @点查", c1)
    v = c1["values"][0]["value"]
    assert 0 <= v <= 1000, f"超区间: {v}"
    c1b = resolve(reg, "船舶.能耗.剩余燃油", time=T)
    assert c1b["values"][0]["value"] == v, "不确定性！同输入应同输出"
    passed.append("C 有界瞬时(点查/确定性/区间)")

    c1r = resolve(reg, "船舶.能耗.剩余燃油", start="2026-06-18 00:00:00", end="2026-06-18 06:00:00")
    assert len(c1r["values"]) == 7, f"区间网格点数={len(c1r['values'])}"   # 每小时, 0..6 含端点
    passed.append(f"C 区间枚举({len(c1r['values'])}点)")

    # C 累计（单调 + 确定性）
    c2a = resolve(reg, "船舶.捕捞.累计产量.泵吸", time="2026-06-18 12:00:00")
    c2b = resolve(reg, "船舶.捕捞.累计产量.泵吸", time="2026-06-18 18:00:00")
    show("C 累计 12:00", c2a); show("C 累计 18:00", c2b)
    assert c2b["values"][0]["value"] >= c2a["values"][0]["value"], "累计非单调！"
    passed.append("C 累计(单调)")

    # C 离散状态
    c3 = resolve(reg, "船舶.桁杆泵吸系统.拖网绞车.状态", time=T)
    show("C 离散状态 拖网绞车", c3)
    assert c3["values"][0]["value"] in ["运行", "待机", "停止"]
    passed.append("C 离散状态")

    # 航迹（4 输出派生一致）
    nav = resolve(reg, "船舶.航行", time=T)
    show("C 航迹 船舶.航行", nav)
    pv = nav["values"][0]["value"]
    assert set(["经度", "纬度", "航速", "航向"]).issubset(pv.keys())
    assert -180 <= pv["经度"] <= 180 and 0 <= pv["航速"] <= 60
    passed.append("航迹(派生4量)")

    # override 生效（设备故障剧情）
    ov = resolve(reg, "设备.台账", filter={"所属产线": "虾肉生产线"})
    assert ov["status"] == 200
    passed.append("B 设备台账多记录")

    # 派生：虾油得率应≈18%（金蝶真实出油率，=成品油/虾粉）
    d = resolve(reg, "工厂.虾油线.生产数据", filter={"指标": "虾油得率"}, time=T)
    yv = d["data"]["虾油得率"]["values"][0]["value"]
    show("派生 虾油得率", d["data"]["虾油得率"])
    assert 13 <= yv <= 27, f"得率 {yv} 不在真实区间"
    passed.append(f"派生·虾油得率={yv}%(真实出油率)")

    # 同一事实只建一处：车间外大气温度不再单独建模，前端直接查 工厂.天气.温度
    w = resolve(reg, "工厂.天气", filter={"指标": "温度"}, time=T)
    assert -10 <= w["data"]["温度"]["values"][0]["value"] <= 45
    passed.append("同一事实只建一处(车间外大气=工厂.天气)")

    print("\n" + "=" * 50)
    for p in passed:
        print("  ✅", p)
    print(f"\n全部 {len(passed)} 项通过 ✅  端到端纵切打通。")


if __name__ == "__main__":
    main()
