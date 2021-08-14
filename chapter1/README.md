## 도메인 모델링
- 도메인 모델은 특정 영역/문제를 표현한 개념 모델이다.
  - e.g. 가구 판매회사에서 `구매 및 조달`, `제품 설계`, `물류 및 배달`이 도메인이 될 수 있다.
- 도메인 모델에 비지니스 로직을 모아 비지니스 로직이 여러 곳에 퍼지는 것을 막을 수 있다. 
- 비니지스 용어에서 모델링을 하면 다른 팀과 대화할 때 편해진다.

### 단위테스트
- 도메인 모델 코드를 바로 작성하지 않고 테스트부터 작성한다. (TDD)
- 포인트는 비지니스 영역에 있는 용어들을 가져와 작성한다.

```python
def test_allocating_to_a_batch_reduces_the_available_quantity():
    # given
    batch = Batch("batch-001", "SMALL-TABLE", qty=20, eta=date.today())
    line = OrderLine('order-ref', "SMALL-TABLE", 2)
    
    # when
    batch.allocate(line)
    
    # then
    assert batch.available_quantity == 18
```

- 위 테스트가 통과할 수 있도록 도메인 모델을 작성한다.

```python
@dataclass(frozen=True)
class OrderLine:
    orderid: str
    sku: str
    qty: int

class Batch:
    def __init__(self, ref: str, sku: str, qty: int, eta: Optional[date]):
        self.reference = ref
        self.sku = sku
        self.eta = eta
        self.available_quantity = qty

    def allocate(self, line: OrderLine):
        self.available_quantity -= line.qty
```

- 이어서 `can_allocate` 메소드에 대해 테스트 코드를 작성한다.

```python
def make_batch_and_line(sku, batch_qty, line_qty):
    return (
        Batch("batch-001", sku, batch_qty, eta=date.today()),
        OrderLine("order-123", sku, line_qty)
    )

def test_can_allocate_if_available_greater_than_required():
    large_batch, small_line = make_batch_and_line("ELEGANT-LAMP", 20, 2)
    assert large_batch.can_allocate(small_line)


def test_cannot_allocate_if_available_smaller_than_required():
    small_batch, large_line = make_batch_and_line("ELEGANT-LAMP", 2, 20)
    assert small_batch.can_allocate(large_line) is False


def test_can_allocate_if_available_equal_to_required():
    batch, line = make_batch_and_line("ELEGANT-LAMP", 2, 2)
    assert batch.can_allocate(line)


def test_cannot_allocate_if_skus_do_not_match():
    batch = Batch("batch-001", "UNCOMFORTABLE-CHAIR", 100, eta=None)
    different_sku_line = OrderLine("order-123", "EXPENSIVE-TOASTER", 10)
    assert batch.can_allocate(different_sku_line) is False
```

- 실패를 확인하고 `can_allocate` 메소드를 구현한다.

```python
    def can_allocate(self, line: OrderLine):
        return self.sku == line.sku and self.available_quantity >= line.qty
```

- `deallocate` 할 수 있다면 어떨까?

```python
def test_can_only_deallocate_allocated_lines():
    batch, unallocated_line = make_batch_and_line("DECORATIVE-TRINKET", 20, 2)
    batch.deallocate(unallocated_line)
    assert batch.available_quantity == 20
```

- 기존 할당된 line 을 기억하고 있어야한다

```python
class Batch:
    def __init__(self, ref: str, sku: str, qty: int, eta: Optional[date]):
        self.reference = ref
        self.sku = sku
        self.eta = eta
        self._purchased_quantity = qty
        self._allocations = set()

    def allocate(self, line: OrderLine):
        if self.can_allocate(line):
            self._allocations.add(line)

    def deallocate(self, line):
        if line in self._allocations:
            self._allocations.remove(line)

    @property
    def allocated_quantity(self) -> int:
        return sum(line.qty for line in self._allocations)

    @property
    def available_quantity(self) -> int:
        return self._purchased_quantity - self.allocated_quantity

    def can_allocate(self, line: OrderLine):
        return self.sku == line.sku and self.available_quantity >= line.qty
```

