from app import db
from app.models import Firm, User, Client, Document, DocumentStatus, Role
from sqlalchemy import text

def seed_data():
    if db.engine.dialect.name == "postgresql":
        db.session.execute(text("SELECT pg_advisory_xact_lock(734921)"))

    if Firm.query.first():
        return  # already seeded

    # Firm A
    firm_a = Firm(name="ABC & Co.")
    db.session.add(firm_a)
    db.session.flush()

    # Firm B
    firm_b = Firm(name="XYZ & Co.")
    db.session.add(firm_b)
    db.session.flush()

    # Users for Firm A
    admin_a = User(email="admin@abc.com", name="Aman (Admin)", role=Role.ADMIN, firm_id=firm_a.id)
    admin_a.set_password("password123")
    reviewer_a = User(email="reviewer@abc.com", name="Aman Reviewer", role=Role.REVIEWER, firm_id=firm_a.id)
    reviewer_a.set_password("password123")
    staff_a = User(email="staff@abc.com", name="Rohit Staff", role=Role.STAFF, firm_id=firm_a.id)
    staff_a.set_password("password123")
    db.session.add_all([admin_a, reviewer_a, staff_a])

    # Users for Firm B
    admin_b = User(email="admin@xyz.com", name="Priya (Admin)", role=Role.ADMIN, firm_id=firm_b.id)
    admin_b.set_password("password123")
    reviewer_b = User(email="reviewer@xyz.com", name="Suresh Reviewer", role=Role.REVIEWER, firm_id=firm_b.id)
    reviewer_b.set_password("password123")
    staff_b = User(email="staff@xyz.com", name="Neha Staff", role=Role.STAFF, firm_id=firm_b.id)
    staff_b.set_password("password123")
    db.session.add_all([admin_b, reviewer_b, staff_b])

    db.session.flush()

    # One seeded client per firm, matching the firm names used by the demo.
    client_a = Client(name="ABC & Co.", firm_id=firm_a.id, created_by=staff_a.id)
    client_b = Client(name="XYZ & Co.", firm_id=firm_b.id, created_by=staff_b.id)
    db.session.add_all([client_a, client_b])
    db.session.flush()

    document_names = [
        "Bank Statement", "Sales Register", "Purchase Register",
        "GST Return", "Expense Summary",
    ]
    for client in (client_a, client_b):
        db.session.add_all([
            Document(name=name, client_id=client.id, status=DocumentStatus.PENDING)
            for name in document_names
        ])

    db.session.commit()
    print("Seed data created successfully.")
