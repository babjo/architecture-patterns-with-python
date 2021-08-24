## 커맨드와 커맨드 핸들러

- 9장에서 `BatchCreated` 이벤트가 발생했을 때 핸들러를 통해 배치를 만들었다.
- API를 통해 배치 생성 요청이 들어온 경우 `BatchCreated` 이벤트를 만들어서 처리하는게 조금 어색하다.
- API 요청은 내부 이벤트 생성을 통한 처리보다는 커맨드와 커맨드 핸들러를 통한 처리가 어울린다.

### 커맨드와 이벤트

- 커맨드는 시스템이 어떤 일을 수행하길 바라는 의도를 담고 있다.
- 이벤트는 그저 관심있는 모든 리스너에게 보내는 메시지일 뿐이다.

|-|이벤트|커맨드|
|------|---|---|
|이름|과거형|명령형|
|오류 처리|(송신하는 쪽과) 독립적으로 실패함|(송신하는 쪽에 오류를 돌려주면서) 시끄럽게 실패함|
|받는 행위자|모든 리스너|정해진 수신자|

- 아래와 같이 커맨드를 정의할 수 있다.

```python
class Command:
    pass

@dataclass
class Allocate(Command):
    orderid: str
    sku: str
    qty: int

@dataclass
class CreateBatch(Command):
    ref: str
    sku: str
    qty: int
    eta: Optional[date] = None

@dataclass
class ChangeBatchQuantity(Command):
    ref: str
    qty: int
```

### 커맨드 핸들러 구현

- 위에서 커맨드를 만들었으니 이를 처리할 수 있도록 커맨드 핸들러가 필요하다.

```python
# src/allocation/service_layer/handlers.py
COMMAND_HANDLERS = {
    commands.Allocate: handlers.allocate,
    commands.CreateBatch: handlers.add_batch,
    commands.ChangeBatchQuantity: handlers.change_batch_quantity,
}  # type: Dict[Type[commands.Command], Callable]
```

- 그리고 메시지 버스에서 메시지가 커맨드면 커맨드 핸들러로 처리하도록 하자.

```python
# src/allocation/service_layer/messagebus.py
def handle(
        message: Message,
        uow: unit_of_work.AbstractUnitOfWork,
):
    results = []
    queue = [message]
    while queue:
        message = queue.pop(0)
        if isinstance(message, events.Event):
            handle_event(message, queue, uow)
        elif isinstance(message, commands.Command):
            cmd_result = handle_command(message, queue, uow)
            results.append(cmd_result)
        else:
            raise Exception(f"{message} was not an Event or Command")
    return results

def handle_event(
        event: events.Event,
        queue: List[Message],
        uow: unit_of_work.AbstractUnitOfWork,
):
    for handler in EVENT_HANDLERS[type(event)]:
        try:
            logger.debug("handling event %s with handler %s", event, handler)
            handler(event, uow=uow)
            queue.extend(uow.collect_new_events())
        except Exception:
            logger.exception("Exception handling event %s", event)
            continue

def handle_command(
        command: commands.Command,
        queue: List[Message],
        uow: unit_of_work.AbstractUnitOfWork,
):
    logger.debug("handling command %s", command)
    try:
        handler = COMMAND_HANDLERS[type(command)]
        result = handler(command, uow=uow)
        queue.extend(uow.collect_new_events())
        return result
    except Exception:
        logger.exception("Exception handling command %s", command)
        raise
```

### 예외 처리 방식의 차이점

- 위 구현을 보면 이벤트 처리시 에러는 무시, 커맨드 처리시 에러는 무시하지 않는다.
- 이벤트 처리에서 발생한 에러는 무시해도 되는걸까?
- 한 커맨드는 한 애그리게이트 변경하며 일관성 유지를 위해 전체 성공하거나 전체 실패해야한다. (실패를 무시할 수 없다.)
- 이벤트는 애그리게이트 변경 이후 발생한다.
- 이벤트 처리는 커맨드 성공 여부와는 아무런 관계가 없으며 실패를 무시하는 것이 사용성에 더 좋을 수 있다.
  - 배치 할당 후 이메일 전송 실패로 배치 할당을 rollback 할 필요는 없다.
- 이벤트 처리에 대한 실패를 격리함으로 시스템 신뢰도가 높아질 수 있다.

### 동기적으로 오류 복구하기

- 이벤트 실패는 back-off 와 함께 retry 해서 회복시킬 수 있다.
- 파이썬에서는 `tenacity` 라이브러리를 보통 사용한다.

### 정리
- 커맨드를 도입하면서 의도가 명시적으로 드러났다. (`BatchCreated` -> `CreateBatch`)
- 커맨드와 이벤트를 다른 방식으로 처리하면서 어느 상황에서 꼭 성공해야하는지 분명해졌다.
- 이벤트 처리는 실패를 삼킨다. 실패감지 할 수 있도록 모니터링을 더 잘해야한다.
  - 실패 범위를 줄이기 위해 실패를 작게 만드는 것도 중요해진다.
