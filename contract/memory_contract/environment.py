"""Read nonempty local settings. Explicit process variables take precedence."""
import os
from pathlib import Path

from dotenv import dotenv_values


def load_environment() -> None:
    for key, value in dotenv_values(Path(".env")).items():
        if value:
            os.environ.setdefault(key, value)
