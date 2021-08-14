## 서비스 계층
- 도메인 모델과 저장소 만들었다.
- 이제 시스템 유즈 케이스를 정의하는 서비스 계층이다.
- 서비스 계층 없이 구현해보고 단점 확인 후 도입해본다.

### 테스트 코드 작성
- 플라스크를 이용하여 만든 기능을 웹으로 제공하고 싶다.
- TDD이니 테스트 코드부터 작성한다.
- E2E 테스트를 작성한다. (느린 테스트)

```python
# test_api.py
@pytest.mark.usefixtures('restart_api')
def test_api_returns_allocation(add_stock):
    sku, othersku = random_sku(), random_sku("other")
    earlybatch = random_batchref("1")
    laterbatch = random_batchref("2")
    otherbatch = random_batchref("3")
    add_stock([
        (laterbatch, sku, 100, '2011-01-02'),
        (earlybatch, sku, 100, '2011-01-01'),
        (otherbatch, othersku, 100, None)
    ])
    data = {'orderid': random_orderid(), 'sku': sku, 'qty': 3}
    url = config.get_api_url()
    r = requests.post(f'{url}/allocate', json=data)
    assert r.status_code == 201
    assert r.json()['batchref'] == earlybatch
```

- 로컬에 postgresql, api 서버를 올리고 requests 로 실제 테스트한다.

### 직접 구현하기
- 가장 뻔한 방법으로 구현해본다.
- endpoint 함수에서 직접 repo 로 도메인 모델을 가져와 작업한다. 마지막에 `session.commit()` 해준다.

```python
# flask_app.py
@app.route("/allocate", methods=["POST"])
def allocate_endpoint():
    session = get_session()
    batches = repository.SqlAlchemyRepository(session).list()
    line = model.OrderLine(
        request.json["orderid"], request.json["sku"], request.json["qty"]
    )
    batchref = model.allocate(line, batches)
    session.commit()

    return {"batchref": batchref}, 201
```

### 오류 조건 추가
- 여기서 실패하는 경우도 추가하고 싶다.
- 재고가 없는 경우, 잘못된 sku 가 들어온 경우 E2E 테스트코드 작성한다.

```python
# test_api.py
@pytest.mark.usefixtures('restart_api')
def test_400_message_for_out_of_stock(add_stock):
    sku, small_batch, large_order = random_sku(), random_batchref(), random_orderid()
    add_stock([
        (small_batch, sku, 10, '2011-01-01')
    ])
    data = {'orderid': large_order, 'sku': sku, 'qty': 20}
    url = config.get_api_url()
    r = requests.post(f'{url}/allocate', json=data)
    assert r.status_code == 400
    assert r.json()['message'] == f'Out of stock for sku {sku}'


@pytest.mark.usefixtures('restart_api')
def test_400_message_for_invaild_sku():
    unknown_sku, orderid = random_sku(), random_orderid()
    data = {'orderid': orderid, 'sku': unknown_sku, 'qty': 20}
    url = config.get_api_url()
    r = requests.post(f'{url}/allocate', json=data)
    assert r.status_code == 400
    assert r.json()['message'] == f'Invalid sku {unknown_sku}'
```

#### 복잡해지는 `allocate_endpoint` 함수
- 위 오류 조건 추가로 `flask_app.py` 에 구현 필요하다.
- `allocate_endpoint` 를 수정하면 되는데 점점 복잡해지는 느낌이다.

```python
# flask_app.py
def is_valid_sku(sku, batches):
    return sku in {b.sku for b in batches}

@app.route("/allocate", methods=["POST"])
def allocate_endpoint():
    session = get_session()
    batches = repository.SqlAlchemyRepository(session).list()
    line = model.OrderLine(
        request.json["orderid"], request.json["sku"], request.json["qty"]
    )

    if not is_valid_sku(line.sku, batches):
        return {"message": f"Invalid sku {line.sku}"}, 400

    try:
        batchref = model.allocate(line, batches)
    except model.OutOfStock as e:
        return {"message": str(e)}, 400
    session.commit()

    return {"batchref": batchref}, 201
```

- 더 큰 문제는 E2E 테스트가 많아진다는 것이다.
  - E2E 테스트보다는 유닛 테스트 수가 많은 것이 좋다.

### 서비스 계층 분리
- `allocate_endpoint` 함수에서는 오케스트레이션 작업을 위주로 한다.
  - 입력을 검증한다.
  - 저장소로부터 도메인 모델을 가져온다.
  - 도메인 모델 조작 성공 후 저장을 위해 `session.commit()` 한다.
- 오케스트레이션 계층은 서비스 계층으로 빼내는 것이 좋다.

```python
# services.py
class InvalidSku(Exception):
    pass

def is_valid_sku(sku, batches):
    return sku in {b.sku for b in batches}

def allocate(line: OrderLine, repo: AbstractRepository, session) -> str:
    batches = repo.list()
    if not is_valid_sku(line.sku, batches):
        raise InvalidSku(f"Invalid sku {line.sku}")
    batchref = model.allocate(line, batches)
    session.commit()
    return batchref
```

