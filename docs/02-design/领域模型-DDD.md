# 领域模型 · DDD 设计文档

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 日期 | 2026-06-22 |
| 方法论 | 领域驱动设计（DDD）：战略设计 + 战术设计 |
| 关联文档 | 《架构设计文档》《造数引擎设计文档》 |
| 范围 | 磷虾船及工厂数据 Mock 后端的领域模型 |

---

## 1. 文档目的

用 DDD 厘清本系统的**领域边界、通用语言与领域模型**，使代码结构由领域驱动而非技术驱动。本文档不重复架构/引擎细节，而是回答：系统里有哪些子域、如何切分限界上下文、各上下文的聚合与不变式是什么、上下文之间如何协作。

> 关键观察：本系统**以查询为主、写入极少**，是一个偏 CQRS 读模型的系统。"命令侧"几乎只剩两类——配置编排（人工维护维度/规则）与天气采集任务；其余全是确定性读模型。这一性质贯穿后续建模。

---

## 2. 战略设计

### 2.1 子域划分

| 子域 | 类型 | 说明 | 价值 |
|---|---|---|---|
| 确定性时序生成 | **核心域** | 造数引擎：种子纯函数 + 相干噪声，把"规则"变成可复现的逼真数据 | 系统最难、最具差异化的部分 |
| 指标供给 | **核心域** | 以指标为中心的取数模型、时间语义、来源编排（Resolver） | 对外价值的承载 |
| 溯源 | 支撑域 | 批次溯源图、递归原料、产品检验 | 业务语义最丰富 |
| 维度主数据 | 支撑域 | 人员、设备、专利、监控、商品、仓储 | 人工维护的参照数据 |
| 真实数据接入 | 支撑域 | open-meteo 采集落库、TimescaleDB 透传 | 与外部系统对接 |
| 监控视频 | 通用域 | RTSP 流（MediaMTX 循环推流） | 现成能力，包一层即可 |
| 配置与注册 | 通用域 | YAML 装载为内存注册表 | 各上下文的数据来源 |

### 2.2 限界上下文（Bounded Context）

1. **指标供给上下文（Metric Serving）**——核心。以 `指标` 为聚合，处理 series 查询、时间语义、`override → provider` 取数编排。定义 `数据源 Provider` 端口。
2. **造数上下文（Generation）**——核心。`computed` provider 的内部领域：生成规则、相干噪声、规则原语库。是供给上下文的"供应方"之一。
3. **溯源上下文（Traceability）**——以 `批次` 为聚合根，溯源类别/环节链/递归原料/产品检验。
4. **记录上下文（Records，主数据）**——人员、设备、专利、监控、商品、仓储等结构化记录聚合（按标识/条件检索）。
5. **真实数据接入上下文（Real-Data Integration）**——open-meteo 采集 + SQLite 落地、TimescaleDB 只读透传；对外部数据模型做防腐。是供给上下文的另一类"供应方"。
6. **监控视频上下文（Surveillance）**——摄像头清单 + RTSP 地址供给，对 MediaMTX 防腐。
7. **配置上下文（Configuration）**——共享内核：渔季、单位、地理点、批次编号等通用语言载体，及各上下文的配置仓储来源。

### 2.3 上下文映射（Context Map）

```
                         ┌──────────────────────────────┐
                         │   指标供给上下文 (核心)        │
                         │   Port: DataSourceProvider     │
                         │   (Open Host / Published Lang) │
                         └──────┬──────────────┬──────────┘
            Customer-Supplier   │              │   Customer-Supplier
                 (Conformist)   │              │   + ACL
                         ┌──────▼─────┐   ┌────▼─────────────────┐
                         │ 造数上下文  │   │ 真实数据接入上下文    │
                         │ (computed)  │   │ open-meteo / TSDB     │
                         └────────────┘   └───────┬──────────────┘
                                                  │ ACL（外部 API/库）
                                          ┌───────▼────────┐
                                          │ open-meteo /    │
                                          │ TimescaleDB     │
                                          └─────────────────┘

   ┌───────────────┐  Shared Kernel: 批次编号   ┌───────────────┐
   │ 溯源上下文     │◄──────────────────────────►│ 记录上下文     │
   │ (Batch 聚合)   │                            │ (含 仓储/物流) │
   └───────────────┘                            └───────────────┘

   ┌───────────────┐  ACL          ┌──────────────┐
   │ 监控视频上下文 │──────────────►│ MediaMTX(外部)│
   └───────────────┘               └──────────────┘

   配置上下文（共享内核：渔季 / 单位 / 地理点 / 批次编号）──供给所有上下文
```

