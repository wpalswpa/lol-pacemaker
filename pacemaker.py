"""PyInstaller entry point (keeps the `src` package importable inside the frozen app)."""
from src.main import main

if __name__ == "__main__":
    raise SystemExit(main())
