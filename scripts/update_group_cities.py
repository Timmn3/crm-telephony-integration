"""Одноразовый скрипт: добавляет «г.» перед городами в таблице groups."""
import asyncio

from sqlalchemy import select, update

from app.db.database import async_session_factory, init_db
from app.db.models import Group

CITY_FIXES = {
    "Москва": "г. Москва",
    "Казань": "г. Казань",
    "Тверь": "г. Тверь",
    "Самара": "г. Самара",
    "Пермь": "г. Пермь",
    "Тула": "г. Тула",
    "Рязань": "г. Рязань",
}


async def main() -> None:
    await init_db()
    async with async_session_factory() as session:
        result = await session.execute(select(Group))
        groups = result.scalars().all()
        updated = 0
        for group in groups:
            new_city = CITY_FIXES.get(group.city)
            if new_city:
                group.city = new_city
                updated += 1
                print(f"  #{group.id} {group.name}: «{group.city}» → «{new_city}»")
        await session.commit()
    print(f"\nОбновлено групп: {updated}")


if __name__ == "__main__":
    asyncio.run(main())
