from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, abort, send_file
from flask_login import login_required, current_user
from app import db, cache, limiter
from sqlalchemy import or_
from app.models import Client, Document, DocumentStatus, AuditEvent, Role, User, Firm
from app.services.workflow import (
    WorkflowError,
    add_audit_event,
    prepare_document_upload,
    transition_document,
    validate_document_upload,
)
from io import BytesIO

main_bp = Blueprint('main', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'xlsx', 'xls', 'csv', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_client_or_404(client_id):
    client = Client.query.filter_by(id=client_id, firm_id=current_user.firm_id).first()
    if not client:
        abort(404)
    return client

def get_document_or_404(doc_id):
    doc = Document.query.join(Client).filter(
        Document.id == doc_id,
        Client.firm_id == current_user.firm_id
    ).first()
    if not doc:
        abort(404)
    return doc

@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))

@main_bp.route('/dashboard')
@login_required
@limiter.limit("60 per minute")
def dashboard():
    # Cached stats — critical under many concurrent users of the same firm
    cache_key = f"dashboard_stats:{current_user.firm_id}"
    stats = cache.get(cache_key)
    if stats is None:
        total_docs = Document.query.join(Client).filter(Client.firm_id == current_user.firm_id).count()
        pending_review = Document.query.join(Client).filter(
            Client.firm_id == current_user.firm_id,
            Document.status.in_([DocumentStatus.UPLOADED, DocumentStatus.UNDER_REVIEW])
        ).count()
        correction_needed = Document.query.join(Client).filter(
            Client.firm_id == current_user.firm_id,
            Document.status == DocumentStatus.CORRECTION_REQUIRED
        ).count()
        approved = Document.query.join(Client).filter(
            Client.firm_id == current_user.firm_id,
            Document.status == DocumentStatus.APPROVED
        ).count()
        stats = {
            'total_docs': total_docs,
            'pending_review': pending_review,
            'correction_needed': correction_needed,
            'approved': approved
        }
        cache.set(cache_key, stats, timeout=45)

    clients = Client.query.filter_by(firm_id=current_user.firm_id).order_by(Client.name).all()

    return render_template('dashboard.html',
                           clients=clients,
                           total_docs=stats['total_docs'],
                           pending_review=stats['pending_review'],
                           correction_needed=stats['correction_needed'],
                           approved=stats['approved'])

@main_bp.route('/clients')
@login_required
def clients():
    clients = Client.query.filter_by(firm_id=current_user.firm_id).order_by(Client.name).all()
    return render_template('clients_list.html', clients=clients)

def require_admin():
    if current_user.role != Role.ADMIN:
        abort(403)

@main_bp.route('/admin/users')
@login_required
def admin_users():
    require_admin()
    users = User.query.filter_by(firm_id=current_user.firm_id).order_by(User.name).all()
    return render_template('admin_users.html', users=users)

@main_bp.route('/admin/users/new', methods=['POST'])
@login_required
def create_user():
    require_admin()
    email = request.form.get('email', '').strip().lower()
    name = request.form.get('name', '').strip()
    role_value = request.form.get('role', '').strip().lower()
    password = request.form.get('password', '')
    if not all((email, name, role_value, password)):
        flash('All user fields are required.', 'danger')
        return redirect(url_for('main.admin_users'))
    try:
        role = Role(role_value)
    except ValueError:
        flash('Invalid role.', 'danger')
        return redirect(url_for('main.admin_users'))
    if User.query.filter_by(email=email).first():
        flash('That email is already in use.', 'danger')
        return redirect(url_for('main.admin_users'))
    user = User(email=email, name=name, role=role, firm_id=current_user.firm_id)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    add_audit_event(current_user, 'created_user', 'user', user.id, user.name,
                    details=f'Created {role.value} account {email}')
    db.session.commit()
    flash(f'User {name} created.', 'success')
    return redirect(url_for('main.admin_users'))

