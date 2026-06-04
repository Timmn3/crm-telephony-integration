"""Тесты бизнес-логики заявок: рендер, отправка, обновление карточки."""
from app.db.models import Order, OrderStatus
from app.db.repositories import group_repo, order_repo, user_repo
from app.db.models import UserRole
from app.services import order_service

CLIENT_PHONE = "79991234567"


def test_render_card_hides_phone():
    order = Order(
        amo_lead_id=12345, client_name="Иванов Иван", client_phone=CLIENT_PHONE,
        client_address="ул. Ленина, 10", comment="Замер окон",
        group_id=1, operator_tg_id=1, status=OrderStatus.SENT,
    )
    card = order_service.render_card(order, note="заметка")
    assert CLIENT_PHONE not in card
    assert "9991234567" not in card
    assert "Иванов Иван" in card
    assert "ул. Ленина, 10" in card
    assert "заметка" in card


async def test_send_to_manager_stores_message(session, fake_bot):
    group = await group_repo.create(session, "Личка", "Город")
    await user_repo.create(
        session, tg_id=811, role=UserRole.MANAGER, full_name="Один", group_id=group.id
    )
    order = await order_repo.create(
        session, amo_lead_id=901, client_name="К", client_phone=CLIENT_PHONE,
        group_id=group.id, operator_tg_id=999, manager_tg_id=811,
    )
    ok = await order_service.send_to_manager(fake_bot, session, order)
    assert ok is True
    assert fake_bot.sent[0]["chat_id"] == 811
    # Телефон не утёк в сообщение
    assert CLIENT_PHONE not in fake_bot.sent[0]["text"]
    # Координаты сообщения сохранены на заявке
    assert order.tg_chat_id == 811
    assert order.tg_message_id is not None


async def test_refresh_card_edits_message(session, fake_bot):
    group = await group_repo.create(session, "Реф", "Город")
    await user_repo.create(
        session, tg_id=821, role=UserRole.MANAGER, full_name="М", group_id=group.id
    )
    order = await order_repo.create(
        session, amo_lead_id=902, client_name="К2", client_phone=CLIENT_PHONE,
        group_id=group.id, operator_tg_id=999, manager_tg_id=821,
    )
    await order_service.send_to_manager(fake_bot, session, order)
    await order_repo.set_status(session, order, OrderStatus.CALL_APPROVED,
                                set_call_approved_at=True)
    await order_service.refresh_card(fake_bot, order)

    assert fake_bot.edited
    last = fake_bot.edited[-1]
    assert last["chat_id"] == 821
    assert CLIENT_PHONE not in last["text"]
    assert "одобрен" in last["text"].lower()
