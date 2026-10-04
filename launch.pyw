import sys

if (len(sys.argv) >= 3 and sys.argv[1] == "--self-test") or (len(sys.argv) >= 4 and sys.argv[1] in ("--verify-scan", "--verify-quick-repair", "--verify-fill-repair", "--verify-preprocessing")):
    destination_arg = sys.argv[2] if sys.argv[1] == "--self-test" else sys.argv[3]
    try:
        from rockform.verification import run, run_scan, run_quick_repair, run_preprocessing
        code = (run_scan(sys.argv[2], destination_arg) if sys.argv[1] == "--verify-scan" else
                run_quick_repair(sys.argv[2], destination_arg) if sys.argv[1] == "--verify-quick-repair" else
                run_quick_repair(sys.argv[2], destination_arg, methods=('fill', 'fill-trim'), trim_heights=(0., 10., 30., 50.)) if sys.argv[1] == "--verify-fill-repair" else
                run_preprocessing(sys.argv[2], destination_arg) if sys.argv[1] == "--verify-preprocessing" else run(destination_arg))
    except Exception:
        import traceback
        from pathlib import Path
        destination = Path(destination_arg)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "startup-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        code = 1
    sys.exit(code)
else:
    from rockform.app import main
    sys.exit(main())
