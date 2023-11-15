from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field, validator
from redis.commands.search.field import (
    GeoField,
    NumericField,
    TagField,
    TextField,
    VectorField,
)
from typing_extensions import Literal

import numpy as np


# distance metrics
REDIS_DISTANCE_METRICS: List[str] = ["COSINE", "IP", "L2"]

# supported vector datatypes
REDIS_VECTOR_DTYPE_MAP: Dict[str, Any] = {
    "FLOAT32": np.float32,
    "FLOAT64": np.float64,
}

class BaseField(BaseModel):
    name: str = Field(...)
    sortable: Optional[bool] = False
    as_name: Optional[str] = None

class ExtraField(BaseModel):
    """Extra Field for non-indexed Metadata"""
    name: str = Field(...)

class TextFieldSchema(BaseField):
    weight: Optional[float] = 1
    no_stem: Optional[bool] = False
    phonetic_matcher: Optional[str] = None
    withsuffixtrie: Optional[bool] = False

    def as_field(self):
        return TextField(
            self.name,
            weight=self.weight,
            no_stem=self.no_stem,
            phonetic_matcher=self.phonetic_matcher,
            sortable=self.sortable,
            as_name=self.as_name,
        )


class TagFieldSchema(BaseField):
    separator: Optional[str] = ","
    case_sensitive: Optional[bool] = False

    def as_field(self):
        return TagField(
            self.name,
            separator=self.separator,
            case_sensitive=self.case_sensitive,
            sortable=self.sortable,
            as_name=self.as_name,
        )


class NumericFieldSchema(BaseField):
    def as_field(self):
        return NumericField(self.name, sortable=self.sortable, as_name=self.as_name)


class GeoFieldSchema(BaseField):
    def as_field(self):
        return GeoField(self.name, sortable=self.sortable, as_name=self.as_name)


class BaseVectorField(BaseModel):
    name: str = Field(...)
    dims: int = Field(...)
    algorithm: object = Field(...)
    datatype: str = Field(default="FLOAT32")
    distance_metric: str = Field(default="COSINE")
    initial_cap: Optional[int] = None
    as_name: Optional[str] = None

    @validator("algorithm", "datatype", "distance_metric", pre=True)
    def uppercase_strings(cls, v):
        return v.upper()

    def as_field(self) -> Dict[str, Any]:
        field_data = {
            "TYPE": self.datatype,
            "DIM": self.dims,
            "DISTANCE_METRIC": self.distance_metric,
        }
        if self.initial_cap is not None:  # Only include it if it's set
            field_data["INITIAL_CAP"] = self.initial_cap
        return field_data

    @validator("datatype", pre=True)
    def uppercase_and_check_dtype(cls, v: str) -> str:
        if v.upper() not in REDIS_VECTOR_DTYPE_MAP:
            raise ValueError(
                f"datatype must be one of {REDIS_VECTOR_DTYPE_MAP.keys()}. Got {v}"
            )
        return v.upper()

class FlatVectorField(BaseVectorField):
    algorithm: Literal["FLAT"] = "FLAT"
    block_size: Optional[int] = None

    def as_field(self):
        # grab base field params and augment with flat-specific fields
        field_data = super().as_field()
        if self.block_size is not None:
            field_data["BLOCK_SIZE"] = self.block_size
        return VectorField(self.name, self.algorithm, field_data, as_name=self.as_name)


class HNSWVectorField(BaseVectorField):
    algorithm: Literal["HNSW"] = "HNSW"
    m: int = Field(default=16)
    ef_construction: int = Field(default=200)
    ef_runtime: int = Field(default=10)
    epsilon: float = Field(default=0.01)

    def as_field(self):
        # grab base field params and augment with hnsw-specific fields
        field_data = super().as_field()
        field_data.update(
            {
                "M": self.m,
                "EF_CONSTRUCTION": self.ef_construction,
                "EF_RUNTIME": self.ef_runtime,
                "EPSILON": self.epsilon,
            }
        )
        return VectorField(self.name, self.algorithm, field_data, as_name=self.as_name)


class StorageType(Enum):
    HASH = "hash"
    JSON = "json"


class IndexModel(BaseModel):
    """
    Represents the schema for an index, including its name,
    optional prefix, and the storage type used.
    """

    name: str
    prefix: Optional[str] = "rvl"
    key_separator: Optional[str] = ":"
    storage_type: StorageType = "hash"

    # Force Pydantic use the value of the enum, not the enum itself
    class Config:
        use_enum_values = True

    @validator("name")
    def name_must_not_be_empty(cls, value):
        if not value:
            raise ValueError("name must not be empty")
        return value

    @validator("prefix", pre=True, always=True)
    def set_default_prefix(cls, v):
        # If prefix is None we need to fallback to empty string
        return v if v is not None else ""

    @validator("key_separator", pre=True, always=True)
    def set_default_key_separator(cls, v):
        # If key separator is None we need to fall back to a ":"
        return v if v is not None else ":"




