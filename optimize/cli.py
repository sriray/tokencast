"""`tokencast-optimize` CLI. Sub-project 1 ships the `run` command: execute one task
under one config, measured accurately, with a pre-flight cost forecast + confirmation.
"""
import argparse
import os
import sys

import tokencast
from optimize.config import AgentConfig
from optimize.harness import run as run_task
from optimize.evalset import EvalSet
from optimize.evalrun import run_evalset
from optimize.generate import gather_context, generate_evalset
from optimize.candidates import from_dirs, model_sweep
from optimize.loop import run_optimize
from optimize.decompose import run_decompose
from optimize.auto import run_auto
from optimize import catalog


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


def cmd_eval_run(args):
    if not os.path.isfile(args.evalset):
        raise SystemExit(f"tokencast-optimize: eval set not found: {args.evalset}")
    if not os.path.isdir(args.config):
        raise SystemExit(f"tokencast-optimize: config dir not found: {args.config}")

    try:
        evalset = EvalSet.load(args.evalset)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"tokencast-optimize: {e}")
    config = AgentConfig.load(args.config)
    n_tasks = len(evalset.tasks)

    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        print(f"Pre-flight: {n_tasks} task(s); est. total ~ {_fmt_cost(p90 * n_tasks)} "
              f"(per-task p90 x {n_tasks}, modeled at list prices)", file=sys.stderr)
    else:
        print(f"Pre-flight: only {hist_n} past sessions (<5); skipping forecast.",
              file=sys.stderr)

    if not args.yes:
        resp = input(
            "Proceed (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    report = run_evalset(evalset, config, out_dir=args.out)

    print("=" * 60)
    print(f"Eval: {n_tasks} task(s) / config {report.config_id}")
    print(f"  Composite  {report.composite * 100:.0f}%    "
          f"Pass rate  {report.pass_rate * 100:.0f}%")
    print(f"  Cost       {_fmt_cost(report.total_cost_usd)}    "
          f"Duration  {report.total_duration_ms / 1000:.1f}s")
    print(f"  Report     {os.path.join(args.out, 'report.json')}")


def cmd_eval_init(args):
    context = gather_context(root=args.root)
    evalset = generate_evalset(context)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "evalset.yaml")
    evalset.save(path)
    print(f"Draft eval set written to {path}", file=sys.stderr)
    print("REVIEW BEFORE RUNNING: generated rule checks are LLM-authored shell commands.",
          file=sys.stderr)
    print(path)


def _print_optimize_result(result, out_dir):
    by_id = {c.config_id: c for c in result.candidates}
    has_budget = result.budget_remaining is not None
    print("=" * 72)
    print(f"TokenCast - optimize (baseline {result.baseline_id}, "
          f"quality floor {result.floor:.2f})")
    print("=" * 72)
    head = f"   {'config':24}{'quality':>8}{'cost':>11}{'time':>8}{'pass':>6}"
    if has_budget:
        head += f"{'runway':>9}"
    print(head)
    for c in sorted(result.candidates, key=lambda x: x.cost_usd):
        mark = "*" if c.config_id == result.winner_id else (
            "+" if c.config_id in result.pareto else " ")
        line = (f" {mark} {c.config_id[:24]:24}{c.quality:8.2f}{_fmt_cost(c.cost_usd):>11}"
                f"{c.duration_ms / 1000:7.1f}s{c.pass_rate * 100:5.0f}%")
        if has_budget:
            tag = ""
            if result.need_tasks is not None and c.fits is not None:
                tag = " fit" if c.fits else " !fit"
            line += f"{c.runway:>7}t{tag}"
        print(line)
    print()
    note = "" if result.improved else "  [no improvement over baseline]"
    print(f"Winner: {result.winner_id}  ({result.cost_delta_pct:+.0f}% cost, "
          f"{result.quality_delta:+.2f} quality vs baseline){note}")
    if has_budget:
        w = by_id[result.winner_id]
        b = by_id[result.baseline_id]
        print(f"Runway: winner ~{w.runway} tasks vs baseline ~{b.runway}  "
              f"(+{result.runway_gain} tasks, same {_fmt_cost(result.budget_remaining)} budget)")
        if result.need_tasks is not None:
            verdict = "YES" if result.winner_fits else "NO"
            print(f"Need {result.need_tasks} tasks in budget? winner fits: {verdict}")
    print(f"Report:   {os.path.join(out_dir, 'optimize.json')}")
    print(f"Promoted: {os.path.join(out_dir, 'promoted')}")


