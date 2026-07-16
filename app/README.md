# app · 最小 resolver（端到端纵切）

设计的最后一层：让 config 跑起来。依据 domain.md（三表）/ 配置与取数契约 §5 / 造数引擎 §4.0。

## 模块
| 文件 | 职责 |
|---|---|
| `registry.py` | 加载 `config/*.yaml` → 内存注册表；**静态自检**（跨 key 引用/约束指向是否存在） |
| `seed.py` | 种子化纯函数 + 相干噪声 + fBm（可复现） |
| `generators.py` | 4 个 kind：`有界瞬时 / 累计 / 离散状态 / 航迹` |
| `timegrid.py` | cron 网格解析、对齐、区间枚举（含降采样） |
| `resolver.py` | 表分派(A/B/C) → 成熟度取值(人工固定/规则生成/真值采集fallback) → override → 组装 |
| `api.py` | FastAPI 薄入口 `POST /api/query`、`GET /api/health` |
| `main.py` | uvicorn 启动 |

## 运行
```bash
pip install -r requirements.txt
python -m tests.smoke          # 端到端纵切冒烟（不需 fastapi）
python -m app.registry         # 打印静态自检报告
uvicorn app.main:app --port 8000   # 起服务
# curl -X POST localhost:8000/api/query -H 'Content-Type: application/json' -d '{"key":"船舶.信息"}'
```

## 已验证（tests/smoke.py，9/9 通过）
- config 加载 + 静态自检：**55 个 key、引用完整无断链**（A13/B8/C34；人工固定21/规则生成34）。
- A 端到端（船舶.信息）、B 端到端（溯源 value=`{类别,溯源链条}`、数据归各环节）。
- C 有界瞬时：点查 + **确定性**（同输入同输出）+ **区间钳制** + 区间枚举（7 点）。
- C 累计：**单调**。C 离散状态：枚举命中。航迹：派生 4 量（经纬度/航速/航向）。
- B 多记录（设备台账分产线）。override 钩子就绪。

> 当前为"先纵切验证整条链"的最小实现：每表代表 key 已端到端跑通。
> 待办：真值采集 provider（待 Q3/Q5）/ HTTP 层联调（沙箱无 fastapi，api.py 为薄包装，随真实环境联调）。