@main_bp.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
def delete_user(user_id):
    require_admin()
    user = User.query.filter_by(id=user_id, firm_id=current_user.firm_id).first_or_404()
    if user.id == current_user.id:
        flash('You cannot remove your own account.', 'danger')
        return redirect(url_for('main.admin_users'))
    if user.role == Role.ADMIN and User.query.filter_by(
            firm_id=current_user.firm_id, role=Role.ADMIN, is_active=True
    ).count() <= 1:
        flash('The firm must retain at least one active administrator.', 'danger')
        return redirect(url_for('main.admin_users'))

    audit_count = AuditEvent.query.filter_by(
        firm_id=current_user.firm_id, actor_id=user.id
    ).count()
    if audit_count:
        user.is_active = False
        action = 'deactivated_user'
        message = f'User {user.name} was deactivated to preserve audit history.'
    else:
        db.session.delete(user)
        action = 'deleted_user'
        message = f'User {user.name} was deleted.'
    add_audit_event(current_user, action, 'user', user.id, user.name,
                    details=message)
    db.session.commit()
    flash(message, 'success')
    return redirect(url_for('main.admin_users'))

@main_bp.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    require_admin()
    firm = Firm.query.filter_by(id=current_user.firm_id).first_or_404()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Firm name cannot be empty.', 'danger')
            return redirect(url_for('main.admin_settings'))
        existing = Firm.query.filter(Firm.name == name, Firm.id != firm.id).first()
        if existing:
            flash('That firm name is already in use.', 'danger')
            return redirect(url_for('main.admin_settings'))
        old_name = firm.name
        firm.name = name
        add_audit_event(current_user, 'updated_firm', 'firm', firm.id, name,
                        details=f'Renamed firm from {old_name} to {name}')
        db.session.commit()
        flash('Firm settings updated.', 'success')
    return render_template('admin_settings.html', firm=firm)

@main_bp.route('/clients/new', methods=['GET', 'POST'])
@login_required
@limiter.limit("20 per minute")
def create_client():
    if current_user.role != Role.ADMIN:
        abort(403)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Client name is required.', 'danger')
            return redirect(url_for('main.create_client'))

        client = Client(name=name, firm_id=current_user.firm_id, created_by=current_user.id)
        db.session.add(client)
        db.session.flush()

        default_docs = [
            "Bank Statement", "Sales Register", "Purchase Register",
            "GST Return", "Expense Summary"
        ]
        for doc_name in default_docs:
            doc = Document(name=doc_name, client_id=client.id, status=DocumentStatus.PENDING)
            db.session.add(doc)

        add_audit_event(
            actor=current_user,
            action="created_client",
            entity_type="client",
            entity_id=client.id,
            entity_name=client.name,
            client_id=client.id,
            details=f"Created client with {len(default_docs)} required documents"
        )
        db.session.commit()
        cache.delete(f"dashboard_stats:{current_user.firm_id}")
        flash(f'Client "{name}" created successfully.', 'success')
        return redirect(url_for('main.client_detail', client_id=client.id))

    return render_template('create_client.html')

@main_bp.route('/clients/<int:client_id>/documents/new', methods=['POST'])
@login_required
def add_document_requirement(client_id):
    client = get_client_or_404(client_id)
    if not current_user.is_staff():
        abort(403)
    name = request.form.get('name', '').strip()
    if not name:
        flash('Document name is required.', 'danger')
        return redirect(url_for('main.client_detail', client_id=client.id))
    if Document.query.filter_by(client_id=client.id, name=name).first():
        flash('That document requirement already exists.', 'danger')
        return redirect(url_for('main.client_detail', client_id=client.id))
    document = Document(name=name, client_id=client.id, status=DocumentStatus.PENDING)
    db.session.add(document)
    db.session.flush()
    add_audit_event(current_user, 'added_document_requirement', 'document', document.id,
                    document.name, client_id=client.id, document_id=document.id,
                    details=f'Added requirement {name}')
    db.session.commit()
    cache.delete(f"dashboard_stats:{current_user.firm_id}")
    flash(f'Document requirement "{name}" added.', 'success')
    return redirect(url_for('main.client_detail', client_id=client.id))

