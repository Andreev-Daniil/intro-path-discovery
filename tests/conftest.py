import os
import sys
from pathlib import Path

# Тесты не читают настоящий .env (ключи, адрес API) и потому не ходят во внешние сервисы.
os.environ["INTRO_PATHS_NO_DOTENV"] = "1"
for _name in ("API_KEY", "API_LINK", "ANTHROPIC_API_KEY", "SEC_USER_AGENT", "OPENALEX_API_KEY"):
    os.environ.pop(_name, None)

sys.path.insert(0, str(Path(__file__).parent))  # чтобы тесты видели tests/fakes.py
