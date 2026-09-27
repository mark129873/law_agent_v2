"""agent 核心子包：工具、权限、hooks、主循环。

参考实现：scripts_mini_harness/mini_harness.py（九机制分区）。
本包把它的核心循环移植为"事件流"形态：run_turn 是一个异步生成器，
边执行边吐 SSE 事件，供 BE-5 的 HTTP SSE 端直接消费。
"""
