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
- `files` (array, SHOULD)：受控文件摘要（用于配置/模板等变更监控）
- `main_version_info` (object, SHOULD)：`versions.main` 的版本更新信息（设备端与版本号一起上报；v1 扩展）
- `docs` (array, SHOULD)：内联文档（通常 Markdown，用于未来 AI 分析；v1 扩展）
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

### files（可选，受控文件摘要）

数组元素对象：

- `path` (string, MUST)：文件路径/标识（建议稳定且可读，如 `/etc/app/config.yml`）
- `checksum` (string, SHOULD)：推荐 `sha256:<hex>`（或其他稳定校验值）
- `size` (integer, SHOULD)：文件大小（字节）
- `mtime` (string|integer, SHOULD)：修改时间（ISO-8601 或 Unix 时间戳）
- `encoding` (string, SHOULD)：当提供内容时的文本编码（如 `utf-8`）
- `content_type` (string, SHOULD)：内容类型（如 `application/json`、`text/yaml`）
- `content_b64` (string, SHOULD)：文件内容的 Base64（适合小文件；可被管理器截断）
- `truncated` (boolean, SHOULD)：设备端指示内容是否已截断（可选）

说明（管理器验证方式）：

- 管理器侧可配置“受控文件规则”：按 `集群 + 供应商 + 设备型号` 维护一组 glob，用于匹配 `files[].path`
- 仅对命中规则的 `path` 做比对，并且是“本次成功拉取” vs “上一次成功拉取”
- 优先使用 `checksum` 做变化判断；如果缺少 `checksum` 但提供了 `size`/`mtime`，管理器会退化为比较 `size+mtime` 组合（准确性不如内容哈希）
- 如果检测到变化，会生成 `controlled_files_change` 事件用于提示/告警

### 受控文件内容端点（可选，推荐）

当不希望在 `device-version` 中内联大文件/敏感内容时，可以额外实现“按需拉取文件内容”端点：

- `GET /.well-known/device-version/file?path=<urlencoded>`
- 需要鉴权时沿用 `device-version` 的鉴权方式（Bearer / X-Device-Token）
- 成功：HTTP 200 + JSON（至少包含 `path` 与 `content_b64`）
- 失败：HTTP 404（不提供该文件），或 401/503 等

返回示例：

```json
{
  "path": "/etc/app/config.yml",
  "encoding": "utf-8",
  "content_type": "text/yaml",
  "content_b64": "Li4u"
}
```

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
  "main_version_info": { "changelog_md": "## 1.8.2\\n- Fix: ...", "released_at": "2025-12-16T12:01:03Z", "checksum": "sha256:..." },
  "components": [
    { "name": "vision-algo", "version": "2.4.1", "checksum": "sha256:..." },
    { "name": "ui", "version": "1.8.2", "build": "20251217.1" }
  ],
  "files": [
    { "path": "/etc/app/config.yml", "checksum": "sha256:..." },
    { "path": "/opt/app/templates/default.json", "checksum": "sha256:..." }
  ],
  "docs": [
    { "name": "files_and_params.md", "content_type": "text/markdown", "encoding": "utf-8", "checksum": "sha256:...", "content_b64": "Li4u" },
    { "name": "device_features.md", "content_type": "text/markdown", "encoding": "utf-8", "checksum": "sha256:...", "content_b64": "Li4u" }
  ],
  "build": { "git": "8c1a2d9", "time": "2025-12-16T12:01:03Z" }
}
```

## 兼容性与演进

- 管理器以 `protocol` + `protocol_version` 判断解析器
- v1 只保证上述 MUST 字段；供应商可自定义字段，管理器会保留原始 JSON 以便追溯
- 如需扩展鉴权/签名：建议在 v2 引入 `signing` 字段与 mTLS 支持
