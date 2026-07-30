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


async def test_set_mango_extension(session):
    group = await group_repo.create(session, "Группа-Ext1", "Город-Ext1")
    admin = await user_repo.create(
        session, tg_id=701, role=UserRole.ADMIN, full_name="Дима", group_id=group.id
    )
    await user_repo.set_mango_extension(session, admin, "502")
    assert admin.mango_extension == "502"

    await user_repo.set_mango_extension(session, admin, None)
    assert admin.mango_extension is None


async def test_get_group_mango_extension_variants(session):
    group = await group_repo.create(session, "Группа-Ext2", "Город-Ext2")

    # Пустая группа — консенсуса нет.
    assert await user_repo.get_group_mango_extension(session, group.id) is None

    a1 = await user_repo.create(
        session, tg_id=711, role=UserRole.ADMIN, full_name="Егор", group_id=group.id
    )
    a2 = await user_repo.create(
        session, tg_id=712, role=UserRole.ADMIN, full_name="Женя", group_id=group.id
    )

    # Ни у кого ещё нет extension.
    assert await user_repo.get_group_mango_extension(session, group.id) is None

    await user_repo.set_mango_extension(session, a1, "502")
    assert await user_repo.get_group_mango_extension(session, group.id) == "502"

    # У второго то же значение — по-прежнему единый консенсус.
    await user_repo.set_mango_extension(session, a2, "502")
    assert await user_repo.get_group_mango_extension(session, group.id) == "502"

    # exclude_user_id исключает собственное (устаревшее) значение при смене группы.
    assert await user_repo.get_group_mango_extension(
        session, group.id, exclude_user_id=a1.id
    ) == "502"  # a2 всё ещё держит 502
    assert await user_repo.get_group_mango_extension(
        session, group.id, exclude_user_id=a2.id
    ) == "502"  # a1 всё ещё держит 502

    # Расхождение — детерминированно наименьшее по sorted().
    await user_repo.set_mango_extension(session, a2, "999")
    assert await user_repo.get_group_mango_extension(session, group.id) == "502"


async def test_list_admins_without_extension(session):
    group = await group_repo.create(session, "Группа-Ext3", "Город-Ext3")

    with_ext = await user_repo.create(
        session, tg_id=721, role=UserRole.ADMIN, full_name="Игорь", group_id=group.id
    )
    await user_repo.set_mango_extension(session, with_ext, "502")

    without_ext = await user_repo.create(
        session, tg_id=722, role=UserRole.ADMIN, full_name="Клим", group_id=group.id
    )

    # Неактивный без extension — не в выборке.
    inactive = await user_repo.create(
        session, tg_id=723, role=UserRole.ADMIN, full_name="Лёша",
        group_id=group.id, is_active=False,
    )
    await user_repo.set_mango_extension(session, inactive, None)

    # Без группы — не в выборке (звонить некому, group_id обязателен для карточки).
    await user_repo.create(
        session, tg_id=724, role=UserRole.ADMIN, full_name="Миша", group_id=None
    )

    # MANAGER без extension — не ADMIN, не в выборке.
    await user_repo.create(
        session, tg_id=725, role=UserRole.MANAGER, full_name="Настя", group_id=group.id
    )

    result = await user_repo.list_admins_without_extension(session)
    tg_ids = {u.tg_id for u in result}
    assert without_ext.tg_id in tg_ids
    assert with_ext.tg_id not in tg_ids
    assert inactive.tg_id not in tg_ids
    assert 724 not in tg_ids
    assert 725 not in tg_ids


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
