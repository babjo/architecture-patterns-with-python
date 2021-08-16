## 이벤트와 메시지 버스

### 지저분해짐
- 앞선 예제에서 재고가 없으면 구매팀에 이메일로 통지를 하고 싶다.
- 관련 로직을 어디에 넣으면 좋을까?

#### 웹 컨트롤러
```python
# src/allocation/entrypoints/flask_app.py
@app.route("/allocate", methods=["POST"])
def allocate_endpoint():
    try:
        batchref = services.allocate(
            request.json["orderid"],
            request.json["sku"],
            request.json["qty"],
            unit_of_work.SqlAlchemyUnitOfWork(),
        )
    except (model.OutOfStock, services.InvalidSku) as e:
        send_mail(
            'out of stock',
            'stock_admin@made.com',
            f'{line.orderid} - {line.sku}'
        )
        return {"message": str(e)}, 400

    return {"batchref": batchref}, 201
```
- 이메일을 보내는 일은 HTTP 계층이 할 일은 아니다.
- 또 메일 전송에 대한 테스트가 어렵다.

#### 도메인 모델
```python
# src/allocation/domain/model.py
    def allocate(self, line: OrderLine) -> str:
        try:
            batch = next(b for b in sorted(self.batches) if b.can_allocate(line))
            batch.allocate(line)
            self.version_number += 1
            return batch.reference
        except StopIteration:
            email.send_mail('stock@made.com', f'Out of stock for {line.sku}')
            raise OutOfStock(f"Out of stock for sku {line.sku}")
```
- 위 웹 컨트롤러 예시보다 더 안좋다.
- 도메인 모델이 인프라 구조에 의존해서는 안된다.
- 메일이 아니고 SMS로도 통지할 수 있는 구조를 가져야한다.

#### 서비스 계층
```python
# src/allocation/service_layer/services.py
def allocate(
        orderid: str, sku: str, qty: int,
        uow: unit_of_work.AbstractUnitOfWork,
) -> str:
    line = OrderLine(orderid, sku, qty)
    with uow:
        product = uow.products.get(sku=line.sku)
        if product is None:
            raise InvalidSku(f"Invalid sku {line.sku}")
        try:
            batchref = product.allocate(line)
            uow.commit()
            return batchref
        except model.OutOfStock:
            email.send_mail('stock@made.com', f'Out of stock for {line.sku}')
            raise
```
- 예외를 잡아서 다시 예외를 던진다? 어색하다.

### 단일 책임 원칙
- 웹 컨트롤러, 도메인 모델, 서비스 계층에 넣는 것은 단일 책임 원칙을 위배하기에 어색해보인다.
  - 이메일을 SMS로 변경하는데 `allocate()` 함수를 변경하면 `allocate()`가 상품할당 위에 다른 일을 책임진다는 뜻이다.
- 이를 해결하기 위해 다른 추상화가 필요하다.

### 도메인 이벤트와 메시지 버스
- 메시지 버스에 이벤트를 보내고 이를 구독해 통지할 수 있도록 하자.

#### 이벤트
- 이벤트는 도메인 모델에 있었던 사건을 의미한다.
- 이벤트 이름은 도메인 언어에서 가져와야한다.

```python
# src/allocation/domain/events.py
class Event:
    pass

@dataclass
class OutOfStock(Event):
    sku: str
```

- 할당이 불가능하면 이벤트가 발생해야한다.

```python
# src/tests/unit/test_product.py
def test_records_out_of_stock_event_if_cannot_allocate():
    batch = Batch('batch1', 'SMALL-FORK', 10, eta=None)
    product = Product(sku='SMALL-FORK', batches=[batch])
    product.allocate(OrderLine('order1', 'SMALL-FORK', 10))

    allocation = product.allocate(OrderLine('order2', 'SMALL-FORK', 1))

    assert product.events[-1] == events.OutOfStock(sku='SMALL-FORK')
    assert allocation is None
```

#### 이벤트 핸들러
- 이벤트를 처리할 핸들러를 만든다.