@main_bp.route('/clients/<int:client_id>')
@login_required
def client_detail(client_id):
    client = get_client_or_404(client_id)
    documents = Document.query.filter_by(client_id=client.id).order_by(Document.name).all()
    return render_template('client_detail.html', client=client, documents=documents)

@main_bp.route('/documents/<int:doc_id>')
@login_required
def document_detail(doc_id):
    doc = get_document_or_404(doc_id)
    history = AuditEvent.query.filter(
        AuditEvent.firm_id == current_user.firm_id,
        AuditEvent.entity_type == 'document',
        AuditEvent.entity_id == doc.id
    ).order_by(AuditEvent.created_at.desc()).limit(100).all()

    return render_template('document_detail.html', doc=doc, history=history)

@main_bp.route('/documents/<int:doc_id>/upload', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def upload_document(doc_id):
    doc = get_document_or_404(doc_id)

    if not current_user.is_staff() and current_user.role != Role.ADMIN:
        flash('Only staff can upload documents.', 'danger')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    try:
        validate_document_upload(doc)
    except WorkflowError as error:
        flash(str(error), 'warning')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    if 'file' not in request.files:
        flash('No file selected.', 'danger')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected.', 'danger')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    if not allowed_file(file.filename):
        flash('File type not allowed. Allowed: PDF, images, Excel, Word.', 'danger')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    # Upload to MinIO (shared object store — works with any number of app replicas)
    from app.storage import upload_file
    try:
        object_key, original = upload_file(file, current_user.firm_id, doc.client_id, doc.id)
    except Exception as e:
        current_app.logger.exception("Upload to object storage failed")
        flash('Upload failed. Please try again.', 'danger')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    try:
        prepare_document_upload(doc, current_user, object_key, original)
    except WorkflowError as error:
        flash(str(error), 'warning')
        return redirect(url_for('main.document_detail', doc_id=doc.id))
    db.session.commit()
    cache.delete(f"dashboard_stats:{current_user.firm_id}")

    flash(f'Document "{doc.name}" uploaded successfully (v{doc.version}).', 'success')
    return redirect(url_for('main.document_detail', doc_id=doc.id))

@main_bp.route('/documents/<int:doc_id>/start_review', methods=['POST'])
@login_required
@limiter.limit("60 per minute")
def start_review(doc_id):
    doc = get_document_or_404(doc_id)

    if not current_user.is_reviewer():
        flash('Only reviewers can start a review.', 'danger')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    if doc.status != DocumentStatus.UPLOADED:
        flash('Document must be in Uploaded status to start review.', 'warning')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    try:
        transition_document(
            doc,
            DocumentStatus.UNDER_REVIEW,
            current_user,
            "started_review",
            details=f"Started reviewing version {doc.version}",
        )
    except WorkflowError as error:
        flash(str(error), 'warning')
        return redirect(url_for('main.document_detail', doc_id=doc.id))
    db.session.commit()
    cache.delete(f"dashboard_stats:{current_user.firm_id}")
    flash('Review started.', 'info')
    return redirect(url_for('main.document_detail', doc_id=doc.id))

@main_bp.route('/documents/<int:doc_id>/review', methods=['POST'])
@login_required
@limiter.limit("60 per minute")
def review_document(doc_id):
    doc = get_document_or_404(doc_id)

    if not current_user.is_reviewer():
        flash('Only reviewers can approve or request corrections.', 'danger')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    if doc.status != DocumentStatus.UNDER_REVIEW:
        flash('Document is not in a reviewable state.', 'warning')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    action_type = request.form.get('action')
    comment = request.form.get('comment', '').strip()

    if action_type == 'approve':
        doc.review_comment = comment or None
        try:
            transition_document(
                doc,
                DocumentStatus.APPROVED,
                current_user,
                "approved_document",
                details=comment or f"Approved version {doc.version}",
            )
        except WorkflowError as error:
            flash(str(error), 'warning')
            return redirect(url_for('main.document_detail', doc_id=doc.id))
        flash(f'Document "{doc.name}" approved.', 'success')
    elif action_type == 'request_correction':
        if not comment:
            flash('A comment/reason is required when requesting correction.', 'danger')
            return redirect(url_for('main.document_detail', doc_id=doc.id))
        try:
            transition_document(
                doc,
                DocumentStatus.CORRECTION_REQUIRED,
                current_user,
                "requested_correction",
                details=comment,
                comment=comment,
            )
        except WorkflowError as error:
            flash(str(error), 'warning')
            return redirect(url_for('main.document_detail', doc_id=doc.id))
        flash(f'Correction requested for "{doc.name}".', 'warning')
    else:
        flash('Invalid action.', 'danger')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    db.session.commit()
    cache.delete(f"dashboard_stats:{current_user.firm_id}")
    return redirect(url_for('main.document_detail', doc_id=doc.id))

@main_bp.route('/documents/<int:doc_id>/download')
@login_required
@limiter.limit("30 per minute")
def download_document(doc_id):
    doc = get_document_or_404(doc_id)
    if not doc.object_key:
        flash('No file uploaded yet.', 'warning')
        return redirect(url_for('main.document_detail', doc_id=doc.id))

    # Prefer presigned URL redirect for large files / CDN-style delivery
    try:
        from app.storage import get_presigned_url
        url = get_presigned_url(doc.object_key, expires_seconds=300)
        return redirect(url)
    except Exception:
        # Fallback: stream through the app
        from app.storage import download_bytes
        data = download_bytes(doc.object_key)
        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=doc.original_filename or 'document'
        )

