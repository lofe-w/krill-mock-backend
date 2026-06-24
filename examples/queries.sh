#!/usr/bin/env bash
# 验收用例：三个专用接口（/api/value /records /series）。先 `docker compose up -d --build`。
# 用法：bash examples/queries.sh
set -e
H="${1:-http://localhost:8000}"
pp() { python3 -m json.tool --no-ensure-ascii 2>/dev/null || cat; }
post() { echo -e "\n### $1"; curl -s -X POST "$H$2" -H 'Content-Type: application/json' -d "$3" | pp; }

echo "### 健康+自检（selfcheck_ok=true, keys≈58, derivations=5）"; curl -s "$H/api/health" | pp

# —— A 表：/api/value（批量取值）——
post "A·value 批量：船舶信息 + 虾油线设计能力" /api/value \
  '{"keys":["船舶.信息","工厂.虾油线.设计能力"]}'

# —— B 表：/api/records（批量，可按 key 分别给 filter）——
post "B·records 批量：溯源(虾油) + 仓储(工厂冷库) + 人员(磷虾船)" /api/records \
  '{"keys":["溯源","仓储","人员"],"filter":{"溯源":{"商品编号":"XY-001"},"仓储":{"仓库类别":"工厂冷库"},"人员":{"所属":"磷虾船"}}}'

post "B·records ★金蝶真实标定：虾油检测（磷脂≈60.7/EPA16.2/DHA7.58/虾青素181mg·kg/酸价7.8）" /api/records \
  '{"keys":["检测"],"filter":{"检测":{"产品":"虾油","批次":"XYTQ-2026-001"}}}'

# —— C 表：/api/series（批量时序；点查 / 区间）——
post "C·series 点查：冻虾舱温度 + 海况(多指标)" /api/series \
  '{"keys":["船舶.冻虾舱.温度","船舶.海况"],"time":"2026-06-18 12:00:00"}'

post "C·series 区间：冻虾舱温度 00:00~06:00（应 7 点、平滑）" /api/series \
  '{"keys":["船舶.冻虾舱.温度"],"start":"2026-06-18 00:00:00","end":"2026-06-18 06:00:00"}'

post "C·series 确定性：同查询再来一次（值与上面点查相同）" /api/series \
  '{"keys":["船舶.冻虾舱.温度"],"time":"2026-06-18 12:00:00"}'

post "C·series ★派生：虾油得率（=成品油/虾粉，应≈18% 金蝶真实出油率）" /api/series \
  '{"keys":["工厂.虾油线.生产数据"],"time":"2026-06-18 12:00:00","指标":"虾油得率"}'

post "C·series 派生：剩余燃油百分比（=剩余燃油/1000*100）" /api/series \
  '{"keys":["船舶.能耗.剩余燃油百分比"],"time":"2026-06-18 12:00:00"}'

post "C·series 引用：车间外大气.温度（=工厂.天气.温度，引用不复制）" /api/series \
  '{"keys":["工厂.虾油线.生产数据"],"time":"2026-06-18 12:00:00","指标":"车间外大气.温度"}'

post "C·series 离散：拖网绞车状态（运行/待机/停止）" /api/series \
  '{"keys":["船舶.桁杆泵吸系统.拖网绞车.状态"],"time":"2026-06-18 12:00:00"}'

post "航迹：船舶航行（经纬度/航速/航向 派生一致）" /api/series \
  '{"keys":["船舶.航行"],"time":"2026-06-18 12:00:00"}'

# —— 误用保护：用错接口会被明确指出 ——
post "误用：把 A 表 key 打到 /series（应返回 error 提示用 /value）" /api/series \
  '{"keys":["船舶.信息"]}'

echo -e "\n— 全部用例已发出，逐条核对 —"
