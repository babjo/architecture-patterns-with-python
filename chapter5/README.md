## 높은 기어비와 낮은 기어비의 TDD

### 테스트 피라미드
- 비용이슈로 테스트는 `단위테스트 수 > 통합테스트 수 > E2E 수`를 따르면 좋다.

![](https://miro.medium.com/max/1400/1*Tcj3OsK8Kou7tCMQgeeCuw.png)
 
- 지금까지 작성한 테스트 갯수를 세보자.

```
$ grep -c test_ test_*.py
tests/unit/test_allocate.py
tests/unit/test_batches.py
tests/unit/test_services.py

tests/integration/test_orm.py
tests/integration/test_repository.py

tests/e2e/test_api.py
```

- 단위테스트 15개, 통합테스트 8개, E2E 테스트 2개뿐이다.
  - 괜찮아보인다.

### 도메인 모델 의존성 분리
- 도메인 모델은 모든 계층에 많이 사용될 수 있다.
- 그런데 반대로 이야기하면 도메인 모델 변경은 모든 계층 변경을 불러올 수 있으며 모든 계층에 대한 테스트코드 또한 변경이 필요해진다.
- 도메인 모델을 가장 많이 이용하는 서비스 계층에서 도메인 모델과 의존을 분리해보자.
- 서비스 계층 함수 시그니쳐에서 도메인 모델을 제거하자.

```python
# service_layer/services.py
def allocate(orderid: str, sku: str, qty: int, repo: AbstractRepository, session) -> str:
    line = OrderLine(orderid, sku, qty)
    batches = repo.list()
    if not is_valid_sku(line.sku, batches):
        raise InvalidSku(f"Invalid sku {line.sku}")
    batchref = model.allocate(line, batches)
    session.commit()
    return batchref
```

- 도메인 모델 의존성을 픽스처 함수로 넣는다.

```python
# tests/test_services.py
class FakeRepository(repository.AbstractRepository):

    @staticmethod
    def for_batch(ref, sku, qty, eta=None):
        return FakeRepository([
            model.Batch(ref, sku, qty, eta)
        ])
...

def test_returns_allocation():
    repo = FakeRepository.for_batch("b1", "COMPLICATED-LAMP", 100, eta=None)
    result = services.allocate("o1", "COMPLICATED-LAMP", 10, repo, FakeSession())
    assert result == "b1"
```

- 도메인 모델에 대한 의존이 없어졌으니 도메인 모델 변경이 쉬워졌다.

#### 한단계 더
- 서비스 계층에 `Batch` 를 추가하는 함수가 있으면 온전히 서비스 계층 함수만으로 서비스 계층 테스트를 만들 수 있다.

```python
# service_layer/services.py
def add_batch(ref: str, sku: str, qty: int, eta: Optional[date], repo, session):
    repo.add(model.Batch(ref, sku, qty, eta))
    session.commit()
```

```python
# tests/test_services.py
def test_returns_allocation():
    repo, session = FakeRepository([]), FakeSession()
    services.add_batch("b1", "COMPLICATED-LAMP", 100, None, repo, session)

    result = services.allocate("o1", "COMPLICATED-LAMP", 10, repo, session)
    assert result == "b1"

def test_add_batch():
    repo, session = FakeRepository([]), FakeSession()
    services.add_batch("b1", "CRUNCHY-ARMCHAIR", 100, None, repo, session)
    assert repo.get("b1") is not None
    assert session.committed
```

- `add_batch(...)` 함수를 엔드포인트로 노출하면 불필요한 픽스처도 없앨 수 있다.

### 정리
- 기능당 E2E 테스트는 하나만 만든다. (잘 부품이 연결됐는지 정도) (가급적이면 적게 만든다.)
- 테스트 대부분은 서비스 계층으로 한다.
  - Fake 객체 사용으로 시간 절감이 가능하며 비지니스 로직, 에지 케이스 모두 확인가능하다.
- 도메인 모델은 핵심 테스트만 작성하고 작게 유지한다.
  - 변경이 용이해진다.