class FieldsModel(BaseModel):
    tag: Optional[List[TagFieldSchema]] = None
    text: Optional[List[TextFieldSchema]] = None
    numeric: Optional[List[NumericFieldSchema]] = None
    geo: Optional[List[GeoFieldSchema]] = None
    vector: Optional[List[Union[FlatVectorField, HNSWVectorField]]] = None

    @property
    def is_empty(self) -> bool:
        return all(
            field is None for field in [self.tag, self.text, self.numeric, self.vector]
        )

class SchemaModel(BaseModel):
    index: IndexModel = Field(...)
    fields: FieldsModel = Field(...)

    @property
    def name(self) -> str:
        """The name of the Redis search index."""
        return self._index.name

    @property
    def prefix(self) -> str:
        """The optional key prefix that comes before a unique key value in forming a Redis key."""
        return self._index.prefix

    @property
    def key_separator(self) -> str:
        """The optional separator between a defined prefix and key value in forming a Redis key."""
        return self._index.key_separator

    @property
    def storage(self) -> BaseStorage:
        """The Storage class that handles all upserts and reads to/from the Redis instances."""
        return self._storage

    @property
    def storage_type(self) -> str:
        """The underlying storage type for the search index: hash or json."""
        return self._index.storage_type

    @property
    def index_fields(self):
        redis_fields = []
        for field_name in self.fields.__fields__.keys():
            field_group = getattr(self.fields, field_name)
            if field_group is not None:
                for field in field_group:
                    redis_fields.append(field.as_field())
        return redis_fields


def read_schema(file_path: str):
    fp = Path(file_path).resolve()
    if not fp.exists():
        raise FileNotFoundError(f"Schema file {file_path} does not exist")

    with open(fp, "r") as f:
        schema = yaml.safe_load(f)

    return SchemaModel(**schema)

def read_schema(
    index_schema: Optional[Union[Dict[str, List[Any]], str, os.PathLike]]
) -> Dict[str, List[Dict[str, Any]]]:
    """Reads in the index schema from a dict or yaml file.

    Check if it is a dict and return RedisModel otherwise, check if it's a path and
    read in the file assuming it's a yaml file and return a RedisModel
    """
    schema: Dict[str, Any] = {}
    if isinstance(index_schema, dict):
        schema = index_schema
    elif isinstance(index_schema, Path):
        with open(index_schema, "rb") as f:
            schema = yaml.safe_load(f)
    elif isinstance(index_schema, str):
        if Path(index_schema).resolve().is_file():
            with open(index_schema, "rb") as f:
                schema = yaml.safe_load(f)
        else:
            raise FileNotFoundError(f"index_schema file {index_schema} does not exist")
    else:
        raise TypeError(
            f"index_schema must be a dict, or path to a yaml file "
            f"Got {type(index_schema)}"
        )
    return schema


class MetadataSchemaGenerator:
    """
    A class to generate a schema for metadata, categorizing fields into text, numeric, and tag types.
    """

    def _test_numeric(self, value) -> bool:
        """
        Test if the given value can be represented as a numeric value.
        Args:
            value: The value to test.
        Returns:
            bool: True if the value can be converted to float, False otherwise.
        """
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False

    def _infer_type(self, value) -> Optional[str]:
        """
        Infer the type of the given value.
        Args:
            value: The value to infer the type of.
        Returns:
            Optional[str]: The inferred type of the value, or None if the type is unrecognized or the value is empty.
        """
        if value is None or value == "":
            return None
        elif self._test_numeric(value):
            return "numeric"
        elif isinstance(value, (list, set, tuple)):
            if all(isinstance(v, str) for v in value):
                return "tag"
        elif isinstance(value, str):
            return "text"
        else:
            return "unknown"

    def generate(
        self, metadata: Dict[str, Any], strict: Optional[bool] = False
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generate a schema from the provided metadata.
        This method categorizes each metadata field into text, numeric, or tag types based on the field values.
        It also allows forcing strict type determination by raising an exception if a type cannot be inferred.
        Args:
            metadata: The metadata dictionary to generate the schema from.
            strict: If True, the method will raise an exception for fields where the type cannot be determined.
        Returns:
            Dict[str, List[Dict[str, Any]]]: A dictionary with keys 'text', 'numeric', and 'tag', each mapping to a list of field schemas.
        Raises:
            ValueError: If the force parameter is True and a field's type cannot be determined.
        """
        result: Dict[str, List[Dict[str, Any]]] = {"text": [], "numeric": [], "tag": []}

        for key, value in metadata.items():
            field_type = self._infer_type(value)

            if field_type in ["unknown", None]:
                if strict:
                    raise ValueError(
                        f"Unable to determine field type for key '{key}' with value '{value}'"
                    )
                print(
                    f"Warning: Unable to determine field type for key '{key}' with value '{value}'"
                )
                continue

            # Extract the field class with defaults
            field_class = {
                "text": TextFieldSchema,
                "tag": TagFieldSchema,
                "numeric": NumericFieldSchema,
            }.get(field_type)

            if field_class:
                result[field_type].append(field_class(name=key).dict(exclude_none=True))  # type: ignore

        return result