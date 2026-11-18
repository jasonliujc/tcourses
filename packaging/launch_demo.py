"""PyInstaller entry point for "TCourses Demo": same app, generated sample data."""

import sys

from app.desktop import main

if __name__ == "__main__":
    main(["--demo", *sys.argv[1:]])
