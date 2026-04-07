"""Template rendering helpers.

Keeping the Jinja environment and common context injection in one place makes
route modules smaller and easier to reason about.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import TEMPLATE_DIR
from .constants import MODULES
from .services.auth import can, get_current_user
from .utils.formatting import euro, fmt_date, today_str

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def build_permission_map(user_id: int) -> dict[str, bool]:
    permissions: dict[str, bool] = {}
    for module_key in MODULES:
        for action in [
            'view', 'create', 'edit', 'approve', 'send',
            'manage_payments', 'see_prices', 'edit_prices',
        ]:
            permissions[f'{module_key}_{action}'] = can(user_id, module_key, action)
    return permissions


def render(request: Request, template_name: str, **context):
    user = get_current_user(request)
    context.update(
        {
            'request': request,
            'user': user,
            'permissions': build_permission_map(user['id']),
            'euro': euro,
            'fmt_date': fmt_date,
            'today': today_str(),
        }
    )
    return templates.TemplateResponse(request, template_name, context)
