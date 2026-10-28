"""Command line: python -m rockform.holdgen "hard crimp on a 30 degree overhang" --out holds"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from ..geometry import validate_solid
from .shapes import HOLD_TYPES
from .generate import HoldRequest, generate, parse_description


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m rockform.holdgen", description=__doc__)
    parser.add_argument("description", nargs="?", help="e.g. 'easy jug', 'difficulty 7 crimp', 'hard sloper on 30 degree overhang'")
    parser.add_argument("--type", choices=HOLD_TYPES)
    parser.add_argument("--difficulty", type=float, help="absolute 0-10 holding-area score")
    parser.add_argument("--level", type=float, help="0-1 within the type's achievable range")
    parser.add_argument("--wall-angle", type=float, help="degrees past vertical; positive = overhang")
    parser.add_argument("--seed", default=None)
    parser.add_argument("--count", type=int, default=1, help="generate this many consecutive seeds")
    parser.add_argument("--out", type=Path, default=Path("generated-holds"))
    parser.add_argument("--no-validate", action="store_true", help="skip the (slower) self-intersection check")
    args = parser.parse_args(argv)
    if not args.description and not args.type:
        parser.error("give a description or --type")
    base = parse_description(args.description) if args.description else HoldRequest(args.type)
    for name in ("type", "difficulty", "level", "wall_angle", "seed"):
        value = getattr(args, name)
        if value is not None:
            setattr(base, "hold_type" if name == "type" else name, value)
    if args.difficulty is not None:
        base.level = None
    elif args.level is not None:
        base.difficulty = None
    args.out.mkdir(parents=True, exist_ok=True)
    start = base.seed
    for i in range(args.count):
        seed = int(start) + i if str(start).lstrip("-").isdigit() else f"{start}-{i}" if i else start
        request = HoldRequest(base.hold_type, base.difficulty, base.level, seed, base.wall_angle, base.cone)
        hold = generate(request)
        report = hold.report()
        if not args.no_validate:
            validate_solid(hold.mesh)
            report["solid_validated"] = True
        stem = f"hold_{request.hold_type}_d{hold.difficulty:.1f}_{seed}"
        hold.mesh.export(args.out / f"{stem}.stl")
        (args.out / f"{stem}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"{stem}.stl  difficulty {hold.difficulty:.2f} (target {hold.target:.2f}), "
              f"range {hold.difficulty_range[0]:.1f}-{hold.difficulty_range[1]:.1f} across pull angles, "
              f"holding area {hold.holding_area_mm2:.0f} mm^2, size {' x '.join(f'{v:.0f}' for v in hold.mesh.extents)} mm")
        for note in hold.notes:
            print("  note:", note)


if __name__ == "__main__":
    main()
