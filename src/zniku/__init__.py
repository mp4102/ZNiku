"""ZNIKU 的 Python 公共命名空间。

0.3.2 开发线继续复用 0.2.0 自由媒体 Graph、Project、Runtime、Project Service 与媒体节点，
由唯一正式 Studio 直接消费这些 authority；``zniku.presentation`` 保持 0.3.0 纯展示合同。
产品版本升级不改写已有 definition、wire 或工程格式；新的业务能力必须使用独立精确定义，
Presentation 不进入 Graph、Run、reuse 或 stale。版本号不代表新处理链已经实现或完成验收。
0.1.0 contracts、Compiler、Evidence、固定 pipeline 与真实媒体候选实现不再属于可导入命名空间。
"""

__version__ = "0.3.2"
