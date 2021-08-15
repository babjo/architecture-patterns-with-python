## 작업 단위 패턴

### 서비스 계층과 데이터 계층 완전한 분리
- `AbstractRepository`로 저장소는 추상화를 했다.
- 하지만 서비스 계층에서 `session.commit()`을 직접 호출하고 있다.
  - `session.commit()`는 변경된 사항을 데이터베이스에 반영하겠다는 의미다.
- 서비스 계층이 데이터베이스와 직접 대화하고 있다.
- 이 사이에 추상화 계층을 넣어서 남은 결합을 완전히 분리시키자.
- UoW (Unit of Work) 라는 추상화를 넣자.

### 통합 테스트로 UoW 작성하기
- 실제 `SQLAlchemy`와 동작을 확인할 예정이니 통합테스트를 작성한다.

```python
# tests/integration/test_uow.py
def test_uow_can_retrieve_a_batch_and_allocate_to_it(session_factory):
    session = session_factory()
    insert_batch(session, 'batch1', 'HIPSTER-WORKBENCH', 100, None)
    session.commit()

    uow = unit_of_work.SqlAlchemyUnitOfWork(session_factory)
    with uow:
        batch = uow.batches.get(reference='batch1')
        line = model.OrderLine('o1', 'HIPSTER-WORKBENCH', 10)
        batch.allocate(line)
        uow.commit()

    batchref = get_allocated_batch_ref(session, 'o1', 'HIPSTER-WORKBENCH')
    assert batchref == 'batch1'
```

- 특정 scope 내 코드 변경을 데이터베이스에 반영하는 형태니 컨텍스트 관리자로 구현을 예상한다.

### SQLAlchemy 세션을 이용하는 실제 작업 단위
- 먼저 추상화 클래스를 만든다.

```python
# src/allocation/service_layer/unit_of_work.py
class AbstractUnitOfWork(abc.ABC):
    batches: repository.AbstractRepository

    def __enter__(self) -> AbstractUnitOfWork:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.rollback()

    @abc.abstractmethod
    def commit(self):
        raise NotImplemented

    @abc.abstractmethod
    def rollback(self):
        raise NotImplemented
```

- `AbstractUnitOfWork`를 상속받아 실제 동작하는 `SqlAlchemyUnitOfWork`를 작성한다.

```python
# src/allocation/service_layer/unit_of_work.py
class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    def __init__(self, session_factory=DEFAULT_SESSION_FACTORY):
        self.session_factory = session_factory

    def __enter__(self):
        self.session = self.session_factory()
        self.batches = repository.SqlAlchemyRepository(self.session)
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        super().__exit__(exc_type, exc_val, exc_tb)
        self.session.close()

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()
```

### 테스트를 위한 가짜 작업 단위
- 추상화의 보상으로 가짜 작업 단위를 만들 수 있다.
- 데이터 계층 접근은 `UoW`로 통하면 된다. 

```python
# tests/unit/test_services.py
class FakeUnitOfWork(unit_of_work.AbstractUnitOfWork):
    def __init__(self):
        self.batches = FakeRepository([])
        self.committed = False

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

def test_returns_allocation():
    uow = FakeUnitOfWork()
    services.add_batch("b1", "COMPLICATED-LAMP", 100, None, uow)

    result = services.allocate("o1", "COMPLICATED-LAMP", 10, uow)
    assert result == "b1"

def test_add_batch():
    uow = FakeUnitOfWork()
    services.add_batch("b1", "CRUNCHY-ARMCHAIR", 100, None, uow)
    assert uow.batches.get("b1") is not None
    assert uow.committed
```

### UoW 를 서비스 계층에서 사용하기
- 테스트 작성을 했으니 실제 구현을 해준다. 

```python
# src/allocation/service_layer/services.py
def add_batch(
    ref: str, sku: str, qty: int, eta: Optional[date],
    uow: unit_of_work.AbstractUnitOfWork,
):
    with uow:
        uow.batches.add(model.Batch(ref, sku, qty, eta))
        uow.commit()

def allocate(
    orderid: str, sku: str, qty: int,
    uow: unit_of_work.AbstractUnitOfWork,
) -> str:
    line = OrderLine(orderid, sku, qty)
    with uow:
        batches = uow.batches.list()
        if not is_valid_sku(line.sku, batches):
            raise InvalidSku(f"Invalid sku {line.sku}")
        batchref = model.allocate(line, batches)
        uow.commit()
    return batchref
```

- 추가적으로 롤백이 잘되는지 통합테스트 추가해볼 수 있겠다.

```python
# tests/integration/test_uow.py
def test_rolls_back_on_error(session_factory):
    class MyException(Exception):
        pass

    uow = unit_of_work.SqlAlchemyUnitOfWork(session_factory)
    with pytest.raises(MyException):
        with uow:
            insert_batch(uow.session, "batch1", "LARGE-FORK", 100, None)
            raise MyException()

    new_session = session_factory()
    rows = list(new_session.execute('SELECT * FROM "batches"'))
    assert rows == []
```

#### 명시적 커밋과 암시적 커밋
- 예외가 발생하지 않은 경우 자동으로 커밋해주면 어떨까?

```python
# src/allocation/service_layer/unit_of_work.py
class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    ...
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
    ...
```

- 서비스 계층에서 명시적 `uow.commit()` 호출을 없앨 수 있다.

### 정리
- 작업 단위 패턴으로 원자적으로 발생하기 원하는 코드를 블록으로 묶을 수 있게 됐다.
- 작업 단위 패턴은 연산 끝에 한꺼번에 플러시(flash)해서 도메인 모델 일관성을 강화한다.
- 작업 단위 패턴로 데이터 계층 접근에 대한 추상화를 완성시킬 수 있다.
- 작업 단위 패턴 구현으로 파이썬 컨텍스트 관리자는 좋은 선택이다.
