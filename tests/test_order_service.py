"""Тесты бизнес-логики заявок: рендер, рассылка, обновление карточек."""
from app.db.models import Order, OrderStatus, UserRole
from app.db.repositories import order_repo, region_repo, user_repo
from app.services import order_service

CLIENT_PHONE = "79991234567"


def test_render_card_hides_phone():
    order = Order(
        amo_lead_id=12345, client_name="Иванов Иван", client_phone=CLIENT_PHONE,
        client_address="ул. Ленина, 10", comment="Замер окон",
        region_id=1, operator_tg_id=1, status=OrderStatus.SENT,
    )
    card = order_service.render_card(order, manager_name="Сергей", note="заметка")
    assert CLIENT_PHONE not in card
    assert "9991234567" not in card
    assert "Иванов Иван" in card
    assert "ул. Ленина, 10" in card


async def test_broadcast_and_race_take(session, fake_bot):
    region = await region_repo.create(session, "Брод-Регион")
    await user_repo.create(session, tg_id=801, role=UserRole.MANAGER, full_name="M1", region_id=region.id)
    await user_repo.create(session, tg_id=802, role=UserRole.MANAGER, full_name="M2", region_id=region.id)
    order = await order_repo.create(
        session, amo_lead_id=900, client_name="Клиент", client_phone=CLIENT_PHONE,
        region_id=region.id, operator_tg_id=999, status=OrderStatus.SENT,
    )

    managers = await user_repo.list_active_managers_by_region(session, region.id)
    delivered = await order_service.broadcast_to_managers(fake_bot, session, order, managers)

    assert delivered == 2
    assert len(fake_bot.sent) == 2
    # Телефон не утёк ни в одно сообщение
    for msg in fake_bot.sent:
        assert CLIENT_PHONE not in msg["text"]
    # Сообщения сохранены в БД
    stored = await order_repo.list_messages(session, order.id)
    assert len(stored) == 2

    # Первый менеджер берёт заявку
    taken = await order_repo.try_take(session, order.id, 801)
    assert taken is not None
    await order_service.refresh_cards(fake_bot, session, taken, manager_name="M1")

    # Второму менеджеру в карточке — «взял другой»
    edited_for_802 = [e for e in fake_bot.edited if e["chat_id"] == 802]
    assert edited_for_802
    assert any("другой" in e["text"] for e in edited_for_802)

    # Повторное взятие невозможно
    assert await order_repo.try_take(session, order.id, 802) is None


async def test_send_to_single_manager(session, fake_bot):
    region = await region_repo.create(session, "Личка-Регион")
    m = await user_repo.create(session, tg_id=811, role=UserRole.MANAGER, full_name="Один", region_id=region.id)
    order = await order_repo.create(
        session, amo_lead_id=901, client_name="К", client_phone=CLIENT_PHONE,
        region_id=region.id, operator_tg_id=999, status=OrderStatus.SENT,
    )
    ok = await order_service.send_to_manager(fake_bot, session, order, m)
    assert ok is True
    assert fake_bot.sent[0]["chat_id"] == 811
    assert order.tg_message_id is not None
