"""Make the project root importable so ``import price_scraper`` works in tests
regardless of the working directory pytest is invoked from."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
