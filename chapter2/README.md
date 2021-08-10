## 저장소 패턴
### 도메인 모델 영속화
- 기능 개발을 위해 도메인 모델을 데이터베이스든 어디든 영속화할 방법이 필요하다. 
```python
@flask.route.gubbins
def allocate_endpoint():
    # 요청으로부터 주문 라인 추출
    line = OrderLine(reuquest.params, ...)
    # DB에서 모든 배치 가져오기
    batches = ...
    # 도메인 서비스 호출
    allocate(line, batches)
    # 어떤 방식으로든 할당한 배치를 다시 데이터베이스에 저장
    return 201
```

### 일반적인 ORM
- 보통 많이 사용되는 패턴이다.
- 도메인 모델이 ORM 에 의존하는 치명적인 단점을 가진다.
```python
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class Order(Base):
    id = Column(Integer, primary_key=True)

class OrderLine(Base):
    id = Column(Integer, primary_key=True)
    sku = Column(String(250))
    qty = Integer(String(250))
    order_id = Column(Integer, ForeignKey('order.id'))
    order = relationship(Order)

class Allocation(Base):
    ...
```

### 순수한 도메인 유지하는 ORM
- 도메인 모델은 ORM 을 모르는 것이 좋다.
- 매퍼를 정의하면 의존성을 없앨 수 있다.
- 다만 도메인 모델에 변경이 생기면 매퍼 변경이 필요하다.
  - 새로운 결정을 할 때는 트레이드오프를 생각해야한다.
  - 종종 정석보다는 실용적인 이유로 선택을 포기하기도 한다.
```python
from sqlalchemy import Table, MetaData, Column, Integer, String, Date, ForeignKey
from sqlalchemy.orm import mapper, relationship

import model

metadata = MetaData()

order_lines = Table(
    "order_lines",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sku", String(255)),
    Column("qty", Integer, nullable=False),
    Column("orderid", String(255)),
)

batches = Table(
    "batches",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("reference", String(255)),
    Column("sku", String(255)),
    Column("_purchased_quantity", Integer, nullable=False),
    Column("eta", Date, nullable=True),
)

allocations = Table(
    "allocations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("orderline_id", ForeignKey("order_lines.id")),
    Column("batch_id", ForeignKey("batches.id")),
)


def start_mappers():
    lines_mapper = mapper(model.OrderLine, order_lines)
    mapper(
        model.Batch,
        batches,
        properties={
            "_allocations": relationship(
                lines_mapper, secondary=allocations, collection_class=set,
            )
        },
    )
```

### DIP (추상화한 저장소)
- 0장에서 계층화로 기분, 각 계층에서 접근할 수 있는 계층을 한정한다고 했다.
  - 표현 계층 -> 비지니스 계층 -> 데이터베이스 계층
- 비지니스 계층에서 데이터베이스 계층을 참조할 때 추상화된 저장소를 통하도록 한다.
  - 비지니스 계층에서 데이터베이스 계층 구체적인 내용을 몰라도 된다.
  - 테스트 코드 작성이 가능해진다.

```python
class AbstractRepository(abc.ABC):
    @abc.abstractmethod
    def add(self, batch: model.Batch):
        raise NotImplemented
    @abc.abstractmethod
    def get(self, reference) -> model.Batch:
        raise NotImplemented
```

#### 실제 저장소
- Production 에서 사용할 저장소를 `AbstractRepository`를 상속해 구현할 수 있다. 
```python
class SqlAlchemyRepository(AbstractRepository):
    def __init__(self, session):
        self.session = session

    def add(self, batch):
        self.session.add(batch)

    def get(self, reference):
        return self.session.query(model.Batch).filter_by(reference=reference).one()

    def list(self):
        return self.session.query(model.Batch).all()
```

#### 테스트를 위한 가짜 저장소
- 비지니스 계층 테스트를 위해 가짜 저장소를 만들 수 있다.
- 추상화를 하면서 얻는 가장 큰 이점이다.
```python
class FakeRepository(AbstractRepository):
    def __init__(self, batches):
        self._batches = set(batches)

    def add(self, batch):
        self._batches.add(batch)

    def get(self, reference):
        return next(b for b in self._batches if b.reference == reference)

    def list(self):
        return list(self._batches)
```

### 트레이드오프
- 어떤 도입에는 장/단이 있다.
- 장점
  - 저장소를 추상화했기에 구체적인 인프라는 언제든 교체될 수 있다. (예를 들면, mysql -> mongodb 로 전환)
  - 가짜 저장소처럼 테스트 가능한 코드를 만들 수 있다. 
- 단점
  - 저장소를 추상화하므로 복잡도가 증가한다.
  - 동시 계속 유지보수해야하는 비용이 발생한다.