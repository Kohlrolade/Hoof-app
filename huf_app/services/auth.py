"""Authentication and authorization helpers.

The application still uses the session-based owner auto-login from the original
project, but the logic now lives in one dedicated module.
"""

from __future__ import annotations

import sqlite3

from fastapi import HTTPException, Request

from ..constants import MODULES
from ..db.core import get_conn, qone

def get_current_user(request: Request) -> sqlite3.Row:
    user_id = request.session.get('user_id')
    with get_conn() as conn:
        user = None
        if user_id:
            user = qone(conn, 'SELECT * FROM users WHERE id = ? AND is_active = 1', (user_id,))
        if not user:
            user = qone(conn, "SELECT * FROM users WHERE role_key = 'owner' ORDER BY id LIMIT 1")
            if user:
                request.session['user_id'] = user['id']
        if not user:
            raise HTTPException(status_code=500, detail='Kein Benutzer vorhanden.')
        return user


def can(user_id: int, module_key: str, action: str) -> bool:
    if module_key not in MODULES:
        return False
    fields = {
        'view': 'can_view',
        'create': 'can_create',
        'edit': 'can_edit',
        'cancel': 'can_cancel',
        'approve': 'can_approve',
        'send': 'can_send',
        'manage_payments': 'can_manage_payments',
        'see_prices': 'can_see_prices',
        'edit_prices': 'can_edit_prices',
    }
    field = fields.get(action)
    if not field:
        return False
    with get_conn() as conn:
        row = qone(conn, f'SELECT {field} FROM permissions WHERE user_id = ? AND module_key = ?', (user_id, module_key))
        return bool(row[field]) if row else False


def require_permission(user_id: int, module_key: str, action: str) -> None:
    if not can(user_id, module_key, action):
        raise HTTPException(status_code=403, detail='Dafür fehlt die Berechtigung.')
