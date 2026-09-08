"""为 PyInstaller 提供绝对 package import 的无控制台双击入口。"""

from zniku.desktop.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
