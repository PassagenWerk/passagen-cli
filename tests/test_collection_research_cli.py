import json
from pathlib import Path
from typing import Literal

import pytest
from passagen.assistant import (
    AnswerClaim,
    AssistantNotFoundError,
    AssistantTurn,
    Citation,
    CitationArtifactKind,
    CollectionSourceSnapshot,
    ContextPlan,
    ContextSource,
    Conversation,
    ConversationDetail,
    ConversationScope,
    Message,
    MessageRole,
    MessageStatus,
    PaperSourceSnapshot,
    ProviderCallError,
    QaRecord,
    QuestionIntent,
    SourceSnapshot,
    SourceStatus,
    StructuredAnswer,
    TurnDisposition,
    source_fingerprint,
)
from passagen.research import (
    CollectionReport,
    CollectionReportRecord,
    CollectionReportResult,
    ReportKind,
    ReportSection,
    SynthesisCoverage,
)
from typer.testing import CliRunner

from passagen_cli import app

runner = CliRunner()

SHA = "1" * 64


def _snapshot(collection_id: str = "collection-1") -> SourceSnapshot:
    return SourceSnapshot(
        scope=ConversationScope.COLLECTION,
        collection=CollectionSourceSnapshot(
            collection_id=collection_id,
            name="Systems",
            papers=[PaperSourceSnapshot(paper_id="paper-1", status="outlined")],
        ),
        context_builder_version="1",
        retrieval_version="1",
        prompt_version="1",
        answer_schema_version="1",
    )


def _citation() -> Citation:
    return Citation(
        citation_id="citation-1",
        paper_id="paper-1",
        artifact_kind=CitationArtifactKind.SUMMARY,
        artifact_id="artifact-1",
        artifact_sha256=SHA,
        summary_path="problem.context",
    )


def _answer() -> StructuredAnswer:
    return StructuredAnswer(
        standalone_question="What do the papers evaluate?",
        intent=QuestionIntent.OVERVIEW,
        answer_markdown="Both papers evaluate systems.",
        claims=[AnswerClaim(text="The paper evaluates a system.", citation_ids=["citation-1"])],
        citations=[_citation()],
    )


def _qa_record(conversation_id: str = "conversation-1") -> QaRecord:
    snapshot = _snapshot()
    answer = _answer()
    return QaRecord(
        id="qa-1",
        conversation_id=conversation_id,
        question_message_id="message-q",
        answer_message_id="message-a",
        standalone_question=answer.standalone_question,
        normalized_question="what do the papers evaluate",
        normalized_question_hash="2" * 64,
        intent=answer.intent,
        context_plan=ContextPlan(
            standalone_question=answer.standalone_question,
            intent=answer.intent,
            sources=[ContextSource.PAPER_SUMMARIES],
        ),
        answer=answer,
        source_snapshot=snapshot,
        source_fingerprint=source_fingerprint(snapshot),
        prompt_version="1",
        answer_schema_version="1",
        created_at="2026-09-08T00:00:00Z",
    )


def _message(message_id: str, role: MessageRole, content: str) -> Message:
    return Message(
        id=message_id,
        conversation_id="conversation-1",
        role=role,
        content=content,
        status=MessageStatus.COMPLETED,
        created_at="2026-09-08T00:00:00Z",
    )


def _turn(disposition: TurnDisposition = TurnDisposition.GENERATED) -> AssistantTurn:
    return AssistantTurn(
        conversation_id="conversation-1",
        run_id="run-1",
        question_message=_message("message-q", MessageRole.USER, "What do the papers evaluate?"),
        answer_message=_message(
            "message-a", MessageRole.ASSISTANT, "Both papers evaluate systems."
        ),
        qa_record=_qa_record(),
        disposition=disposition,
    )


def _conversation(
    conversation_id: str = "conversation-1",
    *,
    scope: ConversationScope = ConversationScope.COLLECTION,
    collection_id: str | None = "collection-1",
    paper_id: str | None = None,
) -> Conversation:
    return Conversation(
        id=conversation_id,
        scope=scope,
        collection_id=collection_id,
        paper_id=paper_id,
        title="Collection conversation",
        created_at="2026-09-08T00:00:00Z",
        updated_at="2026-09-08T00:00:00Z",
    )


