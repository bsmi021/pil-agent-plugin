from pathlib import Path
p = Path('runs/2026-09-05-windows-setup/red.txt')
p.write_text('\n'.join(line.rstrip() for line in p.read_text(encoding='utf-8').splitlines()) + '\n', encoding='utf-8')
