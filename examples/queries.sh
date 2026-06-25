#!/usr/bin/env bash
# 验收用例：三个专用接口（/api/value /records /series）。先 `docker compose up -d --build`。
# 用法：bash examples/queries.sh
set -e
H="${1:-http://localhost:8000}"
# 使用侧 Token：与 .env 的 API_TOKEN_APP 一致（AUTH_ENABLED=false 时可留空）
APP="${APP_TOKEN:-请填使用侧Token}"
pp() { python3 -m json.tool --no-ensure-ascii 2>/dev/null || cat; }
post() { echo -e "\n### $1"; curl -s -X POST "$H$2" -H 'Content-Type: application/json' -H "Authorization: Bearer $APP" -d "$3" | pp; }

echo "### 健康+自检（公开，无需Token；selfcheck_ok=true, keys≈216(含展开子key), derivations=5）"; curl -s "$H/api/health" | pp

# —— A 表：/api/value（批量取值）——
post "A·value 批量：船舶信息 + 虾油线设计能力" /api/value \
  '{"keys":["船舶.信息","工厂.虾油线.设计能力"]}'

# —— B 表：/api/records（批量，可按 key 分别给 filter）——
post "B·records 批量：溯源(虾油) + 仓储(工厂冷库) + 人员(磷虾船)" /api/records \
  '{"keys":["溯源","仓储","人员"],"filter":{"溯源":{"产品批号":"2606AKO01"},"仓储":{"仓库类别":"工厂冷库"},"人员":{"所属":"磷虾船"}}}'

post "B·records ★金蝶真实标定：虾油检测（磷脂≈60.7/EPA16.2/DHA7.58/虾青素181mg·kg/酸价7.8）" /api/records \
  '{"keys":["检测"],"filter":{"检测":{"产品":"虾油","批次":"2606AKO01"}}}'

# —— C 表：/api/series（批量时序）——
# 寻址：扁平化后只用 keys（指标=独立全限定 key，无 metrics）。
# 时间+精度：可选 window={key:{start?,end?,points?}}，逐 key 自洽，无顶层共享默认。
#   start/end 都不传=当前时刻单点；相等=该时刻单点；end>start=区间。
#   points 缺省→配置 默认点数→全局 20；区间按 N 桶自适应比例尺。
post "C·series 当前时刻：冻虾舱温度 + 海况叶子(海水温度)（无 window）" /api/series \
  '{"keys":["船舶.冻虾舱.温度","船舶.海况.海水温度"]}'

post "C·series 某时刻单点：冻虾舱温度（start==end）" /api/series \
  '{"keys":["船舶.冻虾舱.温度"],"window":{"船舶.冻虾舱.温度":{"start":"2026-06-18 12:00:00","end":"2026-06-18 12:00:00"}}}'

post "C·series 区间：冻虾舱温度 00:00~06:00（≤20点、平滑）" /api/series \
  '{"keys":["船舶.冻虾舱.温度"],"window":{"船舶.冻虾舱.温度":{"start":"2026-06-18 00:00:00","end":"2026-06-18 06:00:00"}}}'

post "C·series ★固定点数：海水温度查一整年 points=12（自适应比例尺→恰12点）" /api/series \
  '{"keys":["船舶.海况.海水温度"],"window":{"船舶.海况.海水温度":{"start":"2026-01-01 00:00:00","end":"2026-12-31 00:00:00","points":12}}}'

post "C·series ★派生：虾油得率（扁平叶子 key，应≈18% 金蝶真实出油率）" /api/series \
  '{"keys":["工厂.虾油线.生产数据.虾油得率"],"window":{"工厂.虾油线.生产数据.虾油得率":{"start":"2026-06-18 12:00:00","end":"2026-06-18 12:00:00"}}}'

post "C·series 派生：剩余燃油百分比（=剩余燃油/1000*100，当前时刻）" /api/series \
  '{"keys":["船舶.能耗.剩余燃油百分比"]}'

post "C·series 引用：车间外大气=工厂.天气.温度（同一事实只建一处，直接查叶子）" /api/series \
  '{"keys":["工厂.天气.温度"],"window":{"工厂.天气.温度":{"start":"2026-06-18 12:00:00","end":"2026-06-18 12:00:00"}}}'

post "C·series 离散：拖网绞车状态（运行/待机/停止，当前时刻）" /api/series \
  '{"keys":["船舶.桁杆泵吸系统.拖网绞车.状态"]}'

post "航迹：船舶航行（经纬度/航速/航向 派生一致，当前时刻）" /api/series \
  '{"keys":["船舶.航行"]}'

post "分组发现：查分组容器 船舶.海况 → 返回 子 key 列表（提示查叶子）" /api/series \
  '{"keys":["船舶.海况"]}'

# —— 一屏批量：一次取多叶子 key、每 key 各自时间窗/点数（前端拼一屏的典型用法）——
post "一屏批量·异构窗口：航迹近1天(200点) + 累计耗油整年 + 海水温度近6h(30点)" /api/series \
  '{"keys":["船舶.航行","船舶.能耗.累计耗油量","船舶.海况.海水温度"],"window":{"船舶.航行":{"start":"2026-06-24 00:00:00","end":"2026-06-25 00:00:00","points":200},"船舶.能耗.累计耗油量":{"start":"2026-01-01 00:00:00","end":"2026-06-25 00:00:00"},"船舶.海况.海水温度":{"start":"2026-06-24 18:00:00","end":"2026-06-25 00:00:00","points":30}}}'

# —— 误用保护：用错接口会被明确指出 ——
post "误用：把 A 表 key 打到 /series（应返回 error 提示用 /value）" /api/series \
  '{"keys":["船舶.信息"]}'

echo -e "\n— 全部用例已发出，逐条核对 —"