def cmd_optimize(args):
    if not os.path.isfile(args.evalset):
        raise SystemExit(f"tokencast-optimize: eval set not found: {args.evalset}")
    if not os.path.isdir(args.config):
        raise SystemExit(f"tokencast-optimize: config dir not found: {args.config}")
    for d in (args.candidate or []):
        if not os.path.isdir(d):
            raise SystemExit(f"tokencast-optimize: candidate dir not found: {d}")
    if args.need_tasks is not None and args.budget_remaining is None and not args.budget_config:
        raise SystemExit("tokencast-optimize: --need-tasks requires a budget "
                         "(--budget-remaining or --budget-config/--budget-scope)")

    try:
        evalset = EvalSet.load(args.evalset)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"tokencast-optimize: {e}")
    baseline = AgentConfig.load(args.config)
    candidates = list(from_dirs(args.candidate or []))
    if args.model_sweep:
        candidates += model_sweep(baseline)

    budget_remaining = args.budget_remaining
    if budget_remaining is None and args.budget_config:
        import budget as budget_mod
        import datetime
        cfg = budget_mod.BudgetConfig.load(args.budget_config)
        if cfg is None:
            raise SystemExit(f"tokencast-optimize: budget config not found: {args.budget_config}")
        try:
            records = budget_mod.collect_spend(
                os.path.expanduser("~/.claude/projects"), "./runs")
            budget_remaining = budget_mod.status(
                cfg, args.budget_scope, records, datetime.date.today()).remaining
        except ValueError as e:
            raise SystemExit(f"tokencast-optimize: {e}")

    n_generated = max(0, args.generate)
    n_cfgs = 1 + len(candidates) + n_generated
    n_tasks = len(evalset.tasks)
    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        total = p90 * n_tasks * n_cfgs * args.repeats
        print(f"Pre-flight: {n_cfgs} configs x {n_tasks} tasks x {args.repeats} repeats "
              f"(up to {n_generated} generated); est. total ~ {_fmt_cost(total)} "
              f"(per-task p90, modeled at list prices)", file=sys.stderr)
    else:
        print(f"Pre-flight: only {hist_n} past sessions (<5); skipping forecast.",
              file=sys.stderr)
    if not args.yes:
        resp = input(
            "Proceed (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    skills_catalog = mcp_catalog = None
    if n_generated > 0:
        skills_catalog = catalog.available_skills(args.skills_dir)
        mcp_catalog = catalog.available_mcp(args.mcp_catalog)

    result = run_optimize(baseline, candidates, evalset, repeats=args.repeats,
                          min_quality=args.min_quality, by=args.by, out_dir=args.out,
                          promote_to=args.promote, budget_remaining=budget_remaining,
                          need_tasks=args.need_tasks, n_generated=n_generated,
                          skills_catalog=skills_catalog, mcp_catalog=mcp_catalog)
    _print_optimize_result(result, args.out)


def _print_decompose_results(results, out_dir):
    for r in results:
        mono = r.get("monolithic")
        if mono is None:
            print(f"{r['task_id']}: (failed)")
            continue
        win = r["winner_label"]
        win_cost = next((s["cost_usd"] for s in r["strategies"] if s["label"] == win),
                        mono["cost_usd"])
        verb = f"decomposition '{win}' wins" if win != "monolithic" else "monolithic wins"
        print(f"{r['task_id']}: {verb} "
              f"(cost {_fmt_cost(mono['cost_usd'])} -> {_fmt_cost(win_cost)}, "
              f"{r['cost_delta_pct']:+.0f}% cost / {r['time_delta_pct']:+.0f}% time)")
    print(f"\nFull results: {os.path.join(out_dir, 'decompose.json')}")


def cmd_decompose(args):
    if not os.path.isfile(args.evalset):
        raise SystemExit(f"tokencast-optimize: eval set not found: {args.evalset}")
    if not os.path.isdir(args.config):
        raise SystemExit(f"tokencast-optimize: config dir not found: {args.config}")
    try:
        evalset = EvalSet.load(args.evalset)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"tokencast-optimize: {e}")
    baseline = AgentConfig.load(args.config)

    n_gen = max(0, args.generate)
    n_strategies = 1 + n_gen
    n_tasks = len(evalset.tasks)
    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        total = p90 * n_tasks * n_strategies
        print(f"Pre-flight: {n_tasks} tasks x {n_strategies} strategies "
              f"(monolithic + up to {n_gen} decompositions); est. total ~ {_fmt_cost(total)} "
              f"(per-task p90, modeled at list prices)", file=sys.stderr)
    else:
        print(f"Pre-flight: only {hist_n} past sessions (<5); skipping forecast.",
              file=sys.stderr)
    if not args.yes:
        resp = input(
            "Proceed (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    results = run_decompose(evalset, baseline, n=n_gen, min_quality=args.min_quality,
                            by=args.by, out_dir=args.out)
    _print_decompose_results(results, args.out)