- 이렇게 비지니스 로직을 도메인 모델에 모아 구현할 수 있다.

### 엔티티 vs 값 객체
- 도메인 모델은 엔티티 혹은 값 객체이며 아래와 같은 차이를 가진다.

|-|엔티티|값 객체|
|------|---|---|
|동등성 종류(Type of Equality)|정체성 동등성(identifier equality)|값 동등성(value equality)|
|변이성(Mutability)|가능(mutable)|불가(immutable)|
|수명|생애주기가 존재|생애주기가 존재하지 않음|

- 엔티티는 정체성 동등성로 객체간 ID 비교로 같은지 확인할 수 있다.
- 값 객체는 값 동등성으로 객체가 가진 멤버변수를 모두 비교해 같은지 확인할 수 있다.

### 도메인 서비스 함수
- 비지니스 로직이 꼭 도메인 객체 메소드로 구현될 필요는 없다.
- 엔티티나 값 객체 두기 애매한 비지니스 로직이 있다. 이를 도메인 서비스 함수로 만들 수 있다.
- 여러 Batch 중 하나를 선택해 `allocate` 하는 경우 Batch, OrderLine 중 어디에 둘지 애매해진다. 

```python
def test_prefers_current_stock_batches_to_shipments():
    in_stock_batch = Batch("in-stock-batch", "RETRO-CLOCK", 100, eta=None)
    shipment_batch = Batch("shipment-batch", "RETRO-CLOCK", 100, eta=tomorrow)
    line = OrderLine("oref", "RETRO-CLOCK", 10)

    allocate(line, [in_stock_batch, shipment_batch])

    assert in_stock_batch.available_quantity == 90
    assert shipment_batch.available_quantity == 100


def test_prefers_earlier_batches():
    earliest = Batch("speedy-batch", "MINIMALIST-SPOON", 100, eta=today)
    medium = Batch("normal-batch", "MINIMALIST-SPOON", 100, eta=tomorrow)
    latest = Batch("slow-batch", "MINIMALIST-SPOON", 100, eta=later)
    line = OrderLine("order1", "MINIMALIST-SPOON", 10)

    allocate(line, [medium, earliest, latest])

    assert earliest.available_quantity == 90
    assert medium.available_quantity == 100
    assert latest.available_quantity == 100


def test_returns_allocated_batch_ref():
    in_stock_batch = Batch("in-stock-batch-ref", "HIGHBROW-POSTER", 100, eta=None)
    shipment_batch = Batch("shipment-batch-ref", "HIGHBROW-POSTER", 100, eta=tomorrow)
    line = OrderLine("oref", "HIGHBROW-POSTER", 10)
    allocation = allocate(line, [in_stock_batch, shipment_batch])
    assert allocation == in_stock_batch.reference
```

- 이 경우 새롭게 서비스 함수를 만들면 좋다.

```python
def allocate(line: OrderLine, batches: List[Batch]) -> str:
    batch = next(b for b in sorted(batches) if b.can_allocate(line))
    batch.allocate(line)
    return batch.reference
```

### 예외와 도메인 개념
- 예외로 도메인 개념을 표현할 수 있다.
- 예를 들면 `allocate`시 품절 개념을 예외로 표현할 수 있다.
- 역시 테스트 코드부터 작성한다.

```python
def test_raises_out_of_stock_exception_if_cannot_allocate():
    batch = Batch("batch1", "SMALL-FORK", 10, eta=today)
    allocate(OrderLine("order1", "SMALL-FORK", 10), [batch])

    with pytest.raises(OutOfStock, match="SMALL-FORK"):
        allocate(OrderLine("order2", "SMALL-FORK", 1), [batch])
```

- 그리고 구현한다. Batch 를 찾지못할 시 `OutOfStock` 예외를 발생시킨다.

```python
def allocate(line: OrderLine, batches: List[Batch]) -> str:
    try:
        batch = next(b for b in sorted(batches) if b.can_allocate(line))
        batch.allocate(line)
        return batch.reference
    except StopIteration:
        raise OutOfStock(f"Out of stock for sku {line.sku}")
```
