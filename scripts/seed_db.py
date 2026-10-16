"""Convenience wrapper; the CLI is also available as python -m retailpulse."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retailpulse.__main__ import main
sys.argv.insert(1, 'seed')
if __name__ == '__main__':
    raise SystemExit(main())
