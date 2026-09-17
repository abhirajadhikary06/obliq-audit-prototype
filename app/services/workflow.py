from app import db
from app.models import AuditEvent, DocumentStatus


class WorkflowError(ValueError):
    """Raised when a document action is not valid for its current state."""


ALLOWED_TRANSITIONS = {
    DocumentStatus.PENDING: {DocumentStatus.UPLOADED},
    DocumentStatus.UPLOADED: {DocumentStatus.UNDER_REVIEW},
    DocumentStatus.UNDER_REVIEW: {
        DocumentStatus.APPROVED,
        DocumentStatus.CORRECTION_REQUIRED,
    },
    DocumentStatus.CORRECTION_REQUIRED: {DocumentStatus.UPLOADED},
    DocumentStatus.APPROVED: set(),
}


def add_audit_event(actor, action, entity_type, entity_id=None, entity_name=None,
                    client_id=None, document_id=None, details=None):
    """Add an audit row to the current transaction; the caller commits it."""
    event = AuditEvent(
        firm_id=actor.firm_id,
        actor_id=actor.id,
        actor_name=actor.name,
        client_id=client_id,
        document_id=document_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        details=details,
    )
    db.session.add(event)
    return event


def transition_document(document, target_status, actor, action, details=None,
                        comment=None):
    """Apply one legal transition and append its audit event atomically."""
    if target_status not in ALLOWED_TRANSITIONS.get(document.status, set()):
        raise WorkflowError(
            f"Cannot transition document from {document.status.value} to "
            f"{target_status.value}."
        )
    if target_status == DocumentStatus.CORRECTION_REQUIRED and not comment:
        raise WorkflowError("A correction comment is required.")

    document.status = target_status
    if target_status == DocumentStatus.CORRECTION_REQUIRED:
        document.review_comment = comment
    elif target_status == DocumentStatus.UPLOADED:
        document.review_comment = None

    add_audit_event(
        actor=actor,
        action=action,
        entity_type="document",
        entity_id=document.id,
        entity_name=document.name,
        client_id=document.client_id,
        document_id=document.id,
        details=details,
    )
    return document


def prepare_document_upload(document, actor, object_key, original_filename):
    """Apply upload metadata and the PENDING/CORRECTION_REQUIRED upload transition."""
    validate_document_upload(document)
    old_status = document.status.value
    is_revision = document.status == DocumentStatus.CORRECTION_REQUIRED

    document.object_key = object_key
    document.original_filename = original_filename
    document.uploaded_by = actor.id
    from datetime import datetime
    document.uploaded_at = datetime.utcnow()
    if is_revision:
        document.version += 1

    action = "uploaded_revised_document" if is_revision else "uploaded_document"
    transition_document(
        document,
        DocumentStatus.UPLOADED,
        actor,
        action,
        details=f"Uploaded '{original_filename}' (version {document.version}). "
                f"Previous status: {old_status}",
    )


def validate_document_upload(document):
    if document.status not in (DocumentStatus.PENDING, DocumentStatus.CORRECTION_REQUIRED):
        raise WorkflowError(
            f"Documents in {document.status.value.replace('_', ' ')} status cannot be uploaded."
        )