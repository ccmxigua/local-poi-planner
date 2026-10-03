#!/usr/bin/env python3
"""POI-only entrypoint, sharing the planner's parser, budgets, and output contract."""
import sys

from planner import main as planner_main


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    return planner_main(["--poi-only", "--mode", "search", *args])


if __name__ == "__main__":
    main()
