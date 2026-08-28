#!/usr/bin/env python3

from passagen.execution_logging import archive_execution_logs


def main() -> None:
    moved = archive_execution_logs()
    if not moved:
        print("No execution logs to archive.")
        return
    for source, target in moved:
        print(f"Moved: {source} -> {target}")


if __name__ == "__main__":
    main()
