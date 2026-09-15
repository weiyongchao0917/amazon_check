"""PyInstaller GUI entrypoint. Do not put CLI behavior or secrets here."""
from sku_checker.gui_launcher import main


if __name__ == "__main__":
    raise SystemExit(main())
