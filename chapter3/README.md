## 막간: 결합과 추상화
- 추상화 왜 필요할까?

### 결합도 낮추기
- 두 컴포넌트가 서로 아는게 많아지면 결합도가 높아진다.
  - 한쪽을 변경했을 때 의존하는 다른 컴포넌트도 변경이 필요하다.
- 추상화는 세부구현을 가려 결합도를 낮츨 수 있다.
  - 반대로 각 컴포넌트별 응집도를 높혔다고 할 수 있다.

### 디렉토리 sync 하기
- 추상화에 대한 예시로 디렉토리 sync 하는 애플리케이션을 예로 든다.
- 애플리케이션 핵심 로직은 이러하다.
  - 원본에 파일이 있지만 사본에 없으면 파일을 원본에서 사본으로 복사한다.
  - 원본에 파일이 있지만 사본에 있는 (내용이 같은) 파일과 이름이 다르면 사본의 파일을 원본 파일 이름과 같게 변경한다.
  - 사본에 파일이 있지만 원본에는 없다면 사본의 파일을 삭제한다.
- 디렉토리 내 파일 비교를 위해 hash 함수가 필요하다.
```python
BLOCKSIZE = 65536

def hash_file(path):
    hasher = hashlib.sha1()
    with path.open("rb") as file:
        buf = file.read(BLOCKSIZE)
        while buf:
            hasher.update(buf)
            buf = file.read(BLOCKSIZE)
    return hasher.hexdigest()
```

- 그리고 아래처럼 구현할 수 있다.
```python
def sync(source, dest):
    source_hashes = {}
    # source 디렉토리 파일 hash 를 구한다.
    for folder, _, files in os.walk(source):
        for fn in files:
            source_hashes[hash_file(Path(folder) / fn)] = fn

    seen = set()
    
    # dest 디렉토리 파일 hash 를 구한다.
    for folder, _, files in os.walk(dest):
        for fn in files:
            dest_path = Path(folder) / fn
            dest_hash = hash_file(dest_path)
            seen.add(dest_hash)

            # dest 디렉토리 파일 중 source 디렉토리에 없으면 삭제한다.
            if dest_hash not in source_hashes:
                dest_path.remove()

            # dest 디렉토리 파일 중 source 디렉토리에 있으나 파일 이름이 다르면 이름을 source 디렉토리 파일과 같게 만든다.
            elif dest_hash in source_hashes and fn != source_hashes[dest_hash]:
                shutil.move(dest_path, Path(folder) / source_hashes[dest_hash])

    for src_hash, fn in source_hashes.items():
        # dest 디렉토리에 source 디렉토리 파일이 없으면 복사한다. 
        if src_hash not in seen: 
            shutil.copy(Path(source) / fn, Path(dest) / fn)
```
- 위 구현의 단점
  1. 위 코드를 유닛 테스트하려면 I/O 를 피할 수 없다.
     - 테스트 코드 내에서 실제 디렉토리 생성 및 파일 생성으로 환경을 만들어주고 함수를 수행해봐야할 것이다.
     - 테스트 코드에서 실제 I/O가 일어나는 건 가급적 피해야한다. 테스트 시간을 늘릴뿐이며 비결정성이 높아진다.
  2. `--dry-run` 플래그를 구현한다면? 변경을 피할 수 없다. 확장성이 좋지 않다.

### Functional Core, Imperative Shell
- 유닛 테스트에서 I/O는 피해야한다.
- 이를 위해 I/O(Imperative)와 실제 핵심로직(Functional Core)를 분리, 핵심로직만 테스트를 수행할 수 있다.
- 실제 I/O와 함께 하는 테스트는 통합/인수 테스트에서 커버하고 많은 부분은 핵심로직 유닛테스트 작성하기로 한다.
```python
def sync(source, dest):
    # imperative shell step 1, 디렉토리 내 파일 해쉬를 모은다.
    source_hashes = read_paths_and_hashes(source)
    dest_hashes = read_paths_and_hashes(dest)

    # step 2: functional core 를 호출한다.
    actions = determine_actions(source_hashes, dest_hashes, source, dest)

    # imperative shell step 3, functional core 결과값을 출력한다.
    for action, *paths in actions:
        if action == "COPY":
            shutil.copyfile(*paths)
        if action == "MOVE":
            shutil.move(*paths)
        if action == "DELETE":
            os.remove(paths[0])
```
- 아래와 같이 `determine_actions`로 핵심로직을 I/O 없이 검증할 수 있다.
```python
def test_when_a_file_exists_in_the_source_but_not_the_destination():
    source_hashes = {"hash1": "fn1"}
    dest_hashes = {}
    actions = determine_actions(source_hashes, dest_hashes, Path("/src"), Path("/dst"))
    assert list(actions) == [("COPY", Path("/src/fn1"), Path("/dst/fn1"))]

def test_when_a_file_has_been_renamed_in_the_source():
    source_hashes = {"hash1": "fn1"}
    dest_hashes = {"hash1": "fn2"}
    actions = determine_actions(source_hashes, dest_hashes, Path("/src"), Path("/dst"))
    assert list(actions) == [("MOVE", Path("/dst/fn2"), Path("/dst/fn1"))]
```

### 추상화
- `Functional Core, Imperative Shell`를 통해 핵심로직에 대한 유닛테스트는 작성할 수 있었다.
- 여전히 구체적인 `shutil`, `os`를 사용하며 `--dry-run` 플래그를 구현하기엔 확장성이 좋지않다.
- I/O와 연관있는 부분은 추상화할 수 있다.
```python
class FakeFileSystem(list):
    def copy(self, src, dest):
        self.append(('COPY', src, dest))

    def move(self, src, dest):
        self.append(('MOVE', src, dest))

    def delete(self, dest):
        self.append(('DELETE', dest))

def sync(reader, filesystem, source_root, dest_root):
    source_hashes = reader(source_root)
    dest_hashes = reader(dest_root)

    for sha, filename in source_hashes.items():
        if sha not in dest_hashes:
            sourcepath = source_root / filename
            destpath = dest_root / filename
            filesystem.copy(destpath, sourcepath)

        elif dest_hashes[sha] != filename:
            olddestpath = dest_root / dest_hashes[sha]
            newdestpath = dest_root / filename
            filesystem.move(olddestpath, newdestpath)

    for sha, filename in dest_hashes.items():
        if sha not in source_hashes:
            filesystem.delete(dest_root / filename)
```
- `reader`와 `filesystem`으로 추상화를 시켰고 테스트에서는 Fake 를 사용할 수 있어진다. (I/O 없는 유닛테스트)
- `--dry-run` 플래그용 `filesystem` 구현체를 만들어서 넣어주면 `sync(...)`코드 변경 없이 구현할 수 있다.

### `mock.patch()`와 코드스멜
- 저자는 `mock.patch()`를 코드스멜로 본다.
- `mock.patch()`로는 설계개선에 도움이 되지 않는다.
  - 처음 구현에서 `mock.patch()`를 잘쓰면 I/O 없이 테스트 코드를 작성할 수 있다.
- mock 테스트는 실제 구현 세부 사항과 밀접하게 연관된다. 이는 세부 구현 변경시 테스트를 깨지게 할 수 있다.
- mock 이 과용되면 테스트가 너무 복잡해져서 테스트 대상 코드 동작을 파악하기 어려워진다.