def cmd_auto(args):
    if not os.path.isfile(args.evalset):
        raise SystemExit(f"tokencast-optimize: eval set not found: {args.evalset}")
    if not os.path.isdir(args.config):
        raise SystemExit(f"tokencast-optimize: config dir not found: {args.config}")
    for d in (args.candidate or []):
        if not os.path.isdir(d):
            raise SystemExit(f"tokencast-optimize: candidate dir not found: {d}")
    if args.need_tasks is not None and args.budget_remaining is None and not args.budget_config:
        raise SystemExit("tokencast-optimize: --need-tasks requires a budget "
                         "(--budget-remaining or --budget-config/--budget-scope)")

    try:
        evalset = EvalSet.load(args.evalset)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"tokencast-optimize: {e}")
    baseline = AgentConfig.load(args.config)
    candidates = list(from_dirs(args.candidate or []))
    if args.model_sweep:
        candidates += model_sweep(baseline)

    budget_remaining = args.budget_remaining
    if budget_remaining is None and args.budget_config:
        import budget as budget_mod
        import datetime
        cfg = budget_mod.BudgetConfig.load(args.budget_config)
        if cfg is None:
            raise SystemExit(f"tokencast-optimize: budget config not found: {args.budget_config}")
        try:
            records = budget_mod.collect_spend(
                os.path.expanduser("~/.claude/projects"), "./runs")
            budget_remaining = budget_mod.status(
                cfg, args.budget_scope, records, datetime.date.today()).remaining
        except ValueError as e:
            raise SystemExit(f"tokencast-optimize: {e}")

    n_generated = max(0, args.generate)
    n_decompose = max(0, args.decompose_generate)
    n_cfgs = 1 + len(candidates) + n_generated
    n_tasks = len(evalset.tasks)
    n_decomp = (1 + n_decompose) if args.decompose else 0
    hist_n, p90 = estimate_cost(args.history)
    if p90 is not None:
        total = p90 * n_tasks * (n_cfgs * args.repeats + n_decomp)
        extra = f" + {n_decomp} decomposition strategies/task" if args.decompose else ""
        print(f"Pre-flight: {n_cfgs} configs x {n_tasks} tasks x {args.repeats} repeats "
              f"(up to {n_generated} generated){extra}; est. total ~ {_fmt_cost(total)} "
              f"(per-task p90, modeled at list prices)", file=sys.stderr)
    else:
        print(f"Pre-flight: only {hist_n} past sessions (<5); skipping forecast.",
              file=sys.stderr)
    if not args.yes:
        resp = input(
            "Proceed (real spend: dollars or plan credits)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return

    skills_catalog = mcp_catalog = None
    if n_generated > 0:
        skills_catalog = catalog.available_skills(args.skills_dir)
        mcp_catalog = catalog.available_mcp(args.mcp_catalog)

    summary = run_auto(baseline, evalset, candidates=candidates, repeats=args.repeats,
                       n_generated=n_generated, with_decompose=args.decompose,
                       n_decompose=n_decompose,
                       min_quality=args.min_quality, by=args.by, out_dir=args.out,
                       promote_to=args.promote, budget_remaining=budget_remaining,
                       need_tasks=args.need_tasks, skills_catalog=skills_catalog,
                       mcp_catalog=mcp_catalog)
    _print_optimize_result(summary["optimize"], args.out)
    if summary["decompose"] is not None:
        _print_decompose_results(summary["decompose"], args.out)
    print(f"Summary:  {os.path.join(args.out, 'auto.json')}")


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

    e = sub.add_parser("eval", help="evaluate a config against an eval set")
    esub = e.add_subparsers(dest="eval_cmd", required=True)

    er = esub.add_parser("run", help="run an eval set under one config, scored")
    er.add_argument("evalset", help="path to an evalset.yaml")
    er.add_argument("--config", required=True, help="path to a config dir")
    er.add_argument("--out", default="./runs", help="directory for run logs + report.json")
    er.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                    help="historical logs for the pre-flight estimate")
    er.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    er.set_defaults(func=cmd_eval_run)

    ei = esub.add_parser("init", help="draft an eval set from the repo (LLM, review before use)")
    ei.add_argument("--root", default=".", help="project root to read context from")
    ei.add_argument("--out", default="evals/generated", help="dir to write the draft evalset.yaml")
    ei.set_defaults(func=cmd_eval_init)

    o = sub.add_parser("optimize", help="rank candidate configs cost-first; promote a winner")
    o.add_argument("evalset", help="path to an evalset.yaml")
    o.add_argument("--config", required=True, help="baseline config dir")
    o.add_argument("--candidate", action="append", help="extra candidate config dir (repeatable)")
    o.add_argument("--model-sweep", action="store_true", help="add opus/sonnet/haiku variants")
    o.add_argument("--repeats", type=int, default=1, help="eval each config N times (median/p90)")
    o.add_argument("--min-quality", type=float, default=None, help="quality floor (default baseline)")
    o.add_argument("--by", choices=("cost", "time"), default="cost", help="optimize cost or time")
    o.add_argument("--out", default="./runs", help="output dir (logs + optimize.json + promoted/)")
    o.add_argument("--promote", default=None, help="also save the winner config to this dir")
    o.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                   help="historical logs for the pre-flight estimate")
    o.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    o.add_argument("--budget-remaining", type=float, default=None,
                   help="remaining budget USD -> show runway gained")
    o.add_argument("--budget-config", default=None,
                   help="resolve remaining from a budget JSON instead of --budget-remaining")
    o.add_argument("--budget-scope", default="global", help="budget scope (global | project:NAME)")
    o.add_argument("--need-tasks", type=int, default=None,
                   help="report whether the winner makes N tasks fit the budget")
    o.add_argument("--generate", type=int, default=0,
                   help="generate N failure-driven candidates (instructions/tools) via an LLM")
    o.add_argument("--skills-dir", default=None,
                   help="skills catalog dir for --generate candidates (default ~/.claude/skills)")
    o.add_argument("--mcp-catalog", default=None,
                   help="MCP catalog JSON for --generate candidates (default ~/.claude.json)")
    o.set_defaults(func=cmd_optimize)

    d = sub.add_parser("decompose",
                       help="compare a monolithic task run vs LLM-proposed decompositions")
    d.add_argument("evalset", help="path to an evalset.yaml")
    d.add_argument("--config", required=True, help="baseline config dir")
    d.add_argument("--generate", type=int, default=2,
                   help="propose N decompositions per task (default 2)")
    d.add_argument("--min-quality", type=float, default=None,
                   help="quality floor (default = the monolithic run's quality)")
    d.add_argument("--by", choices=("cost", "time"), default="cost",
                   help="optimize cost or time")
    d.add_argument("--out", default="./runs", help="output dir (logs + decompose.json)")
    d.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                   help="historical logs for the pre-flight estimate")
    d.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    d.set_defaults(func=cmd_decompose)

    a = sub.add_parser("auto",
                       help="forecast -> optimize -> promote in one shot (+ optional decompose)")
    a.add_argument("evalset", help="path to an evalset.yaml")
    a.add_argument("--config", required=True, help="baseline config dir")
    a.add_argument("--candidate", action="append", help="extra candidate config dir (repeatable)")
    a.add_argument("--model-sweep", action="store_true", help="add opus/sonnet/haiku variants")
    a.add_argument("--repeats", type=int, default=1, help="eval each config N times (median/p90)")
    a.add_argument("--min-quality", type=float, default=None, help="quality floor (default baseline)")
    a.add_argument("--by", choices=("cost", "time"), default="cost", help="optimize cost or time")
    a.add_argument("--out", default="./runs", help="output dir (logs + optimize/auto json + promoted/)")
    a.add_argument("--promote", default=None, help="also save the winner config to this dir")
    a.add_argument("--history", default=os.path.expanduser("~/.claude/projects"),
                   help="historical logs for the pre-flight estimate")
    a.add_argument("--yes", action="store_true", help="skip the proceed confirmation")
    a.add_argument("--budget-remaining", type=float, default=None,
                   help="remaining budget USD -> show runway gained")
    a.add_argument("--budget-config", default=None,
                   help="resolve remaining from a budget JSON instead of --budget-remaining")
    a.add_argument("--budget-scope", default="global", help="budget scope (global | project:NAME)")
    a.add_argument("--need-tasks", type=int, default=None,
                   help="report whether the winner makes N tasks fit the budget")
    a.add_argument("--generate", type=int, default=0,
                   help="generate N failure-driven candidates (instructions/tools/skills/mcp)")
    a.add_argument("--skills-dir", default=None,
                   help="skills catalog dir for --generate candidates (default ~/.claude/skills)")
    a.add_argument("--mcp-catalog", default=None,
                   help="MCP catalog JSON for --generate candidates (default ~/.claude.json)")
    a.add_argument("--decompose", action="store_true",
                   help="also compare task decompositions on the promoted winner")
    a.add_argument("--decompose-generate", type=int, default=2,
                   help="propose N decompositions per task when --decompose (default 2)")
    a.set_defaults(func=cmd_auto)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
