# 设备主动注册（Push Registration）

目的：当设备已经在产线运行时，由设备主动向版本管理器“报到”，自动写入设备清单，减少现场手工录入/扫描成本。

## 重要原则

- 主动注册 **只负责写入“设备在哪里/怎么拉取”**；版本号仍然由管理器按 DVP v1 从设备拉取，避免人为伪造版本。

## 端点

- `POST /api/v1/register`

鉴权（可选）：

- 如果管理器启动时设置了 `--registration-token`，则设备必须提供：
  - Header：`X-Registration-Token: <token>`
  - 或 Body：`registration_token`

## 请求 JSON

必填：

- `device_key`：设备在管理系统中的唯一键（推荐与设备出厂唯一 ID 对齐）
- `vendor`、`model`
- `line_no`（可选）：产线号/工位号，用于现场分组展示与管理

集群选择（三选一）：

- `cluster.id`
- `cluster.name`（要求集群已存在）
- 启动参数 `--default-cluster-id`（当请求不带 cluster 时使用）

网络信息（两种方式二选一）：

- 直接给：`ip`、`port`、`path`（默认 path `/.well-known/device-version`）
- 或给：`dvp_url`（如 `http://192.168.10.21/.well-known/device-version`）

推荐：

- `prefer_remote_ip: true`：由管理器使用请求来源 IP，避免设备误填 `127.0.0.1`
- `verify: true`：注册后立即拉取一次并记录快照

## 示例

```json
{
  "cluster": { "name": "产线A-视觉站" },
  "device_key": "VISION-001",
  "vendor": "VendorX",
  "model": "VisionStation-3",
  "prefer_remote_ip": true,
  "port": 80,
  "path": "/.well-known/device-version",
  "verify": true
}
```