#### FakeRepository 사용하기
- 서비스 계층에 대한 테스트코드 필요하다.
- 이 때 저장소에 대한 의존성을 해결해줄 `FakeRepository`를 구현하고 이용할 수 있다.

```python
# test_service.py
class FakeRepository(repository.AbstractRepository):
    def __init__(self, batches):
        self._batches = set(batches)

    def add(self, batch):
        self._batches.add(batch)

    def get(self, reference):
        return next(b for b in self._batches if b.reference == reference)

    def list(self):
        return list(self._batches)

class FakeSession:
    committed = False

    def commit(self):
        self.committed = True

def test_returns_allocation():
    line = model.OrderLine("o1", "COMPLICATED-LAMP", 10)
    batch = model.Batch("b1", "COMPLICATED-LAMP", 100, eta=None)
    repo = FakeRepository([batch])

    result = services.allocate(line, repo, FakeSession())
    assert result == "b1"

def test_error_for_invalid_sku():
    line = model.OrderLine("o1", "NONEXISTENTSKU", 10)
    batch = model.Batch("b1", "AREALSKU", 100, eta=None)
    repo = FakeRepository([batch])

    with pytest.raises(services.InvalidSku, match="Invalid sku NONEXISTENTSKU"):
        services.allocate(line, repo, FakeSession())

def test_commits():
    line = model.OrderLine("o1", "OMINOUS-MIRROR", 10)
    batch = model.Batch("b1", "OMINOUS-MIRROR", 100, eta=None)
    repo = FakeRepository([batch])
    session = FakeSession()

    services.allocate(line, repo, session)
    assert session.committed is True
```

#### 서비스 계층에 위임하는 플라스크 앱
- 서비스 계층을 만들어줬으니 `allocate_endpoint` 함수에서 위임해주도록 하자.

```python
# flask_app.py 
@app.route("/allocate", methods=["POST"])
def allocate_endpoint():
    session = get_session()
    repo = repository.SqlAlchemyRepository(session)
    line = model.OrderLine(request.json["orderid"], request.json["sku"], request.json["qty"])
    try:
        # service 계층에 위임
        batchref = services.allocate(line, repo, session)
    except (model.OutOfStock, services.InvalidSku) as e:
        return {"message": str(e)}, 400

    return {"batchref": batchref}, 201
```

- 서비스 계층에 위임하고 테스트 코드로 검증했으니 불필요한 E2E 테스트는 지울 수 있다.
  - 성공 케이스 하나, 실패 케이스 하나로 정리할 수 있다.
  - 웹 기능을 테스트하는 것만 남긴다. (오케스트레이션 기능들은 서비스 계층 테스트로 커버한다.) 

```python
# test_api.py
@pytest.mark.usefixtures("restart_api")
def test_happy_path_returns_201_and_allocated_batch(add_stock):
    sku, othersku = random_sku(), random_sku("other")
    earlybatch = random_batchref(1)
    laterbatch = random_batchref(2)
    otherbatch = random_batchref(3)
    add_stock(
        [
            (laterbatch, sku, 100, "2011-01-02"),
            (earlybatch, sku, 100, "2011-01-01"),
            (otherbatch, othersku, 100, None),
        ]
    )
    data = {"orderid": random_orderid(), "sku": sku, "qty": 3}
    url = config.get_api_url()

    r = requests.post(f"{url}/allocate", json=data)

    assert r.status_code == 201
    assert r.json()["batchref"] == earlybatch


@pytest.mark.usefixtures("restart_api")
def test_unhappy_path_returns_400_and_error_message():
    unknown_sku, orderid = random_sku(), random_orderid()
    data = {"orderid": orderid, "sku": unknown_sku, "qty": 20}
    url = config.get_api_url()
    r = requests.post(f"{url}/allocate", json=data)
    assert r.status_code == 400
    assert r.json()["message"] == f"Invalid sku {unknown_sku}"
```

### 애플리케이션 서비스 vs 도메인 서비스
- 애플리케이션 서비스는 서비스 계층을 의미한다. 오케스트레이션 작업을 위주로 한다.
  - 데이터베이스에서 데이터를 얻는다.
  - 도메인 모델을 업데이트한다.
  - 변경된 내용을 영속화한다.
- 도메인 서비스는 도메인 모델은 맞지만 상태가 있는 엔티티나 값 객체에 속하지 않는 로직이다.
  - 예를 들면 쇼핑카트 앱에서 세금을 계산하는 경우, `세금 계산`은 중요한 도메인이지만 `쇼핑카트`와는 별개이며 영속적인 엔티티도 아니다.
  - 이 경우 상태가 없는 `TaxCalculator`, `calculate_tex` 함수에서 세금 계산을 할 수 있다. 
