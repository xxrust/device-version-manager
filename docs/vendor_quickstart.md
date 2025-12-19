# 供应商接入快速指南（DVP v1）

只要你的设备在局域网内提供一个 HTTP 端点并返回 JSON，版本管理器就能自动拉取版本并做一致性校验。

## 必须实现

- `GET /.well-known/device-version`
- 返回 JSON（字段要求见 `docs/device_version_protocol_v1.md`）

## 最小实现示例

返回（必须字段只有 `protocol`、`protocol_version`、`device`、`versions`）：

```json
{
  "protocol": "dvp",
  "protocol_version": 1,
  "device": { "id": "PLC-07", "vendor": "VendorY", "model": "PLC-Pro" },
  "versions": { "main": "3.1.0" }
}
```

## 推荐实现

- 增加 `components`：让管理器精确定位“是哪一块软件不一致”
- 增加 `build`：便于追溯具体构建/提交
- 在设备 UI 中展示同一份 `versions` 信息，避免人机不一致

## 鉴权建议（可选）

如果你担心局域网抓包/误调用：

- 支持 `Authorization: Bearer <token>`
- Token 可由设备端配置页面写入

版本管理器会在设备注册时保存 token，并在拉取时带上请求头。

