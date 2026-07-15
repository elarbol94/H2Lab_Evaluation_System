from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


def _split_forwarded_args(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    return shlex.split(text, posix=(os.name != "nt"))


def _build_command(script_path: Path, forwarded: str) -> list[str]:
    cmd = [sys.executable, str(script_path)]
    cmd.extend(_split_forwarded_args(forwarded))
    return cmd


def _run_step(name: str, cmd: list[str], dry_run: bool, continue_on_error: bool) -> bool:
    print(f"[PIPELINE] {name}: {' '.join(cmd)}")
    if dry_run:
        return True
    try:
        subprocess.run(cmd, check=True)
        print(f"[PIPELINE] Completed {name}")
        return True
    except subprocess.CalledProcessError as err:
        print(f"[PIPELINE] Failed {name} (exit={err.returncode})")
        if continue_on_error:
            return False
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run PUB_25_9 plot pipeline (Composition + TGA + Boudouard)."
    )
    parser.add_argument(
        "--skip-composition",
        action="store_true",
        help="Skip Composition plotting step.",
    )
    parser.add_argument(
        "--skip-tga",
        action="store_true",
        help="Skip TGA plotting step.",
    )
    parser.add_argument(
        "--composition-args",
        type=str,
        default="",
        help="Arguments forwarded to Composition/TGAplotting.py",
    )
    parser.add_argument(
        "--tga-args",
        type=str,
        default="",
        help="Arguments forwarded to TGA/TGAplotting.py",
    )
    parser.add_argument(
        "--skip-boudouard",
        action="store_true",
        help="Skip TGA/plottingBoudouard.py step.",
    )
    parser.add_argument(
        "--boudouard-args",
        type=str,
        default="",
        help="Arguments forwarded to TGA/plottingBoudouard.py",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands only without running them.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with next step if one plotting step fails.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    composition_script = root / "Composition" / "TGAplotting.py"
    tga_script = root / "TGA" / "TGAplotting.py"
    boudouard_script = root / "TGA" / "plottingBoudouard.py"

    if not composition_script.exists():
        raise FileNotFoundError(f"Missing script: {composition_script}")
    if not tga_script.exists():
        raise FileNotFoundError(f"Missing script: {tga_script}")
    if not boudouard_script.exists():
        raise FileNotFoundError(f"Missing script: {boudouard_script}")
    if args.skip_composition and args.skip_tga and args.skip_boudouard:
        print("[PIPELINE] Nothing to do (all steps skipped).")
        return

    if not args.skip_composition:
        cmd = _build_command(composition_script, args.composition_args)
        _run_step(
            name="Composition plotting",
            cmd=cmd,
            dry_run=args.dry_run,
            continue_on_error=args.continue_on_error,
        )

    if not args.skip_tga:
        cmd = _build_command(tga_script, args.tga_args)
        _run_step(
            name="TGA plotting",
            cmd=cmd,
            dry_run=args.dry_run,
            continue_on_error=args.continue_on_error,
        )

    if not args.skip_boudouard:
        cmd = _build_command(boudouard_script, args.boudouard_args)
        _run_step(
            name="Boudouard plotting",
            cmd=cmd,
            dry_run=args.dry_run,
            continue_on_error=args.continue_on_error,
        )


if __name__ == "__main__":
    main()
