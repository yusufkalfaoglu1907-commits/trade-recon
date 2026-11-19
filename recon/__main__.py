"""Command line: python -m recon run --date 2026-11-20"""
import argparse

from recon.generate import generate
from recon.report import print_summary, reconcile_day, write_report


def main():
    parser = argparse.ArgumentParser(prog="python -m recon")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="generate the synthetic day, reconcile it and write the report")
    run.add_argument("--date", required=True, help="trading day, YYYY-MM-DD")
    run.add_argument("--data-dir", default="data")
    run.add_argument("--out-dir", default="output")
    args = parser.parse_args()

    generate(args.date, args.data_dir)   # deterministic, so regenerating gives the same files
    exceptions = reconcile_day(args.data_dir, args.date)
    print_summary(exceptions, write_report(exceptions, args.out_dir, args.date))


if __name__ == "__main__":
    main()