关系模式说明：

- **供给 ↔ 造数 / 真实数据接入**：客户-供应商（Customer-Supplier）。供给上下文定义 `DataSourceProvider` 端口作为发布语言（Published Language）；两类来源作为供应方遵奉该端口（Conformist）。真实数据接入内部再以**防腐层（ACL）**隔离 open-meteo / TimescaleDB 的外部模型。
- **溯源 ↔ 维度（仓储）**：共享内核（Shared Kernel）——共享 `批次编号` 这一身份标识。
- **监控视频 → MediaMTX**：防腐层包裹外部流媒体服务（通用域）。
- **配置上下文**：共享内核，承载跨上下文的通用值对象（渔季、单位、地理点、批次编号）。

### 2.4 通用语言（Ubiquitous Language）

**系统/技术域术语**

| 术语 | 含义 |
|---|---|
| 指标（Metric） | 一个可按时间取值的量，全限定 key = `实体[.子实体].指标` |
| 种类（Kind） | 指标的取值语义：瞬时 / 累计 |
| 数据点（DataPoint） | `{time, value}`，时序的最小单元 |
| 时间区间（TimeRange） | `[start, end]` 左闭右闭；同值=点查；空=即时 |
| 数据源（Source/Provider） | 指标取值来源：computed / sqlite / timescaledb |
| 生成规则（GenerationRule） | computed 指标的造数声明（kind + 参数） |
| 相干噪声 / fBm | 平滑、可复现的程序化噪声（见造数引擎文档） |
| 覆盖（Override） | 对某指标某时段强制返回固定值（剧情/冻结/兜底） |
| 渔季（FishingSeason） | 可跨年的捕捞季时间窗 |

**业务域术语**

| 术语 | 含义 |
|---|---|
| 批次（Batch） | 一段产品的唯一批次编号，溯源/物流的身份锚点 |
| 溯源类别 | 虾肉/虾油/脱脂虾粉/蛋白肽/虾油胶囊/全虾粉/冻虾 等溯源 |
| 环节（TraceLink） | 溯源链上的一段，如 磷虾捕捞、船载加工、冷链运输 |
| 溯源链（TraceChain） | 某类别下按业务顺序排列的环节及其批次编号 |
| 原料来源追溯 | 批次的上游原料信息，可**递归**指向上游批次的溯源 |
| 产品检验 | 批次的第三方/自检报告（项目、结论、机构） |
| 在岗人员 | 带时序属性（职务/位置/在岗）的人员 |
| 监控（Camera） | 一路视频监控，含名称与 RTSP 地址 |
| 渔季累计 / 当月累计 | 累计指标在窗口端点求差得到的量 |

---

## 3. 战术设计（按限界上下文）

记号：**〔AR〕**聚合根、**〔E〕**实体、**〔VO〕**值对象、**〔DS〕**领域服务、**〔R〕**仓储、**〔P〕**端口、**〔EVT〕**领域事件。

### 3.1 指标供给上下文（Metric Serving）

