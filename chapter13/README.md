## 의존성 주입(그리고 부트스트래핑)

- 서비스 계층에서 UoW를 인자로 받도록 만들어서 FakeUoW를 사용하는 등 테스트로 이점을 봤다.
- 그런데 실제 실행환경에서는 진입점에서 UoW를 초기화하고 넘겨주는 과정이 필요하게 된다.
- UoW뿐만 아니라 의존성을 주입해줘야할 것이 점점 많아 질 수 있다.
- 의존성 주입만 도와주는 도구가 필요하다.

### 암시적 의존성과 명시적 의존성

- 보통 python 에서는 모듈을 import 해서 의존성을 암시적으로 표시한다.
  - 테스트는 `monkey patch`로 한다.

```python
from allocation.adapters import email, redis_eventpublisher
...
```

- 직접 파라미터로 넘김으로 명시적으로 의존성을 표시할 수도 있다.

```python
def send_out_of_stock_notification(event: events.OutOfStock, uow: unit_of_work.AbstractUnitWork):
    ...
```

- 암시적 의존성 표시에 `monkey patch` 테스트 방식은 해롭기에 명시적 의존성을 선호한다.
  - 그 밖에 명시적인 것이 암시적인 것보다 좋기에 명시적 의존성을 사용한다.

### 핸들러 준비: 클로저와 부분함수를 사용한 수동 DI

- 명시적 의존성을 사용하기로 했으니 부트스트래퍼(수동으로 의존성을 주입해주는 스크립트)를 만들어서 의존성 주입을 해보자.
  - 플라스크/레디스 진입 -(호출)-> 부트스트래퍼 -(의존성이 주입된 핸들러 전달)-> 메시지 버스 -> ...
- `lambda`를 이용해 의존성을 가지는 클로저를 만들 수 있다.

```python
# uow 로 의존성을 주입한다.
allocate_composed = lambda cmd: allocate(cmd, uow)
```

- `functools.partial`로 의존성을 가지는 부분함수를 만들 수 있다.

```python
import functools

# uow 로 의존성을 주입한다.
allocated_composed = functools.partial(allocate, uow=uow)
```

### 클래스를 사용한 대안

- 함수형 프로그래밍을 한 사람은 클로저와 부분함수가 익숙할 것이다.
- 객체지향을 다룬 사람은 클래스로 의존성을 관리하는 것이 익숙할 것이다.

```python
class AllocateHandler:
    def __init__(self, uow: unit_of_work.AbstractUnitOfWork):
        self.uow = uow
    def __call__(self, cmd: commands.Allocate):
        line = OrderLine(cmd.orderid, cmd.sku, cmd.qty)
        with self.uow:
            ...
```

- 함수형 방법이든 객체지향 방법이든 팀에서 편하게 느끼는 방법을 사용한다.

### 부트스트랩 스크립트

```python
# src/allocation/bootstrap.py
def bootstrap(
    start_orm: bool = True,
    uow: unit_of_work.AbstractUnitOfWork = unit_of_work.SqlAlchemyUnitOfWork(),
    notifications: AbstractNotifications = None,
    publish: Callable = redis_eventpublisher.publish,
) -> messagebus.MessageBus:

    if notifications is None:
        notifications = EmailNotifications()

    if start_orm:
        orm.start_mappers()

    dependencies = {"uow": uow, "notifications": notifications, "publish": publish}
    
    # 이벤트 핸들러에 의존성을 주입한다.
    injected_event_handlers = {
        event_type: [
            inject_dependencies(handler, dependencies)
            for handler in event_handlers
        ]
        for event_type, event_handlers in handlers.EVENT_HANDLERS.items()
    }
    
    # 커맨드 핸들러에 의존성을 주입한다.
    injected_command_handlers = {
        command_type: inject_dependencies(handler, dependencies)
        for command_type, handler in handlers.COMMAND_HANDLERS.items()
    }

    return messagebus.MessageBus(
        uow=uow,
        event_handlers=injected_event_handlers,
        command_handlers=injected_command_handlers,
    )

# 클로저로 의존성을 주입한다.
def inject_dependencies(handler, dependencies):
    params = inspect.signature(handler).parameters
    deps = {
        name: dependency
        for name, dependency in dependencies.items()
        if name in params
    }
    return lambda message: handler(message, **deps)
```

### 실행 도중 핸들러가 제공된 메시지 버스

- 메시지 버스에서 의존성이 주입된 핸들러를 사용하기 위해 메시지 버스를 클래스로 만든다.

```python
# src/allocation/service_layer/messagebus.py
class MessageBus:
    def __init__(
        self,
        uow: unit_of_work.AbstractUnitOfWork,
        event_handlers: Dict[Type[events.Event], List[Callable]],
        command_handlers: Dict[Type[commands.Command], Callable],
    ):
        self.uow = uow
        self.event_handlers = event_handlers
        self.command_handlers = command_handlers
```

### 진입점에서 부트스트랩 사용하기

```python
# src/allocation/entrypoints/flask_app.py 
app = Flask(__name__)

# 핸들러에 의존성 주입하고 메시지 버스를 반환한다.
bus = bootstrap.bootstrap()

@app.route("/add_batch", methods=["POST"])
def add_batch():
    eta = request.json["eta"]
    if eta is not None:
        eta = datetime.fromisoformat(eta).date()
    cmd = commands.CreateBatch(
        request.json["ref"], request.json["sku"], request.json["qty"], eta
    )
    
    # 의존성이 주입된 핸들러로 처리된다.
    bus.handle(cmd)
    return "OK", 201
```

### 테스트에서 DI 초기화하기

- DI 는 테스트에서도 유용하다.

```python
# tests/integration/test_views.py
@pytest.fixture
def sqlite_bus(sqlite_session_factory):
    # 통합 테스트니 실제 DB 를 사용할 수 있도록 한다.
    bus = bootstrap.bootstrap(
        start_orm=True,
        uow=unit_of_work.SqlAlchemyUnitOfWork(sqlite_session_factory),
        send_mail=lambda *args: None, 
        publish=lambda *args: None,
    )
    yield bus
    clear_mappers()
```

```python
# tests/unit/test_handlers.py
@pytest.fixture
def bootstrap_test_app:
    # 단위 테스트니 `FakeUnitOfWork`을 사용도록 한다.
    bus = bootstrap.bootstrap(
        start_orm=False,
        uow=FakeUnitOfWork(),
        send_mail=lambda *args: None, 
        publish=lambda *args: None,
    )
    yield bus
    clear_mappers()
```

### 마치며

- 부트스크랩 스크립트(의존성 주입)로 고통스러운 의존성 전달은 해결된다.
- 실행시 한번만 실행되는 코드는 부트스트랩 스크립트에 넣어도 무방하다.
- 연쇄적으로 의존성 주입이 필요한 경우 의존성 프레임워크를 사용하는 것이 좋다.
