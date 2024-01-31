import pytest

from redisvl.index import SearchIndex
from redisvl.redis.utils import convert_bytes
from redisvl.schema import IndexSchema, StorageType

fields = [{"name": "test", "type": "tag"}]


@pytest.fixture
def index_schema():
    return IndexSchema.from_dict({"index": {"name": "my_index"}, "fields": fields})


@pytest.fixture
def index(index_schema):
    return SearchIndex(schema=index_schema)


def test_search_index_properties(index_schema, index):
    assert index.schema == index_schema
    # custom settings
    assert index.name == index_schema.index.name == "my_index"
    assert index.client == None
    # default settings
    assert index.prefix == index_schema.index.prefix == "rvl"
    assert index.key_separator == index_schema.index.key_separator == ":"
    assert index.storage_type == index_schema.index.storage_type == StorageType.HASH
    assert index.key("foo").startswith(index.prefix)


def test_search_index_no_prefix(index_schema):
    # specify an explicitly empty prefix...
    index_schema.index.prefix = ""
    index = SearchIndex(schema=index_schema)
    assert index.prefix == ""
    assert index.key("foo") == "foo"


def test_search_index_redis_url(redis_url, index_schema):
    index = SearchIndex(schema=index_schema, redis_url=redis_url)
    assert index.client

    index.disconnect()
    assert index.client == None


def test_search_index_client(client, index_schema):
    index = SearchIndex(schema=index_schema, redis_client=client)
    assert index.client == client


def test_search_index_set_client(async_client, client, index):
    index.set_client(client)
    assert index.client == client
    # should not be able to set the sync client here
    with pytest.raises(TypeError):
        index.set_client(async_client)

    index.disconnect()
    assert index.client == None


def test_search_index_create(client, index):
    index.set_client(client)
    index.create(overwrite=True, drop=True)
    assert index.exists()
    assert index.name in convert_bytes(index.client.execute_command("FT._LIST"))


def test_search_index_delete(client, index):
    index.set_client(client)
    index.create(overwrite=True, drop=True)
    index.delete(drop=True)
    assert not index.exists()
    assert index.name not in convert_bytes(index.client.execute_command("FT._LIST"))


def test_search_index_load_and_fetch(client, index):
    index.set_client(client)
    index.create(overwrite=True, drop=True)
    data = [{"id": "1", "test": "foo"}]
    index.load(data, key_field="id")

    res = index.fetch("1")
    assert res["test"] == convert_bytes(client.hget("rvl:1", "test")) == "foo"

    index.delete(drop=True)
    assert not index.exists()
    assert not index.fetch("1")


def test_search_index_load_preprocess(client, index):
    index.set_client(client)
    index.create(overwrite=True, drop=True)
    data = [{"id": "1", "test": "foo"}]

    def preprocess(record):
        record["test"] = "bar"
        return record

    index.load(data, key_field="id", preprocess=preprocess)
    res = index.fetch("1")
    assert res["test"] == convert_bytes(client.hget("rvl:1", "test")) == "bar"

    def bad_preprocess(record):
        return 1

    with pytest.raises(TypeError):
        index.load(data, key_field="id", preprocess=bad_preprocess)


def test_no_key_field(client, index):
    index.set_client(client)
    index.create(overwrite=True, drop=True)
    bad_data = [{"wrong_key": "1", "value": "test"}]

    # catch missing / invalid key_field
    with pytest.raises(ValueError):
        index.load(bad_data, key_field="key")
