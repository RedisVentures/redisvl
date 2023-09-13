# RedisVL: Python Client Library for Redis as a Vector Database

<div align="center">
<div display="inline-block">
    <a href="https://github.com/RedisVentures/RedisVL"><b>Home</b></a>&nbsp;&nbsp;&nbsp;
    <a href="https://www.redisvl.com"><b>Documentation</b></a>&nbsp;&nbsp;&nbsp;
    <a href="https://github.com/RedisVentures"><b>More Projects</b></a>&nbsp;&nbsp;&nbsp;
  </div>
    <br />
</div>


<div align="center">

[![Codecov](https://img.shields.io/codecov/c/github/RedisVentures/RedisVL/dev?label=Codecov&logo=codecov&token=E30WxqBeJJ)](https://codecov.io/gh/RedisVentures/RedisVL)
[![License](https://img.shields.io/badge/License-BSD-3--blue.svg)](https://opensource.org/licenses/mit/)
![Language](https://img.shields.io/github/languages/top/RedisVentures/RedisVL)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
![GitHub last commit](https://img.shields.io/github/last-commit/RedisVentures/RedisVL)
![GitHub deployments](https://img.shields.io/github/deployments/RedisVentures/RedisVL/github-pages?label=doc%20build)
[![pypi](https://badge.fury.io/py/redisvl.svg)](https://pypi.org/project/redisvl/)

</div>

RedisVL provides a powerful Python client library for using Redis as a Vector Database. Leverage the speed and reliability of Redis along with vector-based semantic search capabilities to supercharge your application!

**Note**: This supported by Redis, Inc. on a good faith effort basis. To report bugs, request features, or receive assistance, please [file an issue](https://github.com/RedisVentures/redisvl/issues).


------------


## 🚀 What is RedisVL?

Vector databases have become increasingly popular in recent years due to their ability to store and retrieve vectors efficiently. However, most vector databases are complex to use and require a lot of time and effort to set up. RedisVL aims to solve this problem by providing a simple and intuitive interface for using Redis as a vector database.

RedisVL provides a client library that enables you to harness the power and flexibility of Redis as a vector database. This library simplifies the process of storing, retrieving, and performing complex semantic and hybrid searches over vectors in Redis. It also provides a robust index management system that allows you to create, update, and delete indices with ease.


### Capabilities

RedisVL has a host of powerful features designed to streamline your vector database operations.

1. **Index Management**: RedisVL allows for indices to be created, updated, and deleted with ease. A schema for each index can be defined in yaml or directly in python code and used throughout the lifetime of the index.

2. **Embedding Creation**: RedisVL integrates with OpenAI, HuggingFace, and GCP VertexAI to simplify the process of vectorizing unstructured data. *Image support coming soon. Submit a PR for new vectorizers.*

3. **Vector Search**: RedisVL provides robust search capabilities that enable you to query vectors synchronously and asynchronously. Hybrid queries that utilize tag, geographic, numeric, and other filters like full-text search are also supported.

4. **Powerful Abstractions**
    - **Semantic Caching**: `LLMCache` is a semantic caching interface built directly into RedisVL. It allows for the caching of generated output from LLMs like GPT-3 and others. As semantic search is used to check the cache, a threshold can be set to determine if the cached result is relevant enough to be returned. If not, the model is called and the result is cached for future use. This can increase the QPS and reduce the cost of using LLM models in production.


## Installation

Please note that this library is still under heavy development, and while you can quickly try RedisVL and deploy it in a production environment, the API may be subject to change at any time.

Install `redisvl` using `pip`:

```bash
pip install redisvl
```

This library supports the use of hiredis, so you can also install by running:

```bash
pip install redisvl[hiredis]
```

Then make sure to have [Redis](https://redis.io) accessible with Search & Query features enabled on [Redis Cloud](https://redis.com/try-free) or locally in docker with [Redis Stack](https://redis.io/docs/getting-started/install-stack/docker/):

```bash
docker run -d --name redis-stack -p 6379:6379 -p 8001:8001 redis/redis-stack:latest
```

This will also spin up the [Redis Insight GUI](https://redis.com/redis-enterprise/redis-insight/) at `http://localhost:8001`.



## Example Usage

### Index Management

Indices can be defined through yaml specification, or json, that correspond directly to the Redis index, field names and arguments in `redis-py`. Take this example `idx.yaml` file:

```yaml
index:
  name: user_index
  prefix: users

fields:
  # define tag fields
  tag:
  - name: user
  - name: job
  - name: credit_store
  # define numeric fields
  numeric:
  - name: age
  # define vector fields
  vector:
  - name: user_embedding
    dim: 3
    algorithm: hnsw
    distance_metric: cosine
```

This would correspond to a dataset that looked something like:

| user  | age |     job    | credit_score |           user_embedding          |
|-------|-----|------------|--------------|-----------------------------------|
| john  |  1  |  engineer  |     high     | \x3f\x8c\xcc\x3f\x8c\xcc?@         |
| mary  |  2  |   doctor   |     low      | \x3f\x8c\xcc\x3f\x8c\xcc?@         |
|  joe  |  3  |  dentist   |    medium    | \x3f\xab\xcc?\xab\xcc?@         |


With the YAML schema, the RedisVL library can be used to create the index, load vectors and perform vector searches:
```python

from redisvl.index import SearchIndex
from redisvl.query import VectorQuery

# initialize the index and connect to local Redis
index = SearchIndex.from_schema("idx.yaml")
index.connect("redis://localhost:6379")

# create the index in Redis
index.create(overwrite=True)

# load data into the index in Redis (list of dicts)
index.load(data)

query = VectorQuery(
    vector=[0.1, 0.1, 0.5],
    vector_field_name="user_embedding",
    return_fields=["user", "age", "job", "credit_score"],
    num_results=3,
)
results = index.query(query)

```

### Redis Filter Expressions

RedisVL supports a variety of filter types, including tag, numeric, geographic, and full text search to create *Filter Expressions*. Filter expressions can be used to create hybrid queries which allow you to combine multiple complex data types (i.e. text and vector search) into a single query.

```python
from redisvl.index import SearchIndex
from redisvl.query import VectorQuery
from redisvl.query.filter import Tag, Num, Geo, GeoRadius, Text

# exact tag match
is_sam = Tag("user") == "Sam"

# numeric range
is_over_10 = Num("age") > 10

# geographic radius
works_in_sf = Geo("location") == GeoRadius(37.7749, -122.4194, 10)

# full text search with fuzzy match
is_engineer = Text("job") % "enginee*"

filter_expression = is_sam & is_over_10 & works_in_sf & is_engineer

query = VectorQuery(
    vector=[0.1, 0.1, 0.5],
    vector_field_name="user_embedding",
    return_fields=["user", "age", "job", "credit_score"],
    num_results=3,
    filter_expression=filter_expression,
)
results = index.query(query)

```

### Interoperability with core Redis Clients
The purpose of RedisVL is **NOT** to fully replace your usage of the trusted Redis client libraries. It's simply here to make your life easier getting started and building generative AI applications on top of Redis. With that in mind, the RedisVL query interface and filter expressions can be ported and used with clients like `redis-py`.

Take the example filter expression from above:
```python
print(str(filter_expression))
```

This prints the RediSearch hybrid filter expression:
```
'(((@user:{Sam} @age:[(10 +inf]) @location:[37.774900 -122.419400 10 km]) @job:enginee*)'
```

Take the fully formed vector query from above:
```python
print(str(query))
```

This prints the full RediSearch query, including hybrid filters and VSS:
```
'(((@user:{Sam} @age:[(10 +inf]) @location:[37.774900 -122.419400 10 km]) @job:enginee*)=>[KNN 3 @user_embedding $vector AS vector_distance] RETURN 5 user age job credit_score vector_distance SORTBY vector_distance ASC DIALECT 2 LIMIT 0 3'
```

> Both of these query strings and expressions can be used with the core RediSearch API.

For example, use the RedisVL `query` object with the standard `redis-py` client:
```python
import redis

r = redis.Redis.from_url("redis://localhost:6379")

results = r.ft("user_index").search(query.query, query.params)
```

### Semantic cache

The ``LLMCache`` Interface in RedisVL can be used as follows.

```python
from redisvl.llmcache.semantic import SemanticCache

cache = SemanticCache(
  redis_url="redis://localhost:6379",
  threshold=0.9, # semantic similarity threshold
)

# check if the cache has a result for a given query
cache.check("What is the capital of France?")
[ ]

# store a result for a given query
cache.store("What is the capital of France?", "Paris")

# Cache will now have the query
cache.check("What is the capital of France?")
["Paris"]

# Cache will still return the result if the query is similar enough
cache.check("What really is the capital of France?")
["Paris"]
```
