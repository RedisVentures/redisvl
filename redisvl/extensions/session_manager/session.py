import hashlib
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime

from redis import Redis

from redisvl.index import SearchIndex
from redisvl.query import FilterQuery, RangeQuery
from redisvl.query.filter import Tag, Num
from redisvl.redis.utils import array_to_buffer
from redisvl.schema.schema import IndexSchema
from redisvl.utils.vectorize import BaseVectorizer, HFTextVectorizer

class SessionManager:
    def __init__(self,
                 name: str,
                 session_id: str,
                 user_id: str,
                 application_id: str,
                 scope: str = 'session',
                 prefix: Optional[str] = None,
                 vectorizer: Optional[BaseVectorizer] = None,
                 distance_threshold: float = 0.3,
                 redis_client: Optional[Redis] = None,
                 preamble: str = None
                 ):
        """ Initialize session memory with index

        Session Manager stores the current and previous user text prompts and
        LLM responses to allow for enriching future prompts with session
        context. Session history is stored in prompt:response pairs referred to
        as exchanges.

        Args:
            name str: The name of the session manager index.
            session_id str: Tag to be added to entries to link to a specific
                session.
            user_id str: Tag to be added to entries to link to a specific user.
            application_id str: Tag to be added to entries to link to a
                specific application.
            scope str: The level of access this session manager can retrieve
                data at. Must be one of 'session', 'user', 'application'.
            prefix Optional[str]: Prefix for the keys for this session data.
                Defaults to None and will be replaced with the index name.
            vectorizer Vectorizer: The vectorizer to create embeddings with.
            distance_threshold float: The maximum semantic distance to be
                included in the context. Defaults to 0.3.
            redis_client Optional[Redis]: A Redis client instance. Defaults to
                None.
            preamble str: System level prompt to be included in all context.


        The proposed schema will support a single combined vector embedding
        constructed from the prompt & response in a single string.

        """
        prefix = prefix or name
        self._session_id = session_id
        self._user_id = user_id
        self._application_id = application_id
        self._scope = scope
        self.set_preamble(preamble)

        if vectorizer is None:
            self._vectorizer = HFTextVectorizer(
                model="sentence-transformers/all-mpnet-base-v2"
            )

        self.set_distance_threshold(distance_threshold)

        schema = IndexSchema.from_dict({"index": {"name": name, "prefix": prefix}})

        schema.add_fields(
            [
                {"name": "prompt", "type": "text"},
                {"name": "response", "type": "text"},
                {"name": "timestamp", "type": "numeric"},
                {"name": "session_id", "type": "tag"},
                {"name": "user_id", "type": "tag"},
                {"name": "application_id", "type": "tag"},
                {"name": "count", "type": "numeric"},
                {"name": "token_count", "type": "numeric"},
                {
                    "name": "combined_vector_field",
                    "type": "vector",
                    "attrs": {
                        "dims": self._vectorizer.dims,
                        "datatype": "float32",
                        "distance_metric": "cosine",
                        "algorithm": "flat",
                    },
                 },
            ]
        )

        self._index = SearchIndex(schema=schema)

        if redis_client:
            self._index.set_client(redis_client)
            self._redis_client = redis_client
        else:
            self._index.connect(redis_url="redis://localhost:6379")
            self._redis_client = Redis(decode_responses=True)

        self._index.create(overwrite=False)

        self._tag_filter = Tag("application_id") == self._application_id
        if self._scope == 'user':
            user_filter = Tag("user_id") == self._user_id
            self._tag_filter = self._tag_filter & user_filter
        if self._scope == 'session':
            session_filter = Tag("session_id") == self._session_id
            user_filter = Tag("user_id") == self._user_id
            self._tag_filter = self._tag_filter & user_filter & session_filter


    def clear(self) -> None:
        """ Clears the chat session history. """
        with self._index.client.pipeline(transaction=False) as pipe:
            for key in self._index.client.scan_iter(match=f"{self._index.prefix}:*"):
                pipe.delete(key)
            pipe.execute()


    def delete(self) -> None:
        """ Clear all conversation keys and remove the search index. """
        self._index.delete(drop=True)


    def fetch_context(
        self,
        prompt: str,
        as_text: bool = False,
        top_k: int = 3,
        fall_back: bool = False
        ) -> Union[List[str], List[Dict[str,str]]]:
        """ Searches the chat history for information semantically related to
        the specified prompt.

        This method uses vector similarity search with a text prompt as input.
        It checks for semantically similar prompt:response pairs and fetches
        the top k most relevant previous prompt:response pairs to include as
        context to the next LLM call.

        Args:
            prompt str: The text prompt to search for in session memory
            as_text bool: Whether to return the prompt:response pairs as text
            or as JSON
            top_k int: The number of previous exchanges to return. Default is 3.
            fallback bool: Whether to drop back to conversation history if no
                relevant context is found.

        Returns:
            Union[List[str], List[Dict[str,str]]: Either a list of strings, or a
            list of prompts and responses in JSON containing the most relevant
        """

        return_fields = [
            "session_id",
            "user_id",
            "application_id",
            "count",
            "prompt",
            "response",
            "timestamp",
            "combined_vector_field",
        ]

        query = RangeQuery(
            vector=self._vectorizer.embed(prompt),
            vector_field_name="combined_vector_field",
            return_fields=return_fields,
            distance_threshold=self._distance_threshold,
            num_results=top_k,
            return_score=True,
            filter_expression=self._tag_filter
        )
        hits = self._index.query(query)

        # if we don't find semantic matches fallback to returning recent context
        if not hits and fall_back:
            return self.conversation_history(as_text=as_text, top_k=top_k)
        return self._format_context(hits, as_text)


    def conversation_history(
        self,
        as_text: bool = False,
        top_k: int = 3,
        raw = False
        ) -> Union[List[str], List[Dict[str,str]]]:
        """ Retreive the conversation history in sequential order.

        Args:
            as_text bool: Whether to return the conversation as a single string,
                          or list of alternating prompts and responses.
            top_k int: The number of previous exchanges to return. Default is 3
            raw bool: Whether to return the full Redis hash entry or just the
                prompt and response
        Returns:
            Union[str, List[str]]: A single string transcription of the session
                                   or list of strings if as_text is false.
        """
        return_fields = [
            "session_id",
            "user_id",
            "application_id",
            "count",
            "prompt",
            "response",
            "timestamp",
        ]

        count_key = ":".join([self._application_id, self._user_id, self._session_id, "count"])
        count = self._redis_client.get(count_key) or 0
        last_k_filter = Num("count") > int(count) - top_k
        combined = self._tag_filter & last_k_filter

        query = FilterQuery(
                return_fields=return_fields,
                filter_expression=combined
        )
        hits = self._index.query(query)
        return self._format_context(hits, as_text)


    def _format_context(
        self,
        hits: List[Dict[str: Any]],
        as_text: bool
        ) -> Union[List[str], List[Dict[str,str]]]:
        if hits:
            hits.sort(key=lambda x: x['timestamp']) # TODO move sorting to query.py

        if as_text:
            statements = [self._preamble["_content"]]
            for hit in hits:
                statements.append(hit["prompt"])
                statements.append(hit["response"])
        else:
            statements = [self._preamble]
            for hit in hits:
                statements.append({"role": "_user", "_content": hit["prompt"]})
                statements.append({"role": "_llm", "_content": hit["response"]})
        return statements


    @property
    def distance_threshold(self):
        return self._distance_threshold


    def set_distance_threshold(self, threshold):
        self._distance_threshold = threshold


    def store(self, exchange: Tuple[str, str]) -> str:
        """ Insert a prompt:response pair into the session memory. A timestamp
            is associated with each exchange so that they can be later sorted
            in sequential ordering after retrieval.

            Args:
                exchange Tuple[str, str]: The user prompt and corresponding LLM
                    response.

            Returns:
                str: The Redis key for the entry added to the database.
        """
        count_key = ":".join([self._application_id, self._user_id, self._session_id, "count"])
        count = self._redis_client.incr(count_key)
        vector = self._vectorizer.embed(exchange[0] + exchange[1])
        timestamp = int(datetime.now().timestamp())
        payload = {
                "id": self.hash_input(exchange[0]+str(timestamp)),
                "prompt": exchange[0],
                "response": exchange[1],
                "timestamp": timestamp,
                "session_id": self._session_id,
                "user_id": self._user_id,
                "application_id": self._application_id,
                "count": count,
                "token_count": 1, #TODO get actual token count
                "combined_vector_field": array_to_buffer(vector)
        }
        key = self._index.load(data=[payload])
        return key


    def set_preamble(self, prompt: str) -> None:
        """ Add a preamble statement to the the begining of each session to be
            included in each subsequent LLM call.
        """
        self._preamble = {"role": "_preamble", "_content": prompt}
        # TODO store this in Redis with asigned scope?


    def hash_input(self, prompt: str):
        """Hashes the input using SHA256."""
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
