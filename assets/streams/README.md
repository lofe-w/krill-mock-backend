# 监控片源放置规则（你按此补视频）

把每路监控的视频文件放到本目录 `assets/streams/`，**文件名 = 监控ID**。MediaMTX 会
自动把它循环推成 RTSP/HLS 流；`/api/records` 的 `监控.清单` 已返回对应地址。

## 命名规则（必须遵守）
- 文件名：`<监控ID>.mp4`（小写英文 + 连字符，**和 config 里 监控.清单 的监控ID完全一致**）
- 一路监控一个文件；循环播放，时长随意（建议 ≥10 秒）

## 当前需要的监控ID（共 8 个，见 config/registry/主数据.yaml 的 监控.清单）
| 监控ID | 监控名称 | 文件名 |
|---|---|---|
| cam-deck | 甲板监控（船） | `cam-deck.mp4` |
| cam-catch | 捕捞作业区（船） | `cam-catch.mp4` |
| cam-krillmeal | 虾粉加工车间（船） | `cam-krillmeal.mp4` |
| cam-coldhold | 冷冻舱（船） | `cam-coldhold.mp4` |
| cam-gate | 厂区入口（厂） | `cam-gate.mp4` |
| cam-oil | 虾油车间（厂） | `cam-oil.mp4` |
| cam-peptide | 蛋白肽车间（厂） | `cam-peptide.mp4` |
| cam-warehouse | 成品仓库（厂） | `cam-warehouse.mp4` |

## 编码建议
- 视频编码 **H.264**（MediaMTX/ffmpeg 兼容好）；无音轨即可（推流已 `-an` 去音）。
- 分辨率随大屏需要（720p/1080p 均可）。

## 验证
1. 文件放好 → `docker compose up -d`（会一起拉起 mediamtx）。
2. 取流测试：`ffplay rtsp://localhost:8554/cam-deck` 或浏览器播 HLS `http://localhost:8888/cam-deck/index.m3u8`。
3. 大屏从 `/api/records {"keys":["监控.清单"],"filter":{"监控.清单":{"所属":"磷虾船"}}}` 拿地址播放。

> 视频字节由播放器直连 RTSP/HLS，**不经过数据后端**；后端只负责返回地址。
