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


async def test_multiple_admins_per_group(session):
    group = await group_repo.create(session, "Группа-Б", "Город-Б")
    await user_repo.create(
        session, tg_id=601, role=UserRole.ADMIN, full_name="Анна", group_id=group.id
    )
    await user_repo.create(
        session, tg_id=602, role=UserRole.ADMIN, full_name="Борис", group_id=group.id
    )
    # Неактивный админ той же группы в выборку не попадает.
    await user_repo.create(
        session, tg_id=603, role=UserRole.ADMIN, full_name="Виктор",
        group_id=group.id, is_active=False,
    )
    # Менеджер (создатель заявок) к группе не относится.
    await user_repo.create(
        session, tg_id=604, role=UserRole.MANAGER, full_name="Гена", group_id=group.id
    )

    admins = await user_repo.list_active_admins_by_group(session, group.id)
    assert [a.tg_id for a in admins] == [601, 602]  # по алфавиту full_name


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


async def test_get_active_by_amo_lead_id(session):
    group = await group_repo.create(session, "Группа-Г", "Город-Г")
    order = await order_repo.create(
        session, amo_lead_id=778, client_name="Клиент2", client_phone="79991112244",
        group_id=group.id, operator_tg_id=999, manager_tg_id=601,
    )

    found = await order_repo.get_active_by_amo_lead_id(session, 778)
    assert found is not None and found.id == order.id

    # Закрытая заявка по этому лиду больше не «активная» — дубль не заблокирует.
    await order_repo.set_status(session, order, OrderStatus.COMPLETED)
    assert await order_repo.get_active_by_amo_lead_id(session, 778) is None

    assert await order_repo.get_active_by_amo_lead_id(session, 999999) is None


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
