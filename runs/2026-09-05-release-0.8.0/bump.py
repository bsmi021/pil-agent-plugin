from pathlib import Path
root = Path.cwd()
paths = [root / p for p in ['plugin.json', '.claude-plugin/plugin.json', '.claude-plugin/marketplace.json', '.codex-plugin/plugin.json', 'pyproject.toml']]
for path in paths:
    text = path.read_text(encoding='utf-8')
    path.write_text(text.replace('"0.7.0"', '"0.8.0"'), encoding='utf-8')
for path in (root / 'scripts').glob('pil_*.py'):
    text = path.read_text(encoding='utf-8')
    if 'TOOL_VERSION = "0.7.0"' in text:
        path.write_text(text.replace('TOOL_VERSION = "0.7.0"', 'TOOL_VERSION = "0.8.0"'), encoding='utf-8')