class _FakeConversationService:
    def __init__(
        self,
        turn: AssistantTurn | None = None,
        error: Exception | None = None,
        status: SourceStatus | None = None,
        conversation: Conversation | None = None,
    ) -> None:
        self.turn = turn or _turn()
        self.error = error
        self.status = status or SourceStatus()
        self.conversation = conversation
        self.created: list[str | None] = []
        self.asked: list[tuple[str, str, bool]] = []

    def create_conversation(
        self,
        paper_id: str | None = None,
        *,
        collection_id: str | None = None,
        title: str | None = None,
    ) -> Conversation:
        self.created.append(collection_id)
        if self.error is not None:
            raise self.error
        return _conversation(collection_id=collection_id)

    def get_conversation(self, conversation_id: str) -> ConversationDetail:
        if self.error is not None:
            raise self.error
        conversation = self.conversation or _conversation(conversation_id)
        return ConversationDetail(conversation, ())

    def ask(
        self, conversation_id: str, question: str, *, force_regenerate: bool = False
    ) -> AssistantTurn:
        self.asked.append((conversation_id, question, force_regenerate))
        if self.error is not None:
            raise self.error
        return self.turn

    def source_status(self, record: QaRecord) -> SourceStatus:
        return self.status


def _report(kind: ReportKind, *, partial: bool = False) -> CollectionReport:
    return CollectionReport(
        kind=kind,
        title="Collection Review",
        user_prompt="Compare the evaluation setups" if kind is ReportKind.CUSTOM else None,
        coverage=SynthesisCoverage(
            included_paper_ids=["paper-1"],
            missing_summary_paper_ids=["paper-2"] if partial else [],
            partial=partial,
        ),
        sections=[ReportSection(heading="Overview", body_markdown="Both papers study systems.")],
        citations=[_citation()],
    )


def _report_result(
    kind: ReportKind = ReportKind.REVIEW,
    *,
    partial: bool = False,
    disposition: Literal["generated", "reused"] = "generated",
    status: SourceStatus | None = None,
) -> CollectionReportResult:
    report = _report(kind, partial=partial)
    return CollectionReportResult(
        record=CollectionReportRecord(
            id="report-1",
            collection_id="collection-1",
            kind=kind,
            status="completed",
            title=report.title,
            user_prompt=report.user_prompt,
            source_snapshot_json="{}",
            source_fingerprint=SHA,
            run_id="run-1",
            created_at="2026-09-08T00:00:00Z",
        ),
        report=report,
        artifacts=[],
        source_status=status or SourceStatus(),
        disposition=disposition,
    )


class _FakeReportService:
    def __init__(
        self,
        result: CollectionReportResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, ReportKind, str | None, bool, bool]] = []

    def create_report(
        self,
        collection_id: str,
        kind: ReportKind,
        *,
        user_prompt: str | None = None,
        allow_partial: bool = False,
        force: bool = False,
    ) -> CollectionReportResult:
        self.calls.append((collection_id, kind, user_prompt, allow_partial, force))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _invoke_ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeConversationService,
    arguments: list[str],
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "passagen_cli.commands.organization._conversation_service", lambda _ctx: service
    )
    return runner.invoke(app, ["--data-dir", str(tmp_path / "data"), *arguments])


def _invoke_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeReportService,
    arguments: list[str],
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("passagen_cli.commands.organization._report_service", lambda _ctx: service)
    return runner.invoke(app, ["--data-dir", str(tmp_path / "data"), *arguments])


@pytest.mark.parametrize("command", ["ask", "review", "research"])
def test_collection_research_commands_expose_help(command: str) -> None:
    result = runner.invoke(app, ["collection", command, "--help"])

    assert result.exit_code == 0
    assert "markdown" in result.stdout


