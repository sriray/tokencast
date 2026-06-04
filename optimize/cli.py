"""`tokencast-optimize` CLI. Sub-project 1 ships the `run` command: execute one task
under one config, measured accurately, with a pre-flight cost forecast + confirmation.
"""
import argparse
import os
import sys

import tokencast
from optimize.config import AgentConfig
from optimize.harness import run as run_task


def _fmt_cost(x):
    """Cost formatter that does not collapse sub-cent amounts to $0.00 — this tier's
    whole value is precision. Shows 4 decimals under $1, else standard 2-decimal money()."""
    return f"${x:,.4f}" if abs(x) < 1 else tokencast.money(x)


def estimate_cost(history_path):
    """Rough pre-flight: p90 of historical session costs. Returns (n_sessions, p90 | None).
    Richer kNN-matched forecasting arrives with the optimize loop sub-project."""
    sessions = tokencast.load(history_path)
    if len(sessions) < 5:
        return len(sessions), None
    costs = [s["cost"] for s in sessions]
    return len(sessions), tokencast.pct(costs, 0.9)


def cmd_run(args):
    if not os.path.isfile(args.taskfile):
        raise SystemExit(f"tokencast-optimize: taskfile not found: {args.taskfile}")
    if not os.path.isdir(args.config):
        raise SystemExit(f"tokencast-optimize: config dir not found: {args.config}")
    with open(args.taskfile) as fh:
        prompt = fh.read()
    task_id = os.path.splitext(os.path.basename(args.taskfile))[0]
    task = {"id": task_id, "prompt": prompt, "cwd": args.cwd}

    config = AgentConfig.load(args.config)
    if args.budget is not None:
        config.budget_usd = args.budget

    n, p90 = estimate_cost(args.history)
    if p90 is not None:
        print(f"Pre-flight: {n} past sessions; p90 cost ~ {_fmt_cost(p90)} "
              f"(modeled at list prices)", file=sys.stderr)
        if config.budget_usd is not None and p90 > config.budget_usd:
            print(f"  ! p90 estimate exceeds budget {tokencast.money(config.budget_usd)}",
                  file=sys.stderr)
    else:
        print(f"Pre-flight: only {n} past sessions (<5); skipping forecast.", file=sys.stderr)

    if not args.yes:
        resp = input("Proceed with run (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    result = run_task(task, config)

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"{result.task_id}-{result.config_id}.jsonl")
    result.to_jsonl(out_path)

    print("=" * 60)
    print(f"Task {result.task_id} / config {result.config_id}")
    print(f"  Cost      {_fmt_cost(result.cost_usd)}  (accurate token counts)")
    print(f"  Duration  {result.duration_ms / 1000:.1f}s   "
          f"Turns {result.num_turns}   Files {len(result.files_changed)}")
    print(f"  Log       {out_path}")
    print(f"  Inspect   python tokencast.py report {args.out}")


def main():
    ap = argparse.ArgumentParser(prog="tokencast-optimize",
                                 description="TokenCast optimizer (Agent SDK tier)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one task under one config, measured accurately")
    r.add_argument("taskfile", help="text/markdown file whose contents are the task prompt")
    r.add_argument("--config", required=True, help="path to a config dir")
    r.add_argument("--cwd", default=None, help="working directory for the agent")
    r.add_argument("--budget", type=float, default=None,
                   help="hard USD ceiling (maps to SDK max_budget_usd)")
    r.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                   help="historical logs for the pre-flight estimate")
    r.add_argument("--out", default="./runs", help="directory to write the result JSONL")
    r.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    r.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
