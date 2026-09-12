import json
import re
from pathlib import Path

root=Path(__file__).resolve().parents[2]
for path in [root/'plugin.json',root/'.claude-plugin/plugin.json',root/'.codex-plugin/plugin.json',root/'.claude-plugin/marketplace.json']:
    value=json.loads(path.read_text(encoding='utf-8'))
    def update(item):
        if isinstance(item,dict):
            for key,value in item.items():
                if key=='version' and value=='0.8.0':
                    item[key]='0.9.0'
                else:
                    update(value)
        elif isinstance(item,list):
            for child in item: update(child)
    update(value)
    path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
path=root/'pyproject.toml'
path.write_text(path.read_text().replace('version = "0.8.0"','version = "0.9.0"'),encoding='utf-8')
for path in (root/'scripts').glob('pil_*.py'):
    text=path.read_text(encoding='utf-8')
    updated=re.sub(r'''^TOOL_VERSION\s*=\s*["']0\.[89]\.0["']''','TOOL_VERSION = "0.9.0"',text,flags=re.M)
    if text!=updated:path.write_text(updated,encoding='utf-8')
# These tests explicitly assert the public release version; preserve their behavioral assertions.
for name in ['test_character_sheet_review.py','test_components.py','test_silhouette.py']:
    path=root/'tests'/name
    path.write_text(path.read_text(encoding='utf-8').replace('"0.8.0"','"0.9.0"'),encoding='utf-8')
