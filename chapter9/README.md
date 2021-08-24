## 메시지 버스를 타고 시내로 나가기

### 새로운 아키텍처가 필요한 새로운 요구사항

- 배치 수량 변경으로 주문라인을 다시 할당할 필요가 있을 수 있다.
    - 재고 조사하는 동안 지붕이 물이 새서 `SPRINGY-MATTRESS` 3개가 손상된 것을 발견했다.
    - `RELIABLE-FORK` 배송에서 필요한 문서가 빠져서 몇 주 동안 세관에 머물러야 했다. 이후 `RELIABLE-FORK` 3개가 안전 검사에 실패해 폐기됐다.
- 이벤트 기반으로 상상해자.
    - `BatchQuantityChanged` 이벤트가 발생하면 배치 수량을 변경, 수량이 부족하면 배치를 `deallocate`하고 `AllocationRequired` 이벤트를 발생할 수 있다.

#### 모든 것이 이벤트 핸들러

- 현재 두 개의 흐름이 있다.
    1. 서비스 계층 함수에 의해 처리되는 API 호출
    2. 내부 이벤트와 그 이벤트에 대한 핸들러
- 1번에서 API 호출으로 이벤트를 발생시키고 서비스 계층 함수를 핸들러로 생각한다면 2번과 모양이 같아진다.
    - `services.allocate()` 는 `AllocationRequired` 이벤트의 핸들러일 수 있다.
    - `services.add_batch()` 는 `BatchCreated` 이벤트의 핸들러일 수 있다.

### 서비스 함수를 메시지 핸들러로 만들기

- 먼저 이벤트를 만들어준다.

```python
# src/allocation/domain/events.py
@dataclass
class BatchCreated(Event):
    ref: str
    sku: str
    qty: int
    eta: Optional[date] = None

@dataclass
class AllocationRequired(Event):
    orderid: str
    sku: str
    qty: int

```

- 파일명 services.py 를 handlers.py 로 변경하고 이벤트를 사용하도록 변경한다.

```python
def add_batch(
        event: events.BatchCreated,
        uow: unit_of_work.AbstractUnitOfWork,
):
    with uow:
        product = uow.products.get(sku=event.sku)
        if product is None:
            product = model.Product(event.sku, batches=[])
            uow.products.add(product)
        product.batches.append(model.Batch(event.ref, event.sku, event.qty, event.eta))
        uow.commit()

def allocate(
        event: events.AllocationRequired,
        uow: unit_of_work.AbstractUnitOfWork,
) -> str:
    line = OrderLine(event.orderid, event.sku, event.qty)
    with uow:
        product = uow.products.get(sku=line.sku)
        if product is None:
            raise InvalidSku(f"Invalid sku {line.sku}")
        batchref = product.allocate(line)
        uow.commit()
    return batchref

def send_out_of_stock_notification(event: events.OutOfStock):
    email.send_mail('stock@made.com', f'Out of stock for {event.sku}')
```

> Note. Part 1에서 서비스 계층과 도메인 모델 결합을 끊기 위해 원시 타입을 이용했는데 이벤트 사용하는 건 괜찮은가? 도메인 변경은 여전히 가능하며
> 이벤트 변경은 도메인 변경보다는 훨씬 적을 것으로 예상된다. 그리고 함수 시그니쳐가 복잡했었는데 이벤트 안으로 모두 들어가서 인지가 쉬워졌다.

### 메시지 버스로 이벤트 처리하기

- `UoW` 에서 메시지 버스로 이벤트를 처리하도록 구현했었다.
- 이는 `UoW` 에서 생성된 이벤트 없이 웹 계층에서 이벤트를 전달하는 경우 사용할 수 없다.
- 메시지 버스에서 `UoW` 로부터 이벤트를 가져오도록 변경할 필요가 있다.

