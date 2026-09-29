import sys

if (len(sys.argv) >= 3 and sys.argv[1] == "--self-test") or (len(sys.argv) >= 4 and sys.argv[1] == "--verify-scan"):
    destination_arg = sys.argv[3] if sys.argv[1] == "--verify-scan" else sys.argv[2]
    try:
        from rockform.verification import run, run_scan
        code = run_scan(sys.argv[2], destination_arg) if sys.argv[1] == "--verify-scan" else run(destination_arg)
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
