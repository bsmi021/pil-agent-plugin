import re
from pathlib import Path

root=Path(__file__).resolve().parents[2]
checked=0
for name in ('measurement-workflows.md','verification-0.9.0.md'):
    document=root/'docs'/name
    for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)',document.read_text(encoding='utf-8')):
        if '://' in target or target.startswith('#'):
            continue
        path=document.parent/target.split('#',1)[0]
        assert path.exists(),str(path)
        checked+=1
print(f'Validated {checked} local links in the two new guides.')
