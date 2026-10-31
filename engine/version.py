"""引擎版本号与契约版本——**唯一事实来源**。

为什么单独成文件（2026-10-31）：
版本号原先写在 `build_bundle.py` 的 RUNNER 模板字符串里，于是只有**单文件版**知道自己是
哪个版本，包内模式（`python engine/run_demo.py`）根本读不到 `ENGINE_VERSION`。
后果是同一个功能在两条运行路径上版本不一致、也没法互相校验。

现在它是 SECTIONS 的第一节，单文件版与包内模式共用同一个常量；bundle 里不再重复写死。
改版本号只改这里。
"""

ENGINE_VERSION = "chemcritic-engine/1.7.3"
RULES_VERSION = "gates v0.5 · tier-0.1"
CONTRACT_VERSION = "sciencepal-verdict/2"
