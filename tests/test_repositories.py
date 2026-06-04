"""Интеграционные тесты репозиториев (реальный Postgres)."""
from datetime import datetime, timedelta, timezone

from app.db.models import OrderStatus, UserRole
from app.db.repositories import amo_token_repo, group_repo, order_repo, user_repo


async def test_group_crud(session):
    g = await group_repo.create(session, "Г-1", "Город-1")
    assert (await group_repo.get_by_id(session, g.id)).name == "Г-1"
    assert (await group_repo.get_by_name(session, "Г-1")).id == g.id
    assert await group_repo.count(session) >= 1


async def test_user_crud(session):
    group = await group_repo.create(session, "Группа-А", "Город-А")
    m = await user_repo.create(
        session, tg_id=501, role=UserRole.MANAGER, full_name="Менеджер",
        tg_username="man1", group_id=group.id,
    )
    assert (await user_repo.get_by_tg_id(session, 501)).id == m.id
    assert (await user_repo.get_by_username(session, "@MAN1")).id == m.id
    await user_repo.set_phone(session, m, "79990000000")
    assert m.phone == "79990000000"


async def test_one_manager_per_group_and_replace(session):
    group = await group_repo.create(session, "Группа-Б", "Город-Б")
    first = await user_repo.create(
        session, tg_id=601, role=UserRole.MANAGER, full_name="Первый", group_id=group.id
    )
    assert (await user_repo.get_active_manager_by_group(session, group.id)).tg_id == 601

    # Замена: отвязываем первого (group_id=NULL), назначаем второго.
    unassigned = await user_repo.unassign_from_group(session, group.id)
    assert unassigned.tg_id == first.tg_id
    assert first.group_id is None

    await user_repo.create(
        session, tg_id=602, role=UserRole.MANAGER, full_name="Второй", group_id=group.id
    )
    assert (await user_repo.get_active_manager_by_group(session, group.id)).tg_id == 602


async def test_order_create_sent(session):
    group = await group_repo.create(session, "Группа-В", "Город-В")
    order = await order_repo.create(
        session, amo_lead_id=777, client_name="Клиент", client_phone="79991112233",
        group_id=group.id, operator_tg_id=999, manager_tg_id=601,
    )
    assert order.status == OrderStatus.SENT
    assert order.manager_tg_id == 601
    assert order.id is not None

    await order_repo.set_status(session, order, OrderStatus.CALL_REQUESTED,
                                set_call_requested_at=True)
    assert order.status == OrderStatus.CALL_REQUESTED
    assert order.call_requested_at is not None

    active = await order_repo.list_active_by_manager(session, 601)
    assert any(o.id == order.id for o in active)


async def test_amo_token_upsert(session):
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    await amo_token_repo.save(session, access_token="a1", refresh_token="r1", expires_at=exp)
    t = await amo_token_repo.get(session)
    assert t.access_token == "a1" and t.refresh_token == "r1"

    exp2 = datetime.now(timezone.utc) + timedelta(hours=2)
    await amo_token_repo.save(session, access_token="a2", refresh_token="r2", expires_at=exp2)
    t2 = await amo_token_repo.get(session)
    assert t2.id == t.id  # та же единственная запись
    assert t2.access_token == "a2"
