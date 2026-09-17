from types import SimpleNamespace

import pytest

from app.models import DocumentStatus
from app.services import workflow


@pytest.fixture
def actor():
    return SimpleNamespace(id=7, firm_id=3, name="Reviewer")


@pytest.fixture
def document():
    return SimpleNamespace(
        id=11,
        client_id=19,
        name="Bank Statement",
        status=DocumentStatus.PENDING,
        version=1,
        object_key=None,
        original_filename=None,
        uploaded_by=None,
        uploaded_at=None,
        review_comment=None,
    )


def capture_audits(monkeypatch):
    events = []
    monkeypatch.setattr(workflow, "add_audit_event", lambda **kwargs: events.append(kwargs))
    return events


def test_valid_lifecycle_records_each_transition(monkeypatch, actor, document):
    events = capture_audits(monkeypatch)

    workflow.prepare_document_upload(document, actor, "firm/3/client/19/doc/11/a.pdf", "a.pdf")
    workflow.transition_document(
        document, DocumentStatus.UNDER_REVIEW, actor, "started_review"
    )
    workflow.transition_document(
        document,
        DocumentStatus.CORRECTION_REQUIRED,
        actor,
        "requested_correction",
        comment="Missing schedule",
        details="Missing schedule",
    )
    workflow.prepare_document_upload(document, actor, "firm/3/client/19/doc/11/b.pdf", "b.pdf")

    assert document.status == DocumentStatus.UPLOADED
    assert document.version == 2
    assert [event["action"] for event in events] == [
        "uploaded_document",
        "started_review",
        "requested_correction",
        "uploaded_revised_document",
    ]
    assert all(event["document_id"] == document.id for event in events)
    assert all(event["client_id"] == document.client_id for event in events)


def test_approved_document_cannot_be_uploaded(monkeypatch, actor, document):
    capture_audits(monkeypatch)
    document.status = DocumentStatus.APPROVED
    document.object_key = "firm/3/client/19/doc/11/a.pdf"

    with pytest.raises(workflow.WorkflowError):
        workflow.prepare_document_upload(document, actor, "firm/3/client/19/doc/11/b.pdf", "b.pdf")


def test_correction_requires_a_reason(monkeypatch, actor, document):
    capture_audits(monkeypatch)
    document.status = DocumentStatus.UNDER_REVIEW

    with pytest.raises(workflow.WorkflowError, match="comment is required"):
        workflow.transition_document(
            document,
            DocumentStatus.CORRECTION_REQUIRED,
            actor,
            "requested_correction",
        )


def test_invalid_transition_is_rejected(monkeypatch, actor, document):
    capture_audits(monkeypatch)

    with pytest.raises(workflow.WorkflowError):
        workflow.transition_document(document, DocumentStatus.APPROVED, actor, "approved_document")