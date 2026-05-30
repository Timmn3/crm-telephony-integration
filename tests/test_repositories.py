"""Интеграционные тесты репозиториев (реальный Postgres)."""
from app.db.models import OrderStatus, UserRole
from app.db.repositories import order_repo, region_repo, settings_repo, user_repo


async def test_user_crud(session):
    region = await region_repo.create(session, "Регион-1")
    m = await user_repo.create(
        session, tg_id=501, role=UserRole.MANAGER, full_name="Менеджер",
        tg_username="man1", region_id=region.id,
    )
    assert (await user_repo.get_by_tg_id(session, 501)).id == m.id
    assert (await user_repo.get_by_username(session, "@MAN1")).id == m.id
    await user_repo.set_phone(session, m, "79990000000")
    assert m.phone == "79990000000"


async def test_managers_by_region(session):
    region = await region_repo.create(session, "Регион-2")
    await user_repo.create(session, tg_id=601, role=UserRole.MANAGER, full_name="A", region_id=region.id)
    await user_repo.create(session, tg_id=602, role=UserRole.MANAGER, full_name="B", region_id=region.id)
    inactive = await user_repo.create(session, tg_id=603, role=UserRole.MANAGER, full_name="C", region_id=region.id)
    await user_repo.set_active(session, inactive, False)
    managers = await user_repo.list_active_managers_by_region(session, region.id)
    assert {m.tg_id for m in managers} == {601, 602}


async def test_try_take_race(session):
    region = await region_repo.create(session, "Регион-3")
    order = await order_repo.create(
        session, amo_lead_id=777, client_name="Клиент", client_phone="79991112233",
        region_id=region.id, operator_tg_id=999, status=OrderStatus.SENT,
    )
    first = await order_repo.try_take(session, order.id, 701)
    second = await order_repo.try_take(session, order.id, 702)
    assert first is not None
    assert first.manager_tg_id == 701
    assert second is None  # гонка: второй не может взять


async def test_settings_upsert(session):
    await settings_repo.set_value(session, "k", "v1")
    await settings_repo.set_value(session, "k", "v2")
    assert await settings_repo.get(session, "k") == "v2"
    await settings_repo.delete(session, "k")
    assert await settings_repo.get(session, "k") is None
