"""Run the simulation scenarios and print results.

    python -m simulator.run_demo            # all scenarios, summary
    python -m simulator.run_demo 1 16 -v    # chosen scenarios with full event log
"""

from __future__ import annotations

import argparse
import sys

from .scenarios import SCENARIOS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numbers", nargs="*", type=int, help="scenario numbers (default: all)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print the event log")
    args = parser.parse_args(argv)
    chosen = [s for i, s in enumerate(SCENARIOS, 1) if not args.numbers or i in args.numbers]
    failed = 0
    for scenario in chosen:
        result = scenario()
        mark = "PASS" if result.passed else "FAIL"
        failed += not result.passed
        print(f"[{mark}] {result.number:2d}. {result.name}")
        print(f"        expected safe state: {result.expected}")
        for description, ok in result.checks:
            if args.verbose or not ok:
                print(f"        {'ok ' if ok else 'BAD'} {description}")
        if args.verbose:
            print("        --- event log ---")
            for line in result.log:
                print(f"        {line}")
            print()
    print(f"\n{len(chosen) - failed}/{len(chosen)} scenarios passed (simulation only; "
          "this does not validate real hardware)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