def test_collection_ask_creates_conversation_and_prints_only_answer_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeConversationService()

    result = _invoke_ask(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "ask", "collection-1", "What do the papers evaluate?"],
    )

    assert result.exit_code == 0
    assert result.stdout == "Both papers evaluate systems.\n"
    assert service.created == ["collection-1"]
    assert service.asked == [("conversation-1", "What do the papers evaluate?", False)]
    assert "Collection conversation created: conversation-1" in result.stderr
    assert "Collection answer: generated, run=run-1, conversation=conversation-1" in result.stderr


def test_collection_ask_json_contains_only_the_core_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeConversationService()

    result = _invoke_ask(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "ask",
            "collection-1",
            "What do the papers evaluate?",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["answer_markdown"] == "Both papers evaluate systems."
    assert payload["citations"][0]["paper_id"] == "paper-1"


def test_collection_ask_continues_conversation_and_forwards_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeConversationService(turn=_turn(TurnDisposition.EXACT_REUSE))

    result = _invoke_ask(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "ask",
            "collection-1",
            "What do the papers evaluate?",
            "--conversation-id",
            "conversation-9",
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert service.created == []
    assert service.asked == [("conversation-9", "What do the papers evaluate?", True)]
    assert "Collection conversation created" not in result.stderr
    assert "Collection answer: exact_reuse" in result.stderr


def test_collection_ask_rejects_conversation_from_another_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeConversationService(
        conversation=_conversation("conversation-9", collection_id="collection-2")
    )

    result = _invoke_ask(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "ask",
            "collection-1",
            "What do the papers evaluate?",
            "--conversation-id",
            "conversation-9",
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "does not belong to collection collection-1" in result.stderr
    assert service.asked == []


def test_collection_ask_rejects_paper_scope_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeConversationService(
        conversation=_conversation(
            "conversation-9",
            scope=ConversationScope.PAPER,
            collection_id=None,
            paper_id="paper-1",
        )
    )

    result = _invoke_ask(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "ask",
            "collection-1",
            "What do the papers evaluate?",
            "--conversation-id",
            "conversation-9",
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert service.asked == []


def test_collection_ask_reports_missing_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeConversationService(error=AssistantNotFoundError("Conversation not found: x"))

    result = _invoke_ask(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "ask",
            "collection-1",
            "What do the papers evaluate?",
            "--conversation-id",
            "conversation-9",
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "Collection ask error: Conversation not found: x"


def test_collection_ask_displays_stale_sources_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeConversationService(
        status=SourceStatus(stale=True, reasons=["collection membership changed"])
    )

    result = _invoke_ask(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "ask", "collection-1", "What do the papers evaluate?"],
    )

    assert result.exit_code == 0
    assert result.stdout == "Both papers evaluate systems.\n"
    assert "Stale sources: collection membership changed" in result.stderr


def test_collection_ask_provider_failure_is_concise_and_only_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeConversationService(error=ProviderCallError("provider unavailable"))

    result = _invoke_ask(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "ask", "collection-1", "What do the papers evaluate?", "--format", "json"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "Collection ask error: provider unavailable"


def test_collection_review_requests_a_review_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(_report_result(ReportKind.REVIEW, partial=True))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "review", "collection-1", "--allow-partial", "--force"],
    )

    assert result.exit_code == 0
    assert result.stdout.startswith("# Collection Review\n")
    assert "## Citations" in result.stdout
    assert service.calls == [("collection-1", ReportKind.REVIEW, None, True, True)]
    assert "Collection review report: generated, run=run-1" in result.stderr
    assert "Partial coverage; missing summaries: paper-2" in result.stderr


def test_collection_review_json_contains_only_the_core_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(_report_result(ReportKind.REVIEW))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "review", "collection-1", "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["kind"] == "review"
    assert payload["sections"][0]["heading"] == "Overview"


@pytest.mark.parametrize("kind", [ReportKind.REVIEW, ReportKind.COMPARISON, ReportKind.GAPS])
def test_collection_research_supports_each_builtin_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: ReportKind
) -> None:
    service = _FakeReportService(_report_result(kind))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "research", "collection-1", "--kind", kind.value],
    )

    assert result.exit_code == 0
    assert service.calls == [("collection-1", kind, None, False, False)]
    assert f"Collection {kind.value} report: generated" in result.stderr


