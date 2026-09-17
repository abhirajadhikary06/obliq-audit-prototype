"""Object storage abstraction (MinIO / S3 compatible)."""
import os
import uuid
from minio import Minio
from minio.error import S3Error
from io import BytesIO
from werkzeug.utils import secure_filename

_client = None

def get_minio_client():
    global _client
    if _client is None:
        endpoint = os.getenv('MINIO_ENDPOINT', 'localhost:9000')
        access = os.getenv('MINIO_ACCESS_KEY', 'minioadmin')
        secret = os.getenv('MINIO_SECRET_KEY', 'minioadmin')
        secure = os.getenv('MINIO_SECURE', 'false').lower() == 'true'
        _client = Minio(endpoint, access_key=access, secret_key=secret, secure=secure)
    return _client

def ensure_bucket():
    client = get_minio_client()
    bucket = os.getenv('MINIO_BUCKET', 'audit-docs')
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except S3Error:
        pass  # best-effort; createbuckets service also handles this
    return bucket

def upload_file(file_storage, firm_id: int, client_id: int, doc_id: int) -> tuple[str, str]:
    """
    Upload a Werkzeug FileStorage to MinIO.
    Returns (object_key, original_filename)
    """
    bucket = ensure_bucket()
    original = file_storage.filename or 'unnamed'
    # Path layout: firm/{firm_id}/client/{client_id}/doc/{doc_id}/{uuid}_{filename}
    # Enables future per-tenant lifecycle policies
    safe_original = secure_filename(original)
    ext = safe_original.rsplit('.', 1)[-1].lower() if '.' in safe_original else 'bin'
    object_key = f"firm/{firm_id}/client/{client_id}/doc/{doc_id}/{uuid.uuid4().hex}.{ext}"

    # Read into memory (prototype; for very large files use streaming)
    data = file_storage.read()
    length = len(data)
    file_storage.seek(0)

    client = get_minio_client()
    client.put_object(
        bucket,
        object_key,
        BytesIO(data),
        length=length,
        content_type=file_storage.content_type or 'application/octet-stream'
    )
    return object_key, original

def get_presigned_url(object_key: str, expires_seconds: int = 3600) -> str:
    """Generate a temporary download URL."""
    from datetime import timedelta
    bucket = os.getenv('MINIO_BUCKET', 'audit-docs')
    public_endpoint = os.getenv('MINIO_PUBLIC_ENDPOINT')
    if public_endpoint:
        internal_client = get_minio_client()
        region = internal_client._get_region(bucket)
        client = Minio(
            public_endpoint,
            access_key=os.getenv('MINIO_ACCESS_KEY', 'minioadmin'),
            secret_key=os.getenv('MINIO_SECRET_KEY', 'minioadmin'),
            secure=os.getenv('MINIO_PUBLIC_SECURE', 'false').lower() == 'true',
        )
        client._region_map[bucket] = region
    else:
        client = get_minio_client()
    return client.presigned_get_object(bucket, object_key, expires=timedelta(seconds=expires_seconds))

def download_bytes(object_key: str) -> bytes:
    client = get_minio_client()
    bucket = os.getenv('MINIO_BUCKET', 'audit-docs')
    response = client.get_object(bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
