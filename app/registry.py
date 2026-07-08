"""配置加载 + 静态自检。
- 加载 config/registry/*.yaml → 内存注册表（key -> spec）
- 把 C 的「指标 dict」展开成扁平全限定 key（书写糖 → 独立时序），对外只剩 keys 一种寻址
- 加载 constraints/overrides/sources
- 自检：引用/不变式涉及的 key 是否存在；默认点数/分组/冲突校验"""
import glob
import os
import re
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config")


def _load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return list(yaml.safe_load_all(f))


class Registry:
    def __init__(self):
        self.keys = {}          # key -> spec
        self.constraints = {}
        self.overrides = []
        self.sources = {}
        self.derivations = {}   # 派生指标全限定key -> 表达式（来自 constraints）
        self.collisions = []    # 展开期重名冲突（被忽略的子 key）

    def metric_paths(self):
        """全部可被引用的 key 路径（展开后子指标已是独立 key，直接取 keys）。"""
        return set(self.keys)

    def exists(self, ref: str) -> bool:
        if ref in self.keys:
            return True
        for k in self.keys:
            # ref 是某 key 的子路径（<key>.<指标>），或 ref 是某 key 的父路径（指代其下全部）
            if ref.startswith(k + ".") or k.startswith(ref + "."):
                return True
        return False


def load() -> Registry:
    reg = Registry()
    for path in sorted(glob.glob(os.path.join(CONFIG, "registry", "*.yaml"))):
        for doc in _load_yaml(path):
            if not isinstance(doc, list):
                continue
            for e in doc:
                if isinstance(e, dict) and "key" in e:
                    if e["key"] in reg.keys:
                        # 同名（如三线说明性占位）合并：保留首个含实体内容的
                        continue
                    reg.keys[e["key"]] = e
    _expand_metrics(reg)        # 指标 dict → 扁平子 key（书写糖展开）
    cpath = os.path.join(CONFIG, "constraints.yaml")
    if os.path.exists(cpath):
        docs = _load_yaml(cpath)
        reg.constraints = docs[0] if docs and isinstance(docs[0], dict) else {}
    opath = os.path.join(CONFIG, "overrides.yaml")
    if os.path.exists(opath):
        docs = _load_yaml(opath)
        reg.overrides = (docs[0] or {}).get("overrides", []) if docs else []
    spath = os.path.join(CONFIG, "sources.yaml")
    if os.path.exists(spath):
        docs = _load_yaml(spath)
        reg.sources = docs[0] if docs and isinstance(docs[0], dict) else {}
    reg.derivations = _build_derivations(reg.constraints)
    return reg


# —— 指标 dict 展开：书写糖 → 扁平全限定 C key —————————————————————
# 「一个 C key 带 N 指标」与「N 个共前缀 key」在模型上等价（domain §2）。
# 为消除"有些传 metrics、有些传 keys"的寻址歧义：把指标 dict 在加载期展开成
# 独立子 key（继承父的 网格/量语义/成熟度，记 _组 供派生兄弟项解析），
# 父条目降为「分组」元数据（保留 子 列表，供 /api/keys 发现，不再作为时序被查）。
# 展开后种子名 = f"{父key}.{指标名}"，与旧 resolver 的 fk 完全一致 → 生成值逐字不变。
_CHILD_INHERIT = ("单位", "规则", "派生", "区间", "不变式", "说明",
                  "目标成熟度", "默认点数", "网格", "量语义")


def _expand_metrics(reg):
    new = {}
    for pkey, spec in list(reg.keys.items()):
        ind = spec.get("指标")
        if not isinstance(ind, dict):
            continue
        children = []
        for name, m in ind.items():
            ckey = f"{pkey}.{name}"
            children.append(ckey)
            if not isinstance(m, dict):
                continue
            child = {"key": ckey, "表": "C",
                     "成熟度": spec.get("成熟度"), "_组": pkey}
            if "网格" in m or "网格" in spec:
                child["网格"] = m.get("网格", spec.get("网格"))
            ql = m.get("量语义", spec.get("量语义"))
            if ql is not None:
                child["量语义"] = ql
            for f in _CHILD_INHERIT:
                if f in m and f not in child:
                    child[f] = m[f]
            if ckey in reg.keys or ckey in new:
                reg.collisions.append(ckey)
                continue
            new[ckey] = child
        # 父 → 分组元数据
        spec["分组"] = True
        spec["子"] = children
        spec.pop("指标", None)
    reg.keys.update(new)


def _build_derivations(constraints):
    """从 constraints 收集所有 {key, 定义} 对 → {全限定key: 表达式}。
    覆盖：物料守恒.*.得率派生、得率.*、百分比派生[]。"""
    out = {}

    def walk(n):
        if isinstance(n, dict):
            if isinstance(n.get("key"), str) and isinstance(n.get("定义"), str):
                out[n["key"]] = n["定义"]
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(constraints)
    return out


# —— 静态自检：只验证"跨 key 引用"——即以 船舶./工厂. 开头的全限定数据 key ——
# （指标子名如 MVR2500.真空度、constraints 段名如 物料守恒.虾油提取生产线 不以此开头，自然排除）
_KEY_RE = re.compile(r"[一-鿿A-Za-z0-9]+(?:\.[一-鿿A-Za-z0-9]+)+")


def _take(s, acc):
    for m in _KEY_RE.findall(str(s)):
        if m.startswith(("船舶.", "工厂.")) and "(" not in m and "*" not in m:
            acc.add(m)


def _walk(node, acc):
    if isinstance(node, dict):
        for k, v in node.items():
            _take(k, acc)
            _walk(v, acc)
    elif isinstance(node, list):
        for v in node:
            _walk(v, acc)
    else:
        _take(node, acc)


def selfcheck(reg: Registry):
    report = {"key_count": len(reg.keys), "unresolved": [], "ok": True, "notes": []}
    refs = set()
    _walk(reg.constraints, refs)            # 约束层跨 key 引用
    for spec in reg.keys.values():          # 注册表内 引用 字段（溯源/车间外大气 等）
        _walk(spec, refs)
    for ref in sorted(refs):
        if not reg.exists(ref):
            report["unresolved"].append(ref)
    # 展开期重名冲突 → 警告（不致命，但应消除）
    if reg.collisions:
        report["notes"].append(f"指标展开重名冲突(已忽略): {sorted(set(reg.collisions))}")
    # 默认点数：仅许 C 表、正整数
    bad_pts = [k for k, s in reg.keys.items()
               if "默认点数" in s and (s.get("表") != "C"
                  or not isinstance(s["默认点数"], int) or isinstance(s["默认点数"], bool)
                  or s["默认点数"] <= 0)]
    if bad_pts:
        report["notes"].append(f"默认点数 非法(须 C 表正整数): {bad_pts}")
    report["ok"] = not report["unresolved"] and not bad_pts and not reg.collisions
    from collections import Counter
    report["by_table"] = dict(Counter(s.get("表") for s in reg.keys.values()))
    report["by_maturity"] = dict(Counter(s.get("成熟度") for s in reg.keys.values()))
    report["groups"] = sum(1 for s in reg.keys.values() if s.get("分组"))
    return report


if __name__ == "__main__":
    import json
    reg = load()
    rep = selfcheck(reg)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
