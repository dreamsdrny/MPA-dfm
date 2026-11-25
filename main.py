"""CLI startup entry; legacy orchestration lives in Application, not here."""
import sys

__all__ = ["DEMO_NETS", "main", "run_pipeline"]


def __getattr__(name):
    if name in ("DEMO_NETS", "run_pipeline"):
        from src.application import legacy_cli
        return getattr(legacy_cli, name)
    raise AttributeError(name)


def main():
    if not any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        from src.bootstrap import prepare_startup
        license_path = None
        args = sys.argv[1:]
        for index, arg in enumerate(args):
            if arg.startswith("--license="):
                license_path = arg.split("=", 1)[1]
            elif arg == "--license" and index + 1 < len(args):
                license_path = args[index + 1]
        prepare_startup(license_path=license_path)
    from src.application.legacy_cli import main as run_cli
    def launch_ui():
        from src.ui.main_window import MainWindow
        MainWindow().run()
    return run_cli(ui_launcher=launch_ui)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
