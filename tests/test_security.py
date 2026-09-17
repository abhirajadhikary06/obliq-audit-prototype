import re

import pytest

from app import app as flask_app, db
from app.models import Client, Document, Firm


@pytest.fixture
def client():
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
        RATELIMIT_ENABLED=False,
    )
    with flask_app.test_client() as test_client:
        yield test_client


def csrf_token(client):
    response = client.get('/login')
    if response.status_code != 200:
        response = client.get('/dashboard')
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def login(client, email, password='password123'):
    token = csrf_token(client)
    return client.post('/login', data={
        'email': email,
        'password': password,
        'csrf_token': token,
    }, follow_redirects=False)


def test_protected_dashboard_requires_authentication(client):
    response = client.get('/dashboard')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_invalid_credentials_are_rejected(client):
    response = login(client, 'staff@abc.com', 'wrong-password')
    assert response.status_code == 200
    assert b'Invalid email or password' in response.data


def test_state_change_without_csrf_is_rejected(client):
    response = client.post('/login', data={
        'email': 'staff@abc.com',
        'password': 'password123',
    })
    assert response.status_code == 400


def test_staff_cannot_start_review(client):
    with flask_app.app_context():
        document = Document.query.filter_by(status='pending').first()
        document_id = document.id

    assert login(client, 'staff@abc.com').status_code == 302
    token = csrf_token(client)
    response = client.post(f'/documents/{document_id}/start_review', data={'csrf_token': token})
    assert response.status_code == 302
    assert b'Only reviewers' not in response.data

    with flask_app.app_context():
        assert Document.query.get(document_id).status.value == 'pending'


def test_staff_cannot_create_client(client):
    assert login(client, 'staff@abc.com').status_code == 302
    assert client.get('/clients/new').status_code == 403


def test_firm_resources_are_isolated(client):
    with flask_app.app_context():
        abc_firm = Firm.query.filter_by(name='ABC & Co.').first()
        abc_client = Client.query.filter_by(name='ABC & Co.', firm_id=abc_firm.id).first()
        abc_document = Document.query.filter_by(client_id=abc_client.id).first()
        client_id, document_id = abc_client.id, abc_document.id

    assert login(client, 'staff@xyz.com').status_code == 302
    assert client.get(f'/clients/{client_id}').status_code == 404
    assert client.get(f'/documents/{document_id}').status_code == 404
    assert client.get(f'/documents/{document_id}/download').status_code == 404