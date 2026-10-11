from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
TARGET_PATH = ROOT / 'config' / 'target.json'
