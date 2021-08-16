from ..domain import events
from ..service_layer import handlers


def handle(event: events.Event):
    for handler in HANDLERS[type(event)]:
        handler(event)


HANDLERS = {
    events.BatchCreated: [handlers.add_batch],
    events.AllocationRequired: [handlers.allocate],
    events.OutOfStock: [handlers.send_out_of_stock_notification],
}  # type: Dict[Type[events.Event], List[Callable]]
