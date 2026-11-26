"""Resolve dependencies and preferences relative to the executable in a portable build."""

from pathlib import Path
import sys


def application_directory():
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def portable_data_directory():
    app = application_directory()
    return app / "Data" if (app / "PORTABLE").is_file() else None
