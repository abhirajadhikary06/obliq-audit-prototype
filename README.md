# OBLIQ-in Mini Audit Document Review System (Scalable Edition)

Focused CA-firm audit document workflow with **production-oriented scalability foundations**.

## Quick Start

```bash
docker compose up --build
# Application:  http://localhost:5000
# MinIO Console: http://localhost:9001  (minioadmin / dev-minio-password by default)
# Signed document URLs: http://localhost:9000 in local Docker development
```

Scale the app and workers further if needed:

```bash
docker compose up --build --scale web=4 --scale worker=3
```

### Demo Accounts (password: `password123`)

| Firm       | Email             | Role     |
|------------|-------------------|----------|
| ABC & Co.  | staff@abc.com     | Staff    |
| ABC & Co.  | reviewer@abc.com  | Reviewer |
| ABC & Co.  | admin@abc.com     | Admin    |
| XYZ & Co.  | staff@xyz.com     | Staff    |
| XYZ & Co.  | reviewer@xyz.com  | Reviewer |
| XYZ & Co.  | admin@xyz.com     | Admin    |

---

## Scalability Architecture

```
                    ┌──────────────┐
                    │    Nginx     │  ← Load balancer + rate limiting
                    │  (least_conn)│
                    └──────┬───────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
      ┌────────┐      ┌────────┐      ┌────────┐
      │ Web ×3 │      │ Web ×3 │      │ Web ×3 │   Gunicorn + gevent
      │ Flask  │      │ Flask  │      │ Flask  │
      └────┬───┘      └────┬───┘      └────┬───┘
           │               │               │
     ┌─────┴───────────────┴───────────────┴─────┐
     │                                           │
     ▼               ▼               ▼           ▼
┌─────────┐   ┌──────────┐   ┌────────────┐  ┌────────┐
│PgBouncer│   │  Redis   │   │   MinIO    │  │ Celery │
│ (pool)  │   │ sessions │   │  object    │  │Workers │
│         │   │ cache    │   │  storage   │  │  ×2    │
│         │   │ broker   │   │            │  │        │
└────┬────┘   └──────────┘   └────────────┘  └────────┘
     │
     ▼
┌─────────┐
│Postgres │
└─────────┘
```

### What was activated (previously “None”)

| Concern | Implementation |
|---------|----------------|
| **Multiple app replicas** | `web` service with `deploy.replicas: 3` (scale further via CLI) |
| **Load balancer** | Nginx with `least_conn` upstream + keepalive |
| **Shared sessions** | Redis-backed Flask-Session (works across all replicas) |
| **Object storage** | MinIO (S3-compatible). Files no longer on local disk |
| **Connection pooling** | PgBouncer (transaction mode) + SQLAlchemy pool (size=20, overflow=30, pre-ping) |
| **Background workers** | Celery + Redis for background work and cache invalidation; primary audit writes are transactional |
| **Caching** | Redis cache for dashboard stats (45 s TTL, invalidated on write) |
| **Database indexes** | Composite indexes on firm_id + status, firm_id + created_at, client_id + status, etc. |
| **Rate limiting** | Nginx zones + Flask-Limiter (Redis storage). Stricter on `/login` |
| **Worker class** | Gunicorn + **gevent** for high concurrency per process |

### Tenant Isolation (unchanged, still strict)

Every data access path filters by `current_user.firm_id`.  
A user from Firm A can never see Firm B data even if they guess IDs.

### Audit Trail

- Written synchronously in the same PostgreSQL transaction as each business change.
- Celery remains available for genuinely asynchronous work such as notifications and processing.
- Immutable (no update/delete routes). Actor name denormalized.

### Core Workflow (still the same)

1. Staff creates/selects client → standard docs auto-created  
2. Upload document → MinIO  
3. Reviewer starts review → Approve **or** Request Correction (comment required)  
4. Staff uploads revised version (version++)  
5. Full history visible per document, per client, and firm-wide  

---

## Local development (no Docker)

```bash
pip install -r requirements.txt
# Needs local Redis + MinIO or it falls back to SQLite + local behaviour
python run.py
```

For Docker development, Compose supplies clearly marked development fallbacks when
`.env` is absent. Before any production deployment, copy `.env.example` to `.env`
and replace every placeholder with a strong secret. Set `FLASK_ENV=production`;
the application refuses to start without `SECRET_KEY` in that mode. MinIO remains
private and document downloads are authorized by Flask before a short-lived URL is
issued.

When `DATABASE_URL` / `REDIS_URL` / MinIO env vars are absent the app degrades gracefully to SQLite + in-process behaviour so the prototype remains usable.

State-changing forms use CSRF protection. The document workflow is enforced on the
server: documents move from Pending to Uploaded to Under Review, then either
Approved or Correction Required. Correction requires a reason, and approved
documents cannot be replaced.

---

## Project Structure

```
obliq-audit/
├── app/
│   ├── __init__.py       # Factory: pooling, Redis session/cache, limiter
│   ├── models.py         # Indexed multi-tenant models
│   ├── main.py           # Routes + transactional audit + MinIO uploads
│   ├── auth.py
│   ├── storage.py        # MinIO helper
│   ├── celery_app.py
│   ├── seed.py
│   └── templates/
├── nginx/nginx.conf      # Load balancer + rate zones
├── db/init.sql
├── docker-compose.yml    # Full scalable stack
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## What would you improve if you had one more week?

1. **Observability** — Prometheus metrics (request latency, Celery queue depth, DB pool usage) + Grafana dashboards and structured JSON logging.
2. **True blue/green or canary deploys** — Add a second web deployment slot and traffic shifting via Nginx or Traefik.
3. **Document virus scanning** — ClamAV sidecar that scans objects on upload before they become downloadable.
4. **Stronger audit integrity** — Hash-chain the audit events so history is tamper-evident even against a compromised DB admin.
5. **Per-firm rate / quota controls** — Soft limits on storage and concurrent reviews so one noisy firm cannot starve others.
6. **Notification channel** — Email/Slack when a document enters “Correction Required” or is approved (still the biggest real-world friction).

These are the highest-leverage next steps for a multi-firm pilot; none of them expand into tax calculation or government portals.

---

Evaluation prototype for OBLIQ-in. Not production-hardened (no TLS termination inside the compose file, default credentials, etc.).
