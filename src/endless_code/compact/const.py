"""上下文管理的计数型与协议型常量。

压缩量纲类阈值（单条结果上限、聚合上限、摘要预留、安全余量、近期保留量、恢复附件
上限）随配置窗口等比缩放，定义见 :mod:`endless_code.compact.budget`。
"""

RECENT_KEEP_MESSAGES = 5
RECOVERY_FILE_LIMIT = 5
MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES = 3
PTL_RETRY_LIMIT = 3
PTL_DROP_PERCENTAGE = 0.2
ESTIMATE_CHARS_PER_TOKEN = 3.5
PREVIEW_HEAD_BYTES = 2_048
PREVIEW_HEAD_LINES = 20
