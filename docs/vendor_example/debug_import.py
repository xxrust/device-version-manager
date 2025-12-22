from __future__ import annotations

import os
import sys


def main() -> None:
    print("== vendor_example debug_import ==")
    print("cwd:", os.getcwd())
    print("script:", os.path.abspath(__file__))
    print("sys.executable:", sys.executable)
    print("sys.path[0]:", sys.path[0])
    print("sys.path (head):", sys.path[:5])
    print()

    import dvp_config as cfg  # noqa: WPS433

    cfg_file = getattr(cfg, "__file__", None)
    print("dvp_config.__file__:", cfg_file)
    print("HOST/PORT:", getattr(cfg, "HOST", None), getattr(cfg, "PORT", None))
    print("DEVICE_ID:", getattr(cfg, "DEVICE_ID", None))
    print("SUPPLIER:", getattr(cfg, "SUPPLIER", None))
    print("DEVICE_TYPE:", getattr(cfg, "DEVICE_TYPE", None))
    print("MAIN_VERSION:", getattr(cfg, "MAIN_VERSION", None))
    print("INLINE_FILE_CONTENT:", getattr(cfg, "INLINE_FILE_CONTENT", None))
    print("ENABLE_FILE_ENDPOINT:", getattr(cfg, "ENABLE_FILE_ENDPOINT", None))
    print()

    paths = getattr(cfg, "CONTROLLED_PATHS", None)
    print("CONTROLLED_PATHS type:", type(paths).__name__)
    print("CONTROLLED_PATHS:", paths)
    if isinstance(paths, (list, tuple)):
        for i, p in enumerate(paths):
            ps = str(p)
            print(f"[{i}] repr:", repr(ps))
            print(f"    exists:", os.path.exists(ps))
            print(f"    isfile:", os.path.isfile(ps))
            try:
                print(f"    readable:", os.access(ps, os.R_OK))
            except Exception as e:  # noqa: BLE001
                print(f"    readable: error:{type(e).__name__}:{e}")
            if os.path.exists(ps):
                try:
                    print(f"    size:", os.path.getsize(ps))
                except Exception as e:  # noqa: BLE001
                    print(f"    size: error:{type(e).__name__}:{e}")
                try:
                    print(f"    mtime:", int(os.path.getmtime(ps)))
                except Exception as e:  # noqa: BLE001
                    print(f"    mtime: error:{type(e).__name__}:{e}")
    print("\n== done ==")


if __name__ == "__main__":
    main()

