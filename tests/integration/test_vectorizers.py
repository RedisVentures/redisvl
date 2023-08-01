import os
import pytest

from redisvl.vectorize import HuggingfaceVectorizer, OpenAIVectorizer

@pytest.fixture
def openai_key():
    return os.getenv("OPENAI_API_KEY")

@pytest.fixture(params=[HuggingfaceVectorizer, OpenAIVectorizer])
def vectorizer(request, openai_key):
    # Here we use actual models for integration test
    if request.param == HuggingfaceVectorizer:
        return request.param(model="sentence-transformers/all-mpnet-base-v2")
    elif request.param == OpenAIVectorizer:
        return request.param(
            model="text-embedding-ada-002", api_config={"api_key": openai_key}
        )


def test_vectorizer_embed(vectorizer):
    text = "This is a test sentence."
    embedding = vectorizer.embed(text)

    assert isinstance(embedding, list)
    assert len(embedding) == vectorizer.dims


def test_vectorizer_embed_many(vectorizer):
    texts = ["This is the first test sentence.", "This is the second test sentence."]
    embeddings = vectorizer.embed_many(texts)

    assert isinstance(embeddings, list)
    assert len(embeddings) == len(texts)
    assert all(
        isinstance(emb, list) and len(emb) == vectorizer.dims for emb in embeddings
    )


@pytest.fixture(params=[OpenAIVectorizer])
def avectorizer(request, openai_key):
    # Here we use actual models for integration test
    if request.param == OpenAIVectorizer:
        return request.param(
            model="text-embedding-ada-002", api_config={"api_key": openai_key}
        )


@pytest.mark.asyncio
async def test_vectorizer_aembed(avectorizer):
    text = "This is a test sentence."
    embedding = await avectorizer.aembed(text)

    assert isinstance(embedding, list)
    assert len(embedding) == avectorizer.dims


@pytest.mark.asyncio
async def test_vectorizer_aembed_many(avectorizer):
    texts = ["This is the first test sentence.", "This is the second test sentence."]
    embeddings = await avectorizer.aembed_many(texts)

    assert isinstance(embeddings, list)
    assert len(embeddings) == len(texts)
    assert all(
        isinstance(emb, list) and len(emb) == avectorizer.dims for emb in embeddings
    )