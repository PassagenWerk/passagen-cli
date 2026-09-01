import io

from rich.console import Console

from passagen.cli.runtime import ConsoleProgress


def test_progress_persists_each_distinct_message_once() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=120)

    with ConsoleProgress(console, "Start") as progress:
        progress.update("Step one")
        progress.update("Step one")
        progress.update("Step two")

    output = buffer.getvalue()
    assert output.count("Start") == 1
    assert output.count("Step one") == 1
    assert output.count("Step two") == 1
