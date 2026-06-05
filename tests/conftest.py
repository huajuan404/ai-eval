"""让 case-gen/scripts 下的模块可被测试导入。"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "case-gen" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
