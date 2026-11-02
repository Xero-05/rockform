import sys

if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
    # Generate one hold of every type and plan, drill and export one route into the given folder;
    # startup-error.txt there on failure.
    from pathlib import Path
    destination = Path(sys.argv[2])
    destination.mkdir(parents=True, exist_ok=True)
    try:
        from rockform.holdgen.__main__ import main
        from rockform.holdgen.shapes import HOLD_TYPES
        from rockform.holdgen.route import main as route_main
        for hold_type in HOLD_TYPES:
            main(["--type", hold_type, "--seed", "1", "--out", str(destination)])
        route_main(["--grade", "V3", "--wall-angle", "20", "--seed", "1", "--validate", "--out",
                    str(destination / "route")])
        (destination / "self-test-passed.txt").write_text("ok\n", encoding="utf-8")
        code = 0
    except Exception:
        import traceback
        (destination / "startup-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        code = 1
    sys.exit(code)
else:
    from rockform.holdgen.app import main
    sys.exit(main())
