from h2lab_tga_app.desktop.app import main as desktop_main


def run() -> None:
    raise SystemExit(desktop_main())


def cli() -> None:
    run()


if __name__ == "__main__":
    run()
