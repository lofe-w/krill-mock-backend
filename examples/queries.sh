#!/usr/bin/env bash
# 验收用例：逐条打到本地服务，肉眼核对。先 `docker compose up -d --build`。
# 用法：bash examples/queries.sh
set -e
H="${1:-http://localhost:8000}"
pp() { python3 -m json.tool --no-ensure-ascii 2>/dev/null || cat; }
q() { echo -e "\n### $1"; shift; curl -s "$@" | pp; }

q "健康 + 自检（应 selfcheck_ok=true，keys≈58）" "$H/api/health"

q "A 船舶信息（应 IMO=9849332）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"船舶.信息"}'

q "A 虾油线设计能力" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"工厂.虾油线.设计能力"}'

q "B 溯源·虾油 XY-001（链条含 虾油提取 环节、引用检测）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"溯源","filter":{"商品编号":"XY-001"}}'

q "B 检测·虾油（★金蝶真实标定：磷脂≈60.7 EPA≈16.2 DHA≈7.58 虾青素181mg/kg 酸价7.8）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"检测","filter":{"产品":"虾油","批次":"XYTQ-2026-001"}}'

q "B 仓储·工厂冷库（多条）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"仓储","filter":{"仓库类别":"工厂冷库"}}'

q "B 人员·磷虾船（4 人，含内嵌时序属性）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"人员","filter":{"所属":"磷虾船"}}'

q "C 点查·冻虾舱温度（应在 [-25,-15]，约 -18）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"船舶.冻虾舱.温度","time":"2026-06-18 12:00:00"}'

q "C 区间·冻虾舱温度（00:00~06:00 应 7 点序列、平滑）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"船舶.冻虾舱.温度","start":"2026-06-18 00:00:00","end":"2026-06-18 06:00:00"}'

q "C 确定性·同查询再来一次（值应与上面点查完全相同）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"船舶.冻虾舱.温度","time":"2026-06-18 12:00:00"}'

q "C 多指标·虾油线成品油产量（累计，速率按金蝶真实出油率18%标定）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"工厂.虾油线.生产数据","filter":{"指标":"成品油产量"},"start":"2026-06-18 00:00:00","end":"2026-06-18 12:00:00"}'
# 注：虾油得率/剩余燃油百分比 是"派生"指标(=产出/投入，constraints 求)，当前返回占位 note，
#     派生求值的接入是下一步铺开项；成分质检值的真实标定见上面"B 检测"那条。

q "C 离散·拖网绞车状态（运行/待机/停止）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"船舶.桁杆泵吸系统.拖网绞车.状态","time":"2026-06-18 12:00:00"}'

q "航迹·船舶航行（经纬度/航速/航向 派生一致）" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"船舶.航行","time":"2026-06-18 12:00:00"}'

q "404·不存在的 key" \
  -X POST "$H/api/query" -H 'Content-Type: application/json' -d '{"key":"不存在.key"}'

echo -e "\n— 全部用例已发出，逐条核对上面输出 —"
