"""Copy GUI/deploy/offline.html into GUI/deploy/worker.js. Run after editing the offline page:

    python3 GUI/deploy/bake.py
"""

import re
from pathlib import Path

here = Path(__file__).parent
page = (here / "offline.html").read_text()
escaped = page.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
worker = (here / "worker.js").read_text()
worker = re.sub(r"const OFFLINE_HTML = `.*?`;\n", lambda _: f"const OFFLINE_HTML = `{escaped}`;\n", worker, count=1, flags=re.S)
(here / "worker.js").write_text(worker)
print("worker.js now carries the current offline page.")