- **〔AR〕Metric 指标**：身份 = `MetricKey`。持有 `种类`、`值类型`、`单位`、`网格`、`SourceRef`、`图表元数据`。聚合很小（配置型），不可变。
- **〔VO〕MetricKey**：全限定路径，规整化、可比较。
- **〔VO〕TimeRange**：`start/end`，不变式 `start ≤ end`；语义分点查/区间/即时。
- **〔VO〕Grid**：网格粒度（3min/1h/1d…）。
- **〔VO〕DataPoint**：`{time, value}`；value 为 `Number/String/Boolean`。
- **〔VO〕TimeSeries**：`{type, unit, values[]}`，series 的返回载体。
- **〔VO〕SourceRef**：`{kind, params}`，指向某 Provider。
- **〔E/VO〕Override**：`{metricKey, range, value}`；策略对象 OverridePolicy 决定命中。
- **〔P〕DataSourceProvider**：`fetch(metric, range) -> TimeSeries`。**这是本上下文对外发布的语言**，由造数/真实数据接入实现。
- **〔DS〕Resolver**：`resolve(metric, range)`：先查 Override，否则按 `source.kind` 分发到对应 Provider。编排者，本身不含数据。
- **〔R〕MetricRegistry**：只读仓储，从配置加载全部 Metric。

不变式：`source.kind` ∈ 已注册的 Provider 种类；点查/即时返回单点；区间返回按网格枚举的序列。

### 3.2 造数上下文（Generation）

实现 `DataSourceProvider`（computed）。详见《造数引擎设计文档》，此处给领域模型骨架。

- **〔VO〕RuleSpec 生成规则**：`{kind, params}`，不可变。kind ∈ 有界瞬时 / 累计 / 离散状态 / 航迹 / …
- **〔VO〕Seed 种子**：由 `(key, anchorIndex, channel)` 哈希得到，确定性。
- **〔VO〕NoiseField 相干噪声场**：锚点 + 平滑插值 + fBm 倍频。
- **〔VO〕Envelope 包络**：趋势/季节的确定性闭式。
- **〔DS〕GenerationEngine 造数引擎**：`generate(rule, key, t) -> value`，纯函数、无状态。
- **〔DS〕RulePrimitive 规则原语**（策略族）：有界瞬时 / 累计 / 离散状态 / 航迹派生 等，每个是 `GenerationEngine` 的一个策略。
- **〔VO〕MetricDependency 指标依赖图**：一个指标的规则可引用其它指标，构成有向无环图；`GenerationEngine` 在同一 `t` 递归求值并记忆化，用于实现指标间硬约束（按构造派生，详见造数引擎文档 §5.2）。

不变式：相同 `(key, t)` 恒返回相同 `value`（可复现）；累计型满足单调（速率基线 > 漂移最大斜率）；派生量（航向/航速）与位置函数自洽；**指标依赖图必须无环**。

### 3.3 溯源上下文（Traceability）

业务语义最丰富，按聚合建模递归图。

- **〔AR〕Batch 批次**：身份 = `BatchNumber`。聚合内含：所属溯源类别、溯源链、原料来源追溯、产品检验。
- **〔VO〕BatchNumber**：批次编号（与维度/物流共享内核）。
- **〔VO〕TraceabilityCategory 溯源类别**：决定环节集合与**顺序**（来自附录 A 映射）。
- **〔VO〕TraceLink 环节**：`{环节名, 批次编号}`。
- **〔VO〕TraceChain 溯源链**：按类别顺序排列的 `TraceLink` 序列。
- **〔VO〕MaterialSource 原料来源追溯**：捕捞海域/方式/时间 或 生产工艺/位置；**可递归引用上游 `BatchNumber`**（按身份引用，不嵌入，遵守聚合边界）。
- **〔E〕ProductInspection 产品检验**：报告编号、检测项目、结论、机构、是否第三方。
- **〔VO〕GeoPoint**：`{经度, 纬度}`（共享内核）。
- **〔DS〕TraceabilityAssembler 溯源装配器**：给定 `BatchNumber` → 定类别 → 装配链 → **递归展开**原料溯源（按需、可控深度）。
- **〔R〕BatchRepository**：按编号取批次（配置/图）。

不变式：溯源链的环节与顺序须**符合其类别的定义**；`MaterialSource.原料溯源` 引用的上游批次必须存在；递归终止于无上游或达到深度上限。

### 3.4 记录上下文（Records，主数据）

- **〔AR〕Person 人员**：身份=人员编号/姓名。标量：姓名、电话；时序属性：职务、所在位置、类型、是否在岗。
  - **〔VO〕TemporalValue**：`{time, value}`。
  - **〔VO〕TemporalAttribute**：`TemporalValue` 升序序列（不变式：时间升序）。
