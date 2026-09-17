#!/usr/bin/env python3
"""Local development runner (uses SQLite if no DATABASE_URL)."""
from app import app

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
