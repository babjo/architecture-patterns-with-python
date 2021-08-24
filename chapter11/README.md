## 이벤트 기반 아키텍처: 이벤트를 사용한 마이크로서비스 통합

- 지금까지 할당에 대한 마이크로서비스 하나 만들었다.
- 다른 시스템과 이야기하는 방법은 없을까?
- 창고 시스템에 재고가 감소하고 배치를 재할당해야하는 경우 어떻게 처리할 수 있을까?

### 분산된 진흙 공, 명사로 생각하기

- 주문 시스템, 배치 시스템, 창고 시스템이 있다고 가정한다.
- 구현해야할 사용자 시나리오는 이러하다.
  1. 유저가 장바구니에 상품을 넣고 재고를 예약한다.
  2. 운영자는 재고 예약을 보고 주문하고 상품을 출고한다.
  3. 3번째 주문인 경우 일단 고객을 VIP로 올린다.

```uml
@startuml
actor 고객
actor 구매팀
entity 주문
entity 배치
entity 창고
database CRM

== 예약 ==

고객 -> 주문: 장바구니에 상품 추가
주문 -> 배치: 재고 예약

== 구매 ==

구매팀 -> 주문: 주문 넣기
activate 주문
주문 -> 배치: 예약 확인
배치 -> 창고: 상품 출고
주문 -> CRM: 고객 레코드 변경
deactivate 주문
@enduml
```

- 만약 각 시스템을 데이터베이스 테이블 단위로 CURD 하는 API로 만들었다면 주문 시스템에서 다른 시스템 API를 호출하며 요구사항을 구현했을 것이다.
- 이는 잘 동작할순지만 금방 `큰 진흙공`이 될 수 있다.
- 재고 손상으로 창고내 재고가 줄어든 경우 기존 배치는 제거하고 주문에 새로운 배치를 재할당해야한다.

```uml
@startuml
actor "창고 담당자"
entity 창고
entity 배치
entity 주문
database CRM

"창고 담당자" -> 창고: 재고 손상 보고
activate 창고
창고 -> 배치: 사용 가능 재고 감소
배치 -> 배치: 주문 재할당
배치 -> 주문: 주문 상태 업데이트
주문 -> CRM: 주문 이력 업데이트
deactivate 창고
@enduml
```

- 재할당을 할 떄 주문 서비스가 배치 시스템을 제어해야하고 배치 시스템은 다시 창고 시스템을 제어해야한다.
- 의존성 그래프가 지저분해진다.

### 분산 시스템에서 오류 처리하기

- 이렇게 API를 연쇄적으로 호출할 때 하나가 실패할 경우 전체가 실패하게 된다.
  - 이는 서로 결합된 상태며 다른 시스템과 사용이 많아질수록 실패할 확률이 높아진다.

### 대안: 비동기 메시징을 사용한 시간적 결합

- 이렇게 API를 연쇄적으로 호출이 아닌 메시지를 통한 비동기 처리로 시스템을 통합할 수 있다.
- 이 경우 시스템 간 결합 강도를 낮추고 실패 영향 범위를 줄인다.
  - 후추 실패에 대해 대응하기가 쉽다.

### 메시지 발행/구독 통한 시스템 통합하기

- 시스템 간 메시지는 레디스를 통해 발행하거나 구독할 수 있다.
- 레디스와 함께 재고 감소로 배치를 재할당하는 시나리오를 구현해본다.
- 창고 시스템에서 재고 감소로 `배치 수량 변경` 커맨드가 발행됐다고 가정한다.
- `배치 수량 변경` 커맨드 구독하고 처리할 수 있도록 소비자를 만든다.

```python
# src/allocation/entrypoints/redis_eventconsumer.py
r = redis.Redis(**config.get_redis_host_and_port())

def main():
    orm.start_mappers()
    pubsub = r.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe("change_batch_quantity")

    for m in pubsub.listen():
        handle_change_batch_quantity(m)

def handle_change_batch_quantity(m):
    logging.debug("handling %s", m)
    data = json.loads(m["data"])
    cmd = commands.ChangeBatchQuantity(ref=data["batchref"], qty=data["qty"])
    messagebus.handle(cmd, uow=unit_of_work.SqlAlchemyUnitOfWork())

if __name__ == "__main__":
    main()
```

- 발행은 다음과 같다.

```python
# src/allocation/adapters/redis_eventpublisher.py 
r = redis.Redis(**config.get_redis_host_and_port())

def publish(channel, event: events.Event):
    logging.debug("publishing: channel=%s, event=%s", channel, event)
    r.publish(channel, json.dumps(asdict(event)))
```

- Product 애그리거트를 통해 배치 수량을 변경하면서 재할당이 필요한 경우 `Allocate` 커맨드를 메시지 버스로 발행한다. 

```python
# src/allocation/domain/model.py
class Product:
    def change_batch_quantity(self, ref: str, qty: int):
        batch = next(b for b in self.batches if b.reference == ref)
        batch._purchased_quantity = qty
        while batch.available_quantity < 0:
            line = batch.deallocate_one()
            self.events.append(commands.Allocate(line.orderid, line.sku, line.qty))
```

- 메시지 버스 내 `Allocate` 커맨드는 커맨드 핸들러로 처리된다.
- `Product.allocate()`가 수행되면서 `Allocated` 이벤트를 발행하여 외부에 알릴 수 있다.

```python
# src/allocation/domain/model.py
class Product:
    def allocate(self, line: OrderLine) -> str:
        try:
            batch = next(b for b in sorted(self.batches) if b.can_allocate(line))
            batch.allocate(line)
            self.version_number += 1
            self.events.append(
                events.Allocated(
                    orderid=line.orderid,
                    sku=line.sku,
                    qty=line.qty,
                    batchref=batch.reference,
                )
            )
            return batch.reference
```

- 이벤트 핸들러로 외부로 이벤트 발행한다.

```python
# src/allocation/service_layer/messagebus.py 
EVENT_HANDLERS = {
    events.Allocated: [handlers.publish_allocated_event],
}  # type: Dict[Type[events.Event], List[Callable]]

# src/allocation/service_layer/handlers.py
def publish_allocated_event(
    event: events.Allocated,
    uow: unit_of_work.AbstractUnitOfWork,
):
    redis_eventpublisher.publish("line_allocated", event)
```

### 마치며

- 이벤트를 통한 통합 방식은 상당한 유연성을 얻을 수 있으나 시스템 디버깅이나 변경이 어려운 한계점이 있다.