- **〔AR〕Equipment 设备**：名称、所属工艺流程/阶段、状态、温度。
- **〔AR〕Patent 专利**、**〔AR〕Product 商品**、**〔AR〕StorageRecord 仓储记录**（物流，见 3.5）。
- **〔R〕RecordRepository**：按类型 + filter 查询；人员时序属性整段返回。

不变式：时序属性时间升序；仓储记录字段齐备（9 字段）。

### 3.5 物流（仓储）——与溯源共享内核

物流在接口上并入维度（类型=仓储），但领域上与溯源共享 `BatchNumber` 身份。

- **〔AR〕StorageRecord 仓储记录**：身份 = `(BatchNumber, 仓库类别)`。字段：货物名称、批号、数量、规格、保质期、生产/入库/出库日期、冷藏状态。
- **共享内核**：`BatchNumber`、`GeoPoint`、`ProductInspection`（仓储也可携带检验摘要）与溯源上下文共享，须协同演进。

### 3.6 真实数据接入上下文（Real-Data Integration）

实现 `DataSourceProvider`（sqlite / timescaledb），并以 ACL 隔离外部模型。

- **〔E〕WeatherReading 天气读数**：落在本地 SQLite（time, 指标, value）。
- **〔E〕IngestionJob 采集任务**：定时拉 open-meteo → upsert SQLite。
- **〔DS〕WeatherIngestionService**：执行采集、字段映射、回填。
- **〔DS〕TimescaleQueryService**：把 `TimeRange` 翻译为 SQL，只读透传。
- **〔P/ACL〕OpenMeteoClient**：外部天气 API 防腐适配（外部字段 → 本系统指标/单位）。
- **〔P/ACL〕TimescaleDbAdapter**：远程库行 → `DataPoint`。
- **〔EVT〕WeatherIngested**：一批天气读数写入后发出（少数领域事件之一）。

不变式：sqlite provider 只读本地落地数据；timescaledb provider 不在本地落库；外部模型不得泄漏进核心域（经 ACL 转译为 `DataPoint`）。

### 3.7 监控视频上下文（Surveillance）

- **〔AR〕Camera 监控**：身份=监控ID。名称、所属、RTSP 地址、循环片源。
- **〔DS〕SurveillanceCatalog**：列出各监控及其 RTSP 地址。
- **〔P/ACL〕StreamingServerPort（MediaMTX）**：把片源以 RTSP 循环推流；对前端只暴露地址，不经手视频字节。

### 3.8 配置上下文（共享内核）

- **〔VO〕FishingSeason 渔季**：`{开始, 结束}`，支持跨年（开始>结束表示跨年环绕）。
- **〔VO〕Unit 单位**、**〔VO〕GeoPoint**、**〔VO〕BatchNumber**：跨上下文复用。
- 各上下文的"配置仓储"以 YAML 为来源；Season/Unit 等作为发布语言。

---

## 4. 关键聚合详解

### 4.1 Batch（溯源）——递归图的聚合边界

- **聚合根**选 `Batch`，因为溯源查询、链、检验、原料都围绕"一个批次"形成一致性边界。
- **递归用身份引用而非嵌入**：`MaterialSource.原料溯源` 持上游 `BatchNumber`，由 `TraceabilityAssembler` 按需加载。避免一个聚合实例无限膨胀，遵守"聚合内强一致、聚合间最终一致/按引用"。
- **顺序不变式**：链必须符合类别定义的环节顺序（附录 A）。装配时校验。

### 4.2 Metric（供给）——小聚合 + 端口分发

- Metric 本身只是不可变配置；"取值"是 `Resolver`（领域服务）经 `DataSourceProvider`（端口）完成。
- 这把"指标是什么"（核心域稳定）与"数据从哪来"（供应方可插拔）解耦——新增数据源不动 Metric 聚合。

### 4.3 Person（维度）——时序属性是值对象序列

