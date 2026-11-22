import sys

from whydiff import __version__

MESSAGE = (
    f"whydiff {__version__}: the name is reserved and the tool is in early development.\n"
    "Follow progress at https://github.com/alekseidonskikh/whydiff"
)


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(__version__)
    else:
        print(MESSAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
