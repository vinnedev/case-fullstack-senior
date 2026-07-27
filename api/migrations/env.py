# Nome exigido pelo Alembic (não configurável); a lógica real vive em alembic_runner.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alembic_runner import run

run()
