"""Fixture: no verdict, exit 0 (must trigger VerdictNotFound)."""


def main() -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