```python
# src/allocation/service_layer/messagebus.py
def handle(event: events.Event, uow: unit_of_work.AbstractUnitOfWork):
    queue = [event]
    while queue:
        event = queue.pop(0)
        for handler in HANDLERS[type(event)]:
            handler(event, uow)
            queue.extend(uow.collect_new_events())
```

- UoW 에서는 새로운 이벤트에 대해 반환하고 messagebus 는 events 를 직접 가져와 처리한다.

```python
# src/allocation/service_layer/unit_of_work.py
class AbstractUnitOfWork:
    ...  
    def collect_new_events(self):
        for product in self.products.seen:
            while product.events:
                yield product.events.pop(0)
```

- 테스트 케이스도 변경 해야한다.

```python
# tests/unit/test_handlers.py
class TestAddBatch:
    def test_for_new_product(self):
        uow = FakeUnitOfWork()
        messagebus.handle(
            events.BatchCreated("b1", "CRUNCHY-ARMCHAIR", 100, None), uow
        )
        assert uow.products.get("CRUNCHY-ARMCHAIR") is not None
        assert uow.committed
```

### 웹 계층에서 메시지 버스 사용하기

- 웹 계층에서 응답을 반환해주려면 결과도 버스를 통해 받을 수 있어야한다. 

```python
# src/allocation/service_layer/messagebus.py
def handle(event: events.Event, uow: unit_of_work.AbstractUnitOfWork):
    results = []
    queue = [event]
    while queue:
        event = queue.pop(0)
        for handler in HANDLERS[type(event)]:
            results.append(handler(event, uow))
            queue.extend(uow.collect_new_events())
    return results
```

- 웹에서 메시지 버스를 사용도록 변경한다.

```python
# src/allocation/entrypoints/flask_app.py
@app.route("/allocate", methods=["POST"])
def allocate_endpoint():
    try:
        event = events.AllocationRequired(
            request.json["orderid"], request.json["sku"], request.json["qty"]
        )
        results = messagebus.handle(event, unit_of_work.SqlAlchemyUnitOfWork())
        batchref = results.pop(0)
    except InvalidSku as e:
        return {"message": str(e)}, 400

    return {"batchref": batchref}, 201
```

### 요구사항 구현하기

- 위 과정들로 리팩토링은 모두 끝났다. 이제 배치 수량 변경에 대한 요구사항을 이벤트로 처리할 수 있다.

```python
# src/allocation/domain/events.pys
@dataclass
class BatchQuantityChanged(Event):
    ref: str
    qty: int
```

- `BatchQuantityChanged`에 대한 핸들러도 만들어준다.

```python
# src/allocation/service_layer/handlers.py
def change_batch_quantity(event: events.BatchQuantityChanged, uow: unit_of_work.AbstractUnitOfWork):
    with uow:
        product = uow.products.get_by_batchref(batchref=event.ref)
        product.change_batch_quantity(ref=event.ref, qty=event.qty)
        uow.commit()
```

- 수량이 변경되는 것에 대한 비지니스 로직을 도메인 모델에 추가한다.

```python
# src/allocation/domain/model.py
class Product:
    ...
    def change_batch_quantity(self, ref: str, qty: int):
        batch = next(b for b in self.batches if b.reference == ref)
        batch._purchased_quantity = qty
        while batch.available_quantity < 0:
            line = batch.deallocate_one()
            self.events.append(
                events.AllocationRequired(line.orderid, line.sku, line.qty)
            )
```

- 새로운 요구사항을 새로운 아키텍처에 맞게 구현했다.

### 정리

- 이벤트 기반 아키텍쳐는 복잡한 요구 사항 대부분을 처리할 수 있다.
- 아키텍쳐 측면에서는 아무 내용을 추가해도 복잡도가 늘어나지 않는다.
- 다만 메시지 버스를 보면 언제 끝날지 예측할 수 없다.
- 이벤트 필드가 중복이 있을 수 있으며 필드 추가서 여러 이벤트 변경이 필요할 수 있다.
