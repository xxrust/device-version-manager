# 供应商侧配置文件：你只需要改这里

# 监听地址
HOST = "0.0.0.0"
PORT = 18080

# 设备身份与版本（必填）
# DEVICE_ID：设备唯一编号（推荐序列号/出厂编码；同一台设备长期不变）
DEVICE_ID = "CN11"
# SUPPLIER：供应商名称（公司/品牌名；建议固定写法）
SUPPLIER = "台州博信电子"
# DEVICE_TYPE：设备型号/机型（建议固定写法）
DEVICE_TYPE = "点胶检测模组"
# MAIN_VERSION：主软件版本号（例如 3.1.0；能稳定表达当前运行的软件版本）
MAIN_VERSION = "3.1.0"

# 主版本更新信息（可选）：由设备端与版本号一起上报（DVP v1 扩展）
MAIN_CHANGELOG_MD = """## 3.1.0

- 新增：xxx
- 修复：yyy
"""
MAIN_RELEASED_AT = "2025-12-22T00:00:00Z"  # ISO-8601 或可解析字符串
MAIN_CHECKSUM = ""  # 推荐 sha256:<hex>

# 设备文档（可选）：用于未来 AI 分析（DVP v1 扩展，内联传输 Base64）
# - 支持 list[str]：每个元素为本地文件路径（name 将使用 basename）
# - 支持 list[dict]：{name, path} 或 {name, text}，text 支持 "@<file>" 读取文件内容
DOCS = [
    # r"E:\\path\\to\\files_and_params.md",
    # {"name": "files_and_params.md", "path": r"E:\\path\\to\\files_and_params.md"},
    # {"name": "device_features.md", "text": "# Device Features\\n\\n- ...\\n"},
]

# 受控文件（可选）：只填路径列表（路径必须在设备上存在且可读）
# Windows 路径请用原始字符串 r"..."（避免 \t \v \xNN 等转义），或改用正斜杠 E:/path/to/file。
CONTROLLED_PATHS = [
    r"E:\posen_project\点胶检测\上位机程序\WpfApp2\bin\x64\Release\Templates\1612.json",

]

# 文件内容提供方式（可选）
INLINE_FILE_CONTENT = True  # 小文件直接内联到 files[] 里（Base64）
ENABLE_FILE_ENDPOINT = True  # 开启 /.well-known/device-version/file?path=...

# 鉴权（可选）：留空表示不鉴权
TOKEN = ""  # 例如 "change-me"