@main_bp.route('/audit-history')
@login_required
@limiter.limit("30 per minute")
def audit_history():
    if not current_user.is_reviewer():
        flash('Only reviewers can view the full audit history.', 'warning')
        return redirect(url_for('main.dashboard'))

    selected_action = request.args.get('action', '').strip()
    selected_actor = request.args.get('actor', '').strip()
    selected_client_id = request.args.get('client_id', type=int)
    query = AuditEvent.query.filter_by(firm_id=current_user.firm_id)
    if selected_action:
        query = query.filter(AuditEvent.action == selected_action)
    if selected_actor:
        query = query.filter(AuditEvent.actor_name.ilike(f'%{selected_actor}%'))
    if selected_client_id:
        query = query.filter(AuditEvent.client_id == selected_client_id)

    page = request.args.get('page', 1, type=int)
    per_page = 50
    events = query\
        .order_by(AuditEvent.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)

    action_options = [value for (value,) in db.session.query(AuditEvent.action)
                      .filter_by(firm_id=current_user.firm_id)
                      .distinct().order_by(AuditEvent.action).all()]
    clients = Client.query.filter_by(firm_id=current_user.firm_id).order_by(Client.name).all()
    return render_template('audit_history.html', events=events, clients=clients,
                           action_options=action_options,
                           selected_action=selected_action,
                           selected_actor=selected_actor,
                           selected_client_id=selected_client_id)

@main_bp.route('/clients/<int:client_id>/audit')
@login_required
def client_audit(client_id):
    client = get_client_or_404(client_id)
    doc_ids = [d.id for d in client.documents]
    conditions = [db.and_(AuditEvent.entity_type == 'client', AuditEvent.entity_id == client.id)]
    if doc_ids:
        conditions.append(db.and_(AuditEvent.entity_type == 'document', AuditEvent.entity_id.in_(doc_ids)))
    events = AuditEvent.query.filter(
        AuditEvent.firm_id == current_user.firm_id,
        or_(*conditions)
    ).order_by(AuditEvent.created_at.desc()).limit(200).all()

    return render_template('client_audit.html', client=client, events=events)
