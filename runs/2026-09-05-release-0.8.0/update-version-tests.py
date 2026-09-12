from pathlib import Path
for name in ['test_character_sheet_review.py', 'test_components.py', 'test_silhouette.py']:
    p = Path('tests') / name
    p.write_text(p.read_text(encoding='utf-8').replace('"0.7.0"', '"0.8.0"'), encoding='utf-8')
