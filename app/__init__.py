from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_session import Session
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, CSRFError
from sqlalchemy import inspect, text
from dotenv import load_dotenv
import os
import redis
from datetime import timezone
from zoneinfo import ZoneInfo

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
sess = Session()
cache = Cache()
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per minute"])
csrf = CSRFProtect()


def format_ist_datetime(value, fmt='%d %b %Y, %I:%M %p'):
    """Format UTC database timestamps as India Standard Time for the UI."""
    if value is None:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo('Asia/Kolkata')).strftime(fmt)

def create_app():
    app = Flask(__name__)
    app.jinja_env.filters['ist_datetime'] = format_ist_datetime
    flask_env = os.getenv('FLASK_ENV', 'development')
    secret_key = os.getenv('SECRET_KEY')
    if flask_env == 'production' and secret_key in (None, '', 'dev-only-change-me', 'dev-only-secret-key-obliq'):
        raise RuntimeError('SECRET_KEY must be configured in production.')
    app.config['SECRET_KEY'] = secret_key or 'dev-only-secret-key-obliq'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_NAME'] = 'obliq_session'
    app.config['WTF_CSRF_TIME_LIMIT'] = 3600

    # ---------- Database (with connection pooling) ----------
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(basedir, "audit.db")}'

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 20,          # connections kept open
        'max_overflow': 30,       # extra connections under burst
        'pool_timeout': 30,
        'pool_recycle': 1800,     # recycle before PgBouncer / server timeout
        'pool_pre_ping': True,    # detect stale connections
    }

    # ---------- Redis-backed sessions (shared across replicas) ----------
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_REDIS'] = redis.from_url(redis_url)
    app.config['SESSION_PERMANENT'] = False
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_KEY_PREFIX'] = 'obliq:sess:'
    app.config['PERMANENT_SESSION_LIFETIME'] = 3600 * 8  # 8 hours

    # ---------- Caching ----------
    app.config['CACHE_TYPE'] = 'RedisCache'
    app.config['CACHE_REDIS_URL'] = redis_url
    app.config['CACHE_DEFAULT_TIMEOUT'] = 60
    app.config['CACHE_KEY_PREFIX'] = 'obliq:cache:'

    # ---------- Rate limiting (Redis storage) ----------
    app.config['RATELIMIT_STORAGE_URI'] = os.getenv('RATELIMIT_STORAGE_URI', redis_url)
    app.config['RATELIMIT_STRATEGY'] = 'fixed-window'

    # ---------- Uploads / object storage ----------
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    # Init extensions
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'warning'
    sess.init_app(app)
    cache.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)

    from app.models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.auth import auth_bp
    from app.main import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    # Health endpoint for load balancer / k8s probes
    @app.route('/health')
    def health():
        return {'status': 'ok'}, 200

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return render_template(
            'error.html',
            code=400,
            title='Security check failed',
            message='This form session has expired. Reload the page and try again.',
        ), 400

    @app.errorhandler(403)
    def handle_forbidden(error):
        return render_template('error.html', code=403, title='Access denied',
                               message='You do not have permission to perform this action.'), 403

    @app.errorhandler(404)
    def handle_not_found(error):
        return render_template('error.html', code=404, title='Not found',
                               message='That resource is not available.'), 404

    with app.app_context():
        db.create_all()
        _ensure_audit_columns()
        from app.seed import seed_data
        seed_data()

    return app


def _ensure_audit_columns():
    """Apply the small additive audit schema change for existing prototype volumes."""
    columns = {column['name'] for column in inspect(db.engine).get_columns('audit_events')}
    additions = {
        'client_id': 'INTEGER REFERENCES clients(id)',
        'document_id': 'INTEGER REFERENCES documents(id)',
    }
    missing = {name: definition for name, definition in additions.items() if name not in columns}
    if not missing:
        return

    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(734922)'))
        columns = {column['name'] for column in inspect(db.engine).get_columns('audit_events')}
        missing = {name: definition for name, definition in additions.items() if name not in columns}
    for name, definition in missing.items():
        db.session.execute(text(f'ALTER TABLE audit_events ADD COLUMN {name} {definition}'))
    db.session.commit()

# Module-level app for gunicorn / celery
app = create_app()