```python
# src/allocation/service_layer/messagebus.py
def handle(event: events.Event):
    for handler in HANDLERS[type(event)]:
        handler(event)

def send_out_of_stock_notification(event: events.Event):
    email.send_mail('stock@made.com', f'Out of stock for {event.sku}')

HANDLERS = {
    events.OutOfStock: [send_out_of_stock_notification],
}
```

#### 이벤트 발행
- 이벤트, 이벤트 핸들러까지 만들었으니 이제 이벤트 발행이 필요하다.

#### 서비스 계층에서 생성된 이벤트 발행

```python
# src/allocation/service_layer/services.py
def allocate(
        orderid: str, sku: str, qty: int,
        uow: unit_of_work.AbstractUnitOfWork,
) -> str:
    line = OrderLine(orderid, sku, qty)
    with uow:
        product = uow.products.get(sku=line.sku)
        if product is None:
            raise InvalidSku(f"Invalid sku {line.sku}")
        
        try:
            batchref = product.allocate(line)
            uow.commit()
            return batchref
        finally:
            messagebus.handle(product.events)
```
- Exception 발생시 도메인 모델이 기록중인 events 를 발행한다. 

#### 서비스 계층에서 이벤트 생성 후 발행

```python
# src/allocation/service_layer/services.py
def allocate(
        orderid: str, sku: str, qty: int,
        uow: unit_of_work.AbstractUnitOfWork,
) -> str:
    line = OrderLine(orderid, sku, qty)
    with uow:
        product = uow.products.get(sku=line.sku)
        if product is None:
            raise InvalidSku(f"Invalid sku {line.sku}")

        batchref = product.allocate(line)
        uow.commit()
        
        if batchref is None:
            messagebus.handle(events.OutOfStock(line.sku))    
    return batchref
```

- 서비스 계층에서 직접 이벤트를 생성하고 발행한다.
- 서비스 계층에서 이벤트를 발행하는 패턴을 구현한 시스템이 많다.
  - 다만 저자가 생각하기에 더 나은 방법은 아래 소개한다.

#### UoW 에서 발행
- UoW 에는 이미 try/catch 가 있고 도메인 모델에 대해 이미 알고 있으니 이벤트 발행에는 좋은 곳이다.
- `commit()` 할 때 repo 에서 추적한 애그리게이트에서 생성된 Event 를 발행한다.

```python
# src/allocation/service_layer/unit_of_work.py
class AbstractUnitOfWork(abc.ABC):
    def commit(self):
        self._commit()
        self.publish_events()

    @abc.abstractmethod
    def _commit(self):
        raise NotImplemented

    def publish_events(self):
        for product in self.products.seen:
            while product.events:
                event = product.events.pop(0)
                messagebus.handle(event)
```

- 애그리게이트 추적을 위해 `AbstractRepository`에 변경 또한 필요하다.

```python
# src/allocation/adapters/repository.py
class AbstractRepository(abc.ABC):

    def __init__(self):
        self.seen = set()

    def add(self, product: model.Product):
        self._add(product)
        self.seen.add(product)

    @abc.abstractmethod
    def _add(self, product: model.Product):
        raise NotImplementedError

    def get(self, sku) -> model.Product:
        product = self._get(sku)
        if product:
            self.seen.add(product)
        return product

    @abc.abstractmethod
    def _get(self, sku) -> model.Product:
        raise NotImplementedError
```

- 서비스 계층에서 이벤트 발행하지 않고도 `UoW`로 이벤트가 발행된다. (서비스 계층 깨끗)

### 정리
- `X일 때는 Y를 합시다`와 같은 말은 종종 시스템에 구체적으로 만들 수 있는 이벤트를 의미한다.
- 장점
  - 핵심 애플리케이션 로직과 부가적인 행동에 대해 완전한 분리가 가능하다.
  - 이벤트를 도메인 언어에서 따와 코드 내 도메인 언어가 풍부해진다.
- 단점
  - 이벤트 처리가 동기적으로 이뤄져 성능에 이상을 줄 수 있다.
