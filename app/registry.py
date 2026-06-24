"""配置加载 + 静态自检。
- 加载 config/registry/*.yaml → 内存注册表（key -> spec）
- 加载 constraints/overrides/sources
- 自检：引用/不变式涉及的 key 是否存在（含 <key>.<指标> 子路径）"""
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

    def metric_paths(self):
        """全部可被引用的 key 路径：注册 key + <key>.<指标名>。"""
        paths = set(self.keys)
        for k, spec in self.keys.items():
            ind = spec.get("指标")
            if isinstance(ind, dict):
                for name in ind:
                    paths.add(f"{k}.{name}")
        return paths

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
    return reg


# —— 静态自检：只验证"跨 key 引用"——即以 船舶./工厂. 开头的全限定数据 key ——
# （指标子名如 MVR2500.真空度、constraints 段名如 物料守恒.虾油线 不以此开头，自然排除）
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
    report["ok"] = not report["unresolved"]
    from collections import Counter
    report["by_table"] = dict(Counter(s.get("表") for s in reg.keys.values()))
    report["by_maturity"] = dict(Counter(s.get("成熟度") for s in reg.keys.values()))
    return report


if __name__ == "__main__":
    import json
    reg = load()
    rep = selfcheck(reg)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
