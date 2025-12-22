# 设备版本管理器（MVP）

目标：统一管理工厂局域网内各设备/设备集群的软件与固件版本；自动从设备拉取版本；建立“供应商接入标准”；提供版本基线（Baseline）与不一致告警，减少因版本不统一导致的不良品流出。

## Dashboard 能做什么

- 设备状态总览：`ok / mismatch / offline / no_baseline / never_polled`
- 一键设为基线：在“设备状态”表格中可将某台设备的“当前版本”快速设置为该 `集群 + 供应商 + 设备型号` 的基线
- 筛选与搜索：支持按状态筛选、关键字搜索（序列号/IP/产线/供应商/型号/版本），KPI 卡片也可点击快速筛选
- 受控文件变更提示：可配置受控文件（如软件配置、模板配置），设备上报 `files` 校验值后可提示变更

## 你真正需要的东西（第一性原理）

- **真实版本数据必须来自设备本体**：版本以“拉取（pull）”为准，而不是人工录入。
- **设备必须有稳定可实现的“版本接口标准”**：供应商接入成本要低，协议要少、字段要清晰、向后兼容。
- **要有可执行的“基线”**：每个集群/机型定义允许的版本（以及生效时间），系统才能判断“对/错”。
- **要可追溯**：每次拉取结果、失败原因、版本变化历史要可查。
- **要能落地到现场**：部署简单（单机可跑、SQLite），局域网可用，无需依赖云端。

## 当前实现（MVP）

- 管理服务（Python 标准库 + SQLite）：设备、集群、基线、版本目录、拉取记录
- 轮询拉取：按设备 IP/端口访问供应商实现的标准接口
- 简易 Web Dashboard：查看版本不一致、离线、最近拉取时间
- 供应商接入标准：`DVP(Device Version Protocol) v1`（见文档）

## 目录

- `src/version_manager/server.py`：HTTP 服务与 Dashboard
- `src/version_manager/db.py`：SQLite 数据层与初始化
- `src/version_manager/poller.py`：设备拉取连接器（默认 DVP v1 HTTP JSON）
- `docs/device_version_protocol_v1.md`：供应商设备接口标准（必须读）
- `docs/device-version.schema.json`：DVP v1 JSON Schema（供应商可直接验证）
- `docs/vendor_quickstart.md`：供应商接入快速指南（含示例返回）

## 运行

1) 初始化并启动服务：

```powershell
cd version_manage
python -m src.version_manager.server --host 0.0.0.0 --port 8080 --db .\data\vm.sqlite3 --default-cluster-name "新集群1" --poll-interval 30 --registration-token "change-me"
```

2) 打开：

- Dashboard：`http://localhost:8080/`
- API：`http://localhost:8080/api/v1/...`

3) 首次使用：访问 `http://127.0.0.1:8080/setup` 创建管理员，然后到 `http://127.0.0.1:8080/login` 登录。

提示：设备不会“自动出现在列表里”，你需要用 API 注册设备，或用 Dashboard 的“自动发现”功能扫描局域网并写入。

## 基本使用（最短路径）

1) 创建集群

```powershell
irm -Method Post http://localhost:8080/api/v1/clusters -ContentType application/json -Body '{"name":"产线A-视觉站"}'
```

2) 注册设备（供应商实现 DVP v1 接口后即可被拉取）

```powershell
irm -Method Post http://localhost:8080/api/v1/devices -ContentType application/json -Body '{
  "cluster_id": 1,
  "device_serial": "VISION-001",
  "supplier": "VendorX",
  "device_type": "VisionStation-3",
  "line_no": "Line-01",
  "ip": "192.168.10.21",
  "port": 80,
  "protocol": "dvp1-http",
  "auth": { "type": "none" }
}'
```

2.0) 设备主动注册（推荐）

设备/供应商程序可主动请求管理器注册自己（管理器会写入设备表，并可立即拉取一次验证；版本仍以拉取为准）：

```powershell
irm -Method Post http://localhost:8080/api/v1/register `
  -Headers @{ "X-Registration-Token" = "change-me" } `
  -ContentType application/json `
  -Body '{
    "cluster": { "name": "产线A-视觉站" },
    "device_serial": "VISION-001",
    "supplier": "VendorX",
    "device_type": "VisionStation-3",
    "prefer_remote_ip": true,
    "port": 80,
    "path": "/.well-known/device-version",
    "verify": true
  }'
```

2.1) 或者：自动发现并写入（适合现场先跑起来）

```powershell
irm -Method Post http://localhost:8080/api/v1/discover -ContentType application/json -Body '{
  "cluster_id": 1,
  "hosts": ["192.168.10.21","192.168.10.22"],
  "port": 80
}'
```

3) 设置基线（集群 + 供应商 + 设备型号 维度）

```powershell
irm -Method Post http://localhost:8080/api/v1/baselines -ContentType application/json -Body '{
  "cluster_id": 1,
  "supplier": "VendorX",
  "device_type": "VisionStation-3",
  "expected_main_version": "1.8.2",
  "note": "产线A-视觉站统一到 1.8.2"
}'
```

4) 触发拉取（全部设备）

```powershell
irm -Method Post http://localhost:8080/api/v1/poll -ContentType application/json -Body '{}'
```

## 自动轮询与告警

- 自动轮询：启动参数 `--poll-interval <秒>`（如 `30`）
- Webhook 告警：启动参数 `--webhook-url <url>`，当设备状态变化（ok/mismatch/offline/no_baseline）会 `POST` 一条事件 JSON
- 事件查询：`GET /api/v1/events?limit=50`

## 权限与登录

- 首次初始化：`/setup` 创建管理员账号（仅首次可用）
- 登录：`/login`（成功后会写入 `vm_session` Cookie）
- 写操作权限：所有写 API（新增/修改设备、改基线、discover/poll 等）需要管理员；只读 API 需要登录
- 脚本/集成（可选）：启动时设置 `--api-token <token>`，脚本请求带 Header `X-Api-Token: <token>` 可作为管理员调用 API（无需 Cookie）

## API 排查

- 管理器正在使用的数据库路径：`GET /api/v1/info`
- 设备列表：`GET /api/v1/devices`
- 拉取结果：`POST /api/v1/poll`
- 主动注册：`POST /api/v1/register`

## 删除/停用设备

- 停用（不再拉取）：`PUT /api/v1/devices/{id}` Body `{"enabled": false}`
- 删除（同时删除历史拉取记录）：`DELETE /api/v1/devices/{id}`

示例：

```powershell
irm -Method Delete http://127.0.0.1:8080/api/v1/devices/1
```

## 接下来你可能需要我做的

- 增加“允许版本范围/白名单”（如 `1.8.*`）与灰度策略
- 增加“适配器/网关 Agent”模式（供应商不便改固件时）
- 对接现场告警：邮件/企业微信/钉钉/声光
- 接入权限与审计（防止误改基线）