- 人员的职务/位置/在岗是**随时间变化的值对象序列**，整体作为人员聚合的一部分；不变式为时间升序。变更由人工维护（配置），属命令侧的少数写入之一。

---

## 5. 领域事件与 CQRS

- 系统**读重写轻**：series/records/trace/surveillance 全是查询（读模型）。
- 写侧仅两处：① 配置编排（人工维护维度/规则/渔季/覆盖）——本质是模型装载；② 天气采集任务——产生少量领域事件。
- 唯一显著领域事件：**`WeatherIngested`**（采集批次写入完成）。其余上下文无状态变更，故无需事件溯源、无需复杂一致性协议。
- 这一性质决定：不引入消息总线、不做 CQRS 框架；读模型即领域模型本身，命令侧极薄。

---

## 6. 六边形架构映射（Ports & Adapters）

| 层 | 内容 |
|---|---|
| **领域层** | 聚合（Metric/Batch/Person/Camera…）、值对象、领域服务（Resolver、GenerationEngine、TraceabilityAssembler、SurveillanceCatalog） |
| **应用层** | 用例：QuerySeries、QueryRecords、QueryTraceability、ListSurveillance、RunWeatherIngestion |
| **端口（Ports）** | DataSourceProvider、各 Repository、OpenMeteoClient、TimescaleDbPort、StreamingServerPort |
| **适配器（Adapters）** | 入站：FastAPI 控制器（series/records/surveillance）；出站：YAML 配置仓储、SQLite 仓储、Timescale 适配器、open-meteo HTTP 客户端、MediaMTX 适配器 |

依赖方向：适配器 → 应用层 → 领域层；领域层只依赖端口接口，不依赖具体技术。

---

## 7. 映射到代码结构

DDD 模型可落到（与《架构设计文档》第 10 章一致，按上下文归拢）：

```
app/
├── serving/         # 指标供给上下文：metric, resolver, provider 端口, registry
├── generation/      # 造数上下文：engine, rules（原语库）= computed provider
├── traceability/    # 溯源上下文：batch 聚合, assembler, repository
├── records/         # 记录上下文：person/equipment/...、仓储
├── integration/     # 真实数据接入：open-meteo 采集, sqlite/timescaledb provider, ACL
├── surveillance/    # 监控视频上下文：camera, catalog, mediamtx 适配
├── shared/          # 共享内核：FishingSeason, Unit, GeoPoint, BatchNumber, DataPoint
└── api/             # 入站适配器：FastAPI 路由 + 响应包络
```

> 与按技术分层（generators/providers/services）相比，按限界上下文分包更贴合领域演进：一个业务变更通常落在单一上下文内。两种组织方式可融合——上下文为一级目录，技术构件为其内部细分。

---

## 8. 附录：聚合 / 服务 速查

| 限界上下文 | 聚合根 | 关键值对象 | 领域服务 | 端口/ACL |
|---|---|---|---|---|
| 指标供给 | Metric | MetricKey/TimeRange/DataPoint/TimeSeries/SourceRef | Resolver | DataSourceProvider |
| 造数 | （无状态） | RuleSpec/Seed/NoiseField/Envelope | GenerationEngine、规则原语 | 实现 DataSourceProvider |
| 溯源 | Batch | BatchNumber/TraceChain/MaterialSource/GeoPoint | TraceabilityAssembler | BatchRepository |
| 记录（主数据） | Person/Equipment/Patent/Product | TemporalAttribute/TemporalValue | — | RecordRepository |
| 物流（仓储） | StorageRecord | BatchNumber(共享)/ProductInspection | — | （并入维度） |
| 真实数据接入 | WeatherReading/IngestionJob | — | WeatherIngestionService/TimescaleQueryService | OpenMeteoClient/TimescaleDbAdapter |
| 监控视频 | Camera | RTSP 地址 | SurveillanceCatalog | StreamingServerPort(MediaMTX) |
| 配置 | — | FishingSeason/Unit/GeoPoint/BatchNumber | — | 配置仓储 |

---

*（本文档为领域模型设计稿，随业务理解深化迭代上下文边界与聚合不变式。）*
