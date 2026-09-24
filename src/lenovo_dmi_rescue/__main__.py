"""Allow ``python -m lenovo_dmi_rescue``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
