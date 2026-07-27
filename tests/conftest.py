"""让 session-to-eval skill 的 scripts 下的模块可被测试导入。"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "session-to-eval" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