def test_collection_research_custom_prompt_from_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(_report_result(ReportKind.CUSTOM))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "research",
            "collection-1",
            "--kind",
            "custom",
            "--prompt",
            "Compare the evaluation setups",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert service.calls == [
        ("collection-1", ReportKind.CUSTOM, "Compare the evaluation setups", False, False)
    ]
    assert json.loads(result.stdout)["user_prompt"] == "Compare the evaluation setups"


def test_collection_research_custom_prompt_from_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Compare the evaluation setups\n", encoding="utf-8")
    service = _FakeReportService(_report_result(ReportKind.CUSTOM))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "research", "collection-1", "--prompt-file", str(prompt_file)],
    )

    assert result.exit_code == 0
    assert service.calls == [
        ("collection-1", ReportKind.CUSTOM, "Compare the evaluation setups\n", False, False)
    ]


def test_collection_research_custom_requires_a_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(_report_result(ReportKind.CUSTOM))

    result = _invoke_report(
        tmp_path, monkeypatch, service, ["collection", "research", "collection-1"]
    )

    assert result.exit_code == 2
    assert "require a question via --prompt or --prompt-file" in result.stderr
    assert service.calls == []


def test_collection_research_rejects_blank_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(_report_result(ReportKind.CUSTOM))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "research", "collection-1", "--prompt", "   "],
    )

    assert result.exit_code == 2
    assert service.calls == []


def test_collection_research_rejects_prompt_for_builtin_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(_report_result(ReportKind.REVIEW))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "research", "collection-1", "--kind", "review", "--prompt", "ignored"],
    )

    assert result.exit_code == 2
    assert "only valid with --kind custom" in result.stderr
    assert service.calls == []


def test_collection_research_rejects_conflicting_prompt_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("From file", encoding="utf-8")
    service = _FakeReportService(_report_result(ReportKind.CUSTOM))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "research",
            "collection-1",
            "--prompt",
            "From option",
            "--prompt-file",
            str(prompt_file),
        ],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr
    assert service.calls == []


def test_collection_research_rejects_missing_prompt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(_report_result(ReportKind.CUSTOM))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "research",
            "collection-1",
            "--prompt-file",
            str(tmp_path / "missing.txt"),
        ],
    )

    assert result.exit_code == 2
    assert service.calls == []


def test_collection_research_displays_stale_and_partial_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_payload = _report_result(
        ReportKind.GAPS,
        partial=True,
        disposition="reused",
        status=SourceStatus(stale=True, reasons=["summary artifact replaced"]),
    )
    service = _FakeReportService(result_payload)

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "research", "collection-1", "--kind", "gaps", "--allow-partial"],
    )

    assert result.exit_code == 0
    assert result.stdout.startswith("# Collection Review\n")
    assert "Collection gaps report: reused, run=run-1" in result.stderr
    assert "Partial coverage; missing summaries: paper-2" in result.stderr
    assert "Stale sources: summary artifact replaced" in result.stderr


def test_collection_report_provider_failure_is_concise_and_only_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(error=ProviderCallError("provider unavailable"))

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "review", "collection-1", "--format", "json"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "Collection report error: provider unavailable"


@pytest.mark.parametrize("command", ["ask", "review", "research"])
def test_collection_research_commands_reject_unknown_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    conversation_service = _FakeConversationService()
    report_service = _FakeReportService(_report_result())
    monkeypatch.setattr(
        "passagen_cli.commands.organization._conversation_service",
        lambda _ctx: conversation_service,
    )
    monkeypatch.setattr(
        "passagen_cli.commands.organization._report_service", lambda _ctx: report_service
    )
    monkeypatch.chdir(tmp_path)

    arguments = ["collection", command, "collection-1", "--format", "yaml"]
    if command == "ask":
        arguments.insert(3, "question")
    result = runner.invoke(app, ["--data-dir", str(tmp_path / "data"), *arguments])

    assert result.exit_code == 2
    assert conversation_service.asked == []
    assert report_service.calls == []


def test_collection_research_rejects_unknown_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeReportService(_report_result())

    result = _invoke_report(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "research", "collection-1", "--kind", "essay"],
    )

    assert result.exit_code == 2
    assert service.calls == []
