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
    # r"C:\app\config\config.yml",
    # "/etc/app/config.yml",
]

# 文件内容提供方式（可选）
INLINE_FILE_CONTENT = False  # 小文件直接内联到 files[] 里（Base64）
ENABLE_FILE_ENDPOINT = True  # 开启 /.well-known/device-version/file?path=...

# 鉴权（可选）：留空表示不鉴权
TOKEN = ""  # 例如 "change-me"
