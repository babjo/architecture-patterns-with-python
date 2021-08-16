from ..domain import events
from ..adapters import email


def handle(event: events.Event):
    for handler in HANDLERS[type(event)]:
        handler(event)


def send_out_of_stock_notification(event: events.Event):
    email.send_mail('stock@made.com', f'Out of stock for {event.sku}')


HANDLERS = {
    events.OutOfStock: [send_out_of_stock_notification],
}
