# 供应商接入快速指南（DVP v1）

你只需要做一件事：**让版本管理器（管理端）能访问到你设备上的一个固定地址，并拿到一段 JSON**。

你不用理解协议细节。按本文把代码跑起来，然后只改“配置区”的几行即可。

## 你要交付的东西（最小）

- 固定地址：`http://<设备IP>:<端口>/.well-known/device-version`（管理端的版本管理器会访问它）
- 访问该地址时输出 JSON（参考下方示例程序）

## 示例程序（仓库内提供）

仓库已提供一套可直接运行的示例程序：

- 配置文件（你只改这个）：`docs/vendor_example/dvp_config.py`
- 实现文件（协议/逻辑，不用改）：`docs/vendor_example/dvp_server.py`

### `dvp_config.py`（完整内容，可直接复制）

```python
# 供应商侧配置文件：你只需要改这里

# 监听地址
HOST = "0.0.0.0"
PORT = 18080

# 设备身份与版本（必填）
# DEVICE_ID：设备唯一编号（推荐序列号/出厂编码；同一台设备长期不变）
DEVICE_ID = "PLC-07"
# SUPPLIER：供应商名称（公司/品牌名；建议固定写法）
SUPPLIER = "VendorY"
# DEVICE_TYPE：设备型号/机型（建议固定写法）
DEVICE_TYPE = "PLC-Pro"
# MAIN_VERSION：主软件版本号（例如 3.1.0；能稳定表达当前运行的软件版本）
MAIN_VERSION = "3.1.0"

# 受控文件（可选）：只填路径列表（路径必须在设备上存在且可读）
CONTROLLED_PATHS = [
    # r"C:\\app\\config\\config.yml",
    # "/etc/app/config.yml",
]

# 文件内容提供方式（可选）
INLINE_FILE_CONTENT = False  # 小文件直接内联到 files[] 里（Base64）
ENABLE_FILE_ENDPOINT = True  # 开启 /.well-known/device-version/file?path=...

# 鉴权（可选）：留空表示不鉴权
TOKEN = ""  # 例如 "change-me"
```

### 这些字段是什么意思（按上面注释填就行）

- `DEVICE_ID`：设备唯一编号（别重复、别变化）
- `SUPPLIER`：供应商名称（稳定写法即可）
- `DEVICE_TYPE`：设备型号/机型（稳定写法即可）
- `MAIN_VERSION`：主软件版本号（你们内部怎么标识版本就怎么填；常见写法 `3.1.0`）

使用方式：

1) 把 `docs/vendor_example/dvp_config.py` 和 `docs/vendor_example/dvp_server.py` 复制到设备（或设备旁路工控机）同一目录
2) 只修改 `dvp_config.py` 里的几项：`DEVICE_ID`、`SUPPLIER`、`DEVICE_TYPE`、`MAIN_VERSION`，以及可选的 `CONTROLLED_PATHS`
3) 运行：`python dvp_server.py`

`dvp_server.py` 的工作逻辑（概览）：
- 对外提供 `/.well-known/device-version`：返回设备身份与版本信息 JSON
- 若配置了 `CONTROLLED_PATHS`：运行时读取这些文件并自动生成指纹与可选内容，供管理器做“受控文件变更提示/差异分析”
- （可选）提供 `/.well-known/device-version/file?path=...`：当管理器需要内容时按需拉取

## 自测（看见 JSON 就算成功）

- PowerShell：`irm http://127.0.0.1:18080/.well-known/device-version`
- curl：`curl http://127.0.0.1:18080/.well-known/device-version`

## 需要更完整字段说明（可选）

- `docs/device_version_protocol_v1.md`
- `docs/device-version.schema.json`
