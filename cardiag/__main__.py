"""Allow ``python -m cardiag`` as well as the installed ``cardiag`` script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
