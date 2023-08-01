from typing import List, Optional, Union

from redis.commands.search.field import VectorField

from redisvl.index import SearchIndex
from redisvl.llmcache.base import BaseLLMCache
from redisvl.vectorize.text import HuggingfaceVectorizer
from redisvl.vectorize.base import BaseVectorizer
from redisvl.query import VectorQuery
from redisvl.utils.utils import array_to_buffer


class SemanticCache(BaseLLMCache):
    """Cache for Large Language Models."""

    # TODO allow for user to change default fields
    _default_fields = [
        VectorField(
            "prompt_vector",
            "FLAT",
            {"DIM": 768, "TYPE": "FLOAT32", "DISTANCE_METRIC": "COSINE"},
        ),
    ]

    def __init__(
        self,
        index_name: Optional[str] = "cache",
        prefix: Optional[str] = "llmcache",
        threshold: Optional[float] = 0.9,
        ttl: Optional[int] = None,
        vectorizer: Optional[BaseVectorizer] = HuggingfaceVectorizer(
            "sentence-transformers/all-mpnet-base-v2"
        ),
        redis_url: Optional[str] = "redis://localhost:6379",
        connection_args: Optional[dict] = None,
        index: Optional[SearchIndex] = None
    ):
        """Semantic Cache for Large Language Models.

        Args:
            index_name (Optional[str], optional): The name of the index. Defaults to "cache".
            prefix (Optional[str], optional): The prefix for the index. Defaults to "llmcache".
            threshold (Optional[float], optional): Semantic threshold for the cache. Defaults to 0.9.
            ttl (Optional[int], optional): The TTL for the cache. Defaults to None.
            vectorizer (Optional[BaseVectorizer], optional): The vectorizer for the cache.
                Defaults to HuggingfaceVectorizer("sentence-transformers/all-mpnet-base-v2").
            redis_url (Optional[str], optional): The redis url. Defaults to "redis://localhost:6379".
            connection_args (Optional[dict], optional): The connection arguments for the redis client. Defaults to None.
            index (Optional[SearchIndex], optional): The underlying search index to use for the semantic cache. Defaults to None.

        Raises:
            ValueError: If the threshold is not between 0 and 1.
            ValueError: If the index name or prefix is not supplied when constructing index manually.
        """
        self._ttl = ttl
        self._vectorizer = vectorizer
        self.set_threshold(threshold)
        connection_args = connection_args or {}

        if not index:
            if index_name and prefix:
                index = SearchIndex(name=index_name, prefix=prefix, fields=self._default_fields)
                index.connect(url=redis_url, **connection_args)
            else:
                raise ValueError("Index name and prefix must be provided if not constructing from an existing index.")

        # create index if non-existent
        if not index.exists():
            index.create()

        self._index = index


    @classmethod
    def from_index(cls, index: SearchIndex, **kwargs):
        """Create a SemanticCache from a pre-existing SearchIndex.

        Args:
            index (SearchIndex): The SearchIndex object to use as the backbone of the cache.

        Returns:
            SemanticCache: A SemanticCache object.
        """
        return cls(
            index=index,
            **kwargs
        )

    @property
    def ttl(self) -> Optional[int]:
        """Returns the TTL for the cache.

        Returns:
            Optional[int]: The TTL for the cache.
        """
        return self._ttl

    def set_ttl(self, ttl: int):
        """Sets the TTL for the cache.

        Args:
            ttl (int): The TTL for the cache.

        Raises:
            ValueError: If the TTL is not an integer.
        """
        self._ttl = int(ttl)

    @property
    def index(self) -> SearchIndex:
        """Returns the index for the cache.

        Returns:
            SearchIndex: The index for the cache.
        """
        return self._index

    @property
    def threshold(self) -> float:
        """Returns the threshold for the cache."""
        return self._threshold

    def set_threshold(self, threshold: float):
        """Sets the threshold for the cache.

        Args:
            threshold (float): The threshold for the cache.

        Raises:
            ValueError: If the threshold is not between 0 and 1.
        """
        if not 0 <= float(threshold) <= 1:
            raise ValueError("Threshold must be between 0 and 1.")
        self._threshold = float(threshold)

    def clear(self):
        """Clear the LLMCache of all keys in the index"""
        client = self._index.client
        if client:
            pipe = client.pipeline()
            for key in client.scan_iter(match=f"{self._index._prefix}:*"):
                pipe.delete(key)
            pipe.execute()
        else:
            raise RuntimeError("LLMCache is not connected to a Redis instance.")

    def check(
        self,
        prompt: Optional[str] = None,
        vector: Optional[List[float]] = None,
        num_results: int = 1,
        fields: List[str] = ["response"],
    ) -> Optional[List[str]]:
        """Checks whether the cache contains the specified prompt or vector.

        Args:
            prompt (Optional[str], optional): The prompt to check. Defaults to None.
            vector (Optional[List[float]], optional): The vector to check. Defaults to None.
            num_results (int, optional): The number of results to return. Defaults to 1.
            fields (List[str], optional): The fields to return. Defaults to ["response"].

        Raises:
            ValueError: If neither prompt nor vector is specified.

        Returns:
            Optional[List[str]]: The response(s) if the cache contains the prompt or vector.
        """
        if not prompt and not vector:
            raise ValueError("Either prompt or vector must be specified.")

        if not vector:
            vector = self._vectorizer.embed(prompt)  # type: ignore

        v = VectorQuery(
            vector=vector,
            vector_field_name="prompt_vector",
            return_fields=fields,
            num_results=num_results,
            return_score=True,
        )

        results = self._index.search(v.query, query_params=v.params)

        cache_hits = []
        for doc in results.docs:
            sim = similarity(doc.vector_distance)
            if sim > self.threshold:
                self._refresh_ttl(doc.id)
                cache_hits.append(doc.response)
        return cache_hits

    def store(
        self,
        prompt: str,
        response: str,
        vector: Optional[List[float]] = None,
        metadata: Optional[dict] = {},
    ) -> None:
        """Stores the specified key-value pair in the cache along with metadata.

        Args:
            prompt (str): The prompt to store.
            response (str): The response to store.
            vector (Optional[List[float]], optional): The vector to store. Defaults to None
            metadata (Optional[dict], optional): The metadata to store. Defaults to {}.
            key (Optional[str], optional): The key to store. Defaults to None.

        Raises:
            ValueError: If neither prompt nor vector is specified.
        """
        # Prepare LLMCache inputs
        key = self.hash_input(prompt)

        if not vector:
            # TODO - do we need to allow custom vector and custom key???
            vector = self._vectorizer.embed(prompt)  # type: ignore

        # TODO - what about schema mismatch???
        payload = {
            "id": key,
            "prompt_vector": array_to_buffer(vector),
            "prompt": prompt,
            "response": response
        }
        if metadata:
            payload.update(metadata)

        # Load LLMCache entry with TTL
        self._index.load([payload], ttl=self._ttl, key_field="id")

    def _refresh_ttl(self, key: str):
        """Refreshes the TTL for the specified key."""
        client = self._index.client
        if client:
            if self.ttl:
                client.expire(key, self.ttl)
        else:
            raise RuntimeError("LLMCache is not connected to a Redis instance.")

def similarity(distance: Union[float, str]) -> float:
    return 1 - float(distance)
