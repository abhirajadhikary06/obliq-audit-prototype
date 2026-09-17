from app import db
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import enum

class Role(enum.Enum):
    STAFF = "staff"
    REVIEWER = "reviewer"
    ADMIN = "admin"

class DocumentStatus(enum.Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    CORRECTION_REQUIRED = "correction_required"

class Firm(db.Model):
    __tablename__ = 'firms'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship('User', backref='firm', lazy=True)
    clients = db.relationship('Client', backref='firm', lazy=True)

    def __repr__(self):
        return f'<Firm {self.name}>'

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum(Role, values_callable=lambda obj: [e.value for e in obj]),
                     nullable=False, default=Role.STAFF)
    firm_id = db.Column(db.Integer, db.ForeignKey('firms.id'), nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploaded_documents = db.relationship('Document', backref='uploaded_by_user', lazy=True,
                                         foreign_keys='Document.uploaded_by')

    __table_args__ = (
        db.Index('ix_users_firm_role', 'firm_id', 'role'),
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_reviewer(self):
        return self.role in (Role.REVIEWER, Role.ADMIN)

    def is_staff(self):
        return self.role in (Role.STAFF, Role.ADMIN)

    def __repr__(self):
        return f'<User {self.email}>'

class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    firm_id = db.Column(db.Integer, db.ForeignKey('firms.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    documents = db.relationship('Document', backref='client', lazy=True, cascade='all, delete-orphan')

    __table_args__ = (
        db.Index('ix_clients_firm_name', 'firm_id', 'name'),
    )

    def __repr__(self):
        return f'<Client {self.name}>'

class Document(db.Model):
    __tablename__ = 'documents'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    # Object storage key (MinIO / S3) instead of local filename
    object_key = db.Column(db.String(500))
    original_filename = db.Column(db.String(300))
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    status = db.Column(db.Enum(DocumentStatus, values_callable=lambda obj: [e.value for e in obj]),
                       default=DocumentStatus.PENDING, nullable=False, index=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    uploaded_at = db.Column(db.DateTime)
    review_comment = db.Column(db.Text)
    version = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Critical multi-tenant + status filter
        db.Index('ix_documents_client_status', 'client_id', 'status'),
        db.Index('ix_documents_status_uploaded', 'status', 'uploaded_at'),
    )

    def __repr__(self):
        return f'<Document {self.name} v{self.version}>'

class AuditEvent(db.Model):
    """Immutable audit trail. No update/delete endpoints exposed."""
    __tablename__ = 'audit_events'
    id = db.Column(db.Integer, primary_key=True)
    firm_id = db.Column(db.Integer, db.ForeignKey('firms.id'), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    actor_name = db.Column(db.String(100), nullable=False)  # denormalized for immutability
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), index=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), index=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    entity_type = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.Integer)
    entity_name = db.Column(db.String(200))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    actor = db.relationship('User', foreign_keys=[actor_id])
    firm = db.relationship('Firm')
    client = db.relationship('Client', foreign_keys=[client_id])
    document = db.relationship('Document', foreign_keys=[document_id])

    __table_args__ = (
        # Primary query patterns for audit history
        db.Index('ix_audit_firm_created', 'firm_id', 'created_at'),
        db.Index('ix_audit_firm_entity', 'firm_id', 'entity_type', 'entity_id'),
        db.Index('ix_audit_client_created', 'client_id', 'created_at'),
        db.Index('ix_audit_document_created', 'document_id', 'created_at'),
    )

    def __repr__(self):
        return f'<AuditEvent {self.action} by {self.actor_name}>'
