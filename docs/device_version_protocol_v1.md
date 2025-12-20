# DVP(Device Version Protocol) v1 — 供应商设备版本接口标准

目的：让版本管理器能在局域网中以**统一方式**从不同供应商设备直接拉取版本信息，并且字段稳定、实现简单、可扩展。

## 设计原则

- **设备端实现成本低**：一个 HTTP GET + JSON 返回即可（无需数据库、无需复杂鉴权）。
- **字段可扩展且向后兼容**：未知字段必须忽略；新增字段不破坏旧客户端。
- **可用于追溯**：包含“设备唯一标识、主版本、构建信息、组件版本”。
- **可选安全**：局域网默认不加密也能用，但支持 Token 或 mTLS（后续版本）。

## 传输层要求（MUST/SHOULD）

- MUST：提供 HTTP 服务，支持 `GET`
- MUST：返回 `Content-Type: application/json; charset=utf-8`
- MUST：在 2 秒内响应（或明确返回 503），避免阻塞产线网络
- SHOULD：支持 Keep-Alive
- SHOULD：接口路径固定且不与业务接口冲突

## 标准端点

### 1) 获取版本信息（必选）

- `GET /.well-known/device-version`

请求头（可选鉴权）：

- `Authorization: Bearer <token>`（可选）
- 或 `X-Device-Token: <token>`（可选）

成功响应：HTTP 200 + JSON（见下方字段）

失败响应：

- HTTP 401：鉴权失败
- HTTP 404：不支持该协议（将被管理器判定为未接入）
- HTTP 503：设备忙/暂不可用

### 2) 获取协议/设备能力（可选）

- `GET /.well-known/device-version/capabilities`

### 3) 健康检查（可选）

- `GET /healthz`（200 表示在线）

## 返回 JSON 字段（v1）

顶层字段：

- `protocol` (string, MUST)：固定为 `"dvp"`
- `protocol_version` (integer, MUST)：固定为 `1`
- `device` (object, MUST)
- `versions` (object, MUST)
- `components` (array, SHOULD)
- `build` (object, SHOULD)
- `timestamp` (string, SHOULD)：设备端生成的 ISO-8601 时间

### device

- `device.id` (string, MUST)：设备唯一 ID（推荐用序列号/出厂唯一编码）
- `device.supplier` (string, MUST)：供应商名（稳定值）
- `device.device_type` (string, MUST)：设备型号（稳定值）
- `device.serial` (string, SHOULD)：序列号（可与 id 相同）


### versions

- `versions.main` (string, MUST)：主版本号（推荐语义化版本 SemVer，如 `1.8.2`）
- `versions.firmware` (string, SHOULD)：固件版本
- `versions.bootloader` (string, SHOULD)

### components（可选，但强烈推荐）

数组元素对象：

- `name` (string, MUST)：组件名（如 `vision-algo`、`plc-bridge`）
- `version` (string, MUST)：组件版本号
- `checksum` (string, SHOULD)：推荐 `sha256:<hex>`
- `build` (string, SHOULD)：构建号/提交号

### build（可选）

- `build.git` (string, SHOULD)：提交哈希
- `build.time` (string, SHOULD)：ISO-8601 构建时间
- `build.image` (string, SHOULD)：容器镜像 tag（如适用）

## 示例返回

```json
{
  "protocol": "dvp",
  "protocol_version": 1,
  "timestamp": "2025-12-17T08:40:10Z",
  "device": { "id": "VISION-001", "supplier": "VendorX", "device_type": "VisionStation-3", "serial": "VS3-24001" },
  "versions": { "main": "1.8.2", "firmware": "F3.2.0" },
  "components": [
    { "name": "vision-algo", "version": "2.4.1", "checksum": "sha256:..." },
    { "name": "ui", "version": "1.8.2", "build": "20251217.1" }
  ],
  "build": { "git": "8c1a2d9", "time": "2025-12-16T12:01:03Z" }
}
```

## 兼容性与演进

- 管理器以 `protocol` + `protocol_version` 判断解析器
- v1 只保证上述 MUST 字段；供应商可自定义字段，管理器会保留原始 JSON 以便追溯
- 如需扩展鉴权/签名：建议在 v2 引入 `signing` 字段与 mTLS 支持
