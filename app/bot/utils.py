from __future__ import annotations
import html
from app.db.models import Group


def format_group_lines(group: Group) -> str:
    lines = [f'Группа: #{group.id} "{html.escape(group.name)}"']
    if group.city:
        lines.append(f"Город: г. {html.escape(group.city)}")
    return "\n".join(lines)
