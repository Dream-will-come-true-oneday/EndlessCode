"""在后台删除过期的新格式会话目录。"""

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from endless_code.compact import parse_session_time


def clean_expired(sessions_dir: str, max_age: timedelta = timedelta(days=30)) -> None:
    root = Path(sessions_dir)
    if not root.is_dir():
        return
    now = datetime.now().astimezone()
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        created_at = parse_session_time(directory.name)
        if created_at is None or now - created_at <= max_age:
            continue
        try:
            shutil.rmtree(directory)
        except OSError:
            continue
