"""Client for calling LLM APIs (OpenAI compatible)."""

from collections.abc import Mapping
import json
import re
from typing import Any, Literal, overload, cast
import httpx
from paper_engine.storage.database import get_connection

STRUCTURED_OUTPUT_MAX_ATTEMPTS = 3


class LLMStructuredOutputError(ValueError):
    """Raised when an LLM response cannot satisfy the requested schema."""


class _RetryableStructuredOutputError(LLMStructuredOutputError):
    """Internal marker for structured output failures worth retrying."""


async def get_llm_config() -> dict[str, str]:
    """Retrieve LLM configuration from database."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM app_state WHERE key LIKE 'llm_%'").fetchall()
        config = {row["key"]: row["value"] for row in rows}
        return {
            "api_key": config.get("llm_api_key", ""),
            "base_url": config.get("llm_base_url", "https://api.openai.com/v1"),
            "model": config.get("llm_model", "gpt-4o"),
        }
    finally:
        conn.close()

@overload
async def call_llm(
    system_prompt: str,
    user_prompt: str,
    json_mode: Literal[True] = True,
) -> dict[str, Any]:
    ...


@overload
async def call_llm(
    system_prompt: str,
    user_prompt: str,
    json_mode: Literal[False],
) -> str:
    ...


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    json_mode: bool = True,
) -> dict[str, Any] | str:
    """Call an OpenAI-compatible LLM API."""
    config = await get_llm_config()
    _ensure_llm_config_is_usable(config)

    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
    }
    
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    data = await _post_chat_completion(config, payload)
    content = data["choices"][0]["message"]["content"]
    if json_mode:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM response is not a JSON object.")
        return cast(dict[str, Any], parsed)
    return str(content)


async def call_llm_schema(
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    provider_capabilities: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Call an LLM API and require a response matching a JSON schema."""
    config = await get_llm_config()
    _ensure_llm_config_is_usable(config)

    use_json_schema = _provider_supports_json_schema(provider_capabilities)
    last_error: Exception | None = None

    for _attempt in range(STRUCTURED_OUTPUT_MAX_ATTEMPTS):
        payload = {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": _schema_response_format(schema_name, schema)
            if use_json_schema
            else {"type": "json_object"},
        }

        try:
            data = await _post_chat_completion(config, payload)
        except httpx.HTTPStatusError as exc:
            if (
                use_json_schema
                and _provider_allows_json_mode_fallback(provider_capabilities)
                and _looks_like_schema_format_rejection(exc, config["base_url"])
            ):
                use_json_schema = False
                last_error = exc
                continue
            raise

        try:
            parsed = _parse_structured_message(data)
            _validate_json_schema(parsed, schema)
            return parsed
        except _RetryableStructuredOutputError as exc:
            last_error = exc

    error_detail = f": {last_error}" if last_error is not None else ""
    raise LLMStructuredOutputError(
        f"LLM response did not satisfy schema after "
        f"{STRUCTURED_OUTPUT_MAX_ATTEMPTS} attempts{error_detail}"
    ) from last_error


def _ensure_llm_config_is_usable(config: Mapping[str, str]) -> None:
    base_url = config["base_url"]
    if not config["api_key"] and "localhost" not in base_url and "127.0.0.1" not in base_url:
        raise ValueError("LLM API Key is missing. Please configure it in settings.")


async def _post_chat_completion(
    config: Mapping[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
    }
    api_key = config["api_key"].strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{config['base_url'].rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("LLM response envelope is not a JSON object.")
        return cast(dict[str, Any], data)


def _schema_response_format(
    schema_name: str, schema: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "strict": True,
            "schema": schema,
        },
    }


def _provider_supports_json_schema(
    provider_capabilities: Mapping[str, bool] | None,
) -> bool:
    explicit = _capability_value(
        provider_capabilities, "json_schema", "supports_json_schema"
    )
    if explicit is not None:
        return explicit
    return True


def _provider_allows_json_mode_fallback(
    provider_capabilities: Mapping[str, bool] | None,
) -> bool:
    explicit = _capability_value(
        provider_capabilities,
        "json_mode_fallback",
        "allow_json_mode_fallback",
    )
    if explicit is not None:
        return explicit
    return True


def _capability_value(
    provider_capabilities: Mapping[str, bool] | None, *keys: str
) -> bool | None:
    if provider_capabilities is None:
        return None
    for key in keys:
        if key in provider_capabilities:
            return provider_capabilities[key]
    return None


def _looks_like_schema_format_rejection(
    exc: httpx.HTTPStatusError, base_url: str
) -> bool:
    response = exc.response
    if response.status_code not in {400, 404, 422}:
        return False
    if _is_local_base_url(base_url):
        return True
    error_text = response.text.lower()
    return any(
        marker in error_text
        for marker in ("json_schema", "response_format", "schema")
    )


def _is_local_base_url(base_url: str) -> bool:
    lowered = base_url.lower()
    return any(
        host in lowered for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
    )


def _parse_structured_message(data: Mapping[str, Any]) -> dict[str, Any]:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise _RetryableStructuredOutputError(
            "LLM response missing choices[0].message"
        ) from exc

    if not isinstance(message, Mapping):
        raise _RetryableStructuredOutputError("LLM message is not an object")

    refusal = message.get("refusal")
    if refusal:
        raise _RetryableStructuredOutputError(f"model refusal: {refusal}")

    content = message.get("content")
    if not isinstance(content, str):
        raise _RetryableStructuredOutputError("LLM message content is not text")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise _RetryableStructuredOutputError("LLM response is invalid JSON") from exc

    if not isinstance(parsed, dict):
        raise _RetryableStructuredOutputError("LLM response is not a JSON object")
    return cast(dict[str, Any], parsed)


def _validate_json_schema(value: Any, schema: Mapping[str, Any]) -> None:
    try:
        _validate_schema_node(value, schema, schema, "$")
    except ValueError as exc:
        raise _RetryableStructuredOutputError(
            f"schema validation failed: {exc}"
        ) from exc


def _validate_schema_node(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: str,
) -> None:
    if "$ref" in schema:
        ref_schema = _resolve_schema_ref(root_schema, schema["$ref"])
        merged_schema = dict(ref_schema)
        merged_schema.update({key: item for key, item in schema.items() if key != "$ref"})
        _validate_schema_node(value, merged_schema, root_schema, path)
        return

    if "anyOf" in schema:
        _validate_schema_union(value, schema["anyOf"], root_schema, path, "anyOf")
        return
    if "oneOf" in schema:
        _validate_schema_union(value, schema["oneOf"], root_schema, path, "oneOf")
        return
    if "allOf" in schema:
        subschemas = schema["allOf"]
        if not isinstance(subschemas, list):
            raise ValueError(f"{path}: allOf must be a list")
        for subschema in subschemas:
            if not isinstance(subschema, Mapping):
                raise ValueError(f"{path}: allOf item must be a schema object")
            _validate_schema_node(value, subschema, root_schema, path)

    if "enum" in schema:
        allowed_values = schema["enum"]
        if not isinstance(allowed_values, list):
            raise ValueError(f"{path}: enum must be a list")
        if value not in allowed_values:
            raise ValueError(f"{path}: value is not one of the allowed enum values")

    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path}: value does not match const")

    expected_types = _schema_types(schema.get("type"))
    if expected_types and not any(_matches_json_type(value, expected) for expected in expected_types):
        type_names = ", ".join(expected_types)
        raise ValueError(f"{path}: expected type {type_names}")

    if isinstance(value, dict):
        _validate_object_schema(value, schema, root_schema, path)
    elif isinstance(value, list):
        _validate_array_schema(value, schema, root_schema, path)
    elif isinstance(value, str):
        _validate_string_schema(value, schema, path)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        _validate_number_schema(value, schema, path)


def _validate_schema_union(
    value: Any,
    union_schema: Any,
    root_schema: Mapping[str, Any],
    path: str,
    keyword: str,
) -> None:
    if not isinstance(union_schema, list):
        raise ValueError(f"{path}: {keyword} must be a list")
    errors: list[str] = []
    match_count = 0
    for subschema in union_schema:
        if not isinstance(subschema, Mapping):
            raise ValueError(f"{path}: {keyword} item must be a schema object")
        try:
            _validate_schema_node(value, subschema, root_schema, path)
            match_count += 1
        except ValueError as exc:
            errors.append(str(exc))
    if keyword == "oneOf" and match_count == 1:
        return
    if keyword == "anyOf" and match_count >= 1:
        return
    joined_errors = "; ".join(errors)
    raise ValueError(f"{path}: value does not match {keyword}: {joined_errors}")


def _resolve_schema_ref(
    root_schema: Mapping[str, Any], ref: Any
) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ValueError(f"unsupported schema ref {ref}")

    current: Any = root_schema
    for part in ref.removeprefix("#/").split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or key not in current:
            raise ValueError(f"unresolved schema ref {ref}")
        current = current[key]

    if not isinstance(current, Mapping):
        raise ValueError(f"schema ref {ref} does not resolve to an object")
    return current


def _schema_types(type_definition: Any) -> tuple[str, ...]:
    if isinstance(type_definition, str):
        return (type_definition,)
    if isinstance(type_definition, list) and all(
        isinstance(item, str) for item in type_definition
    ):
        return tuple(type_definition)
    if type_definition is None:
        return ()
    raise ValueError("schema type must be a string or string list")


def _matches_json_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    raise ValueError(f"unsupported schema type {expected_type}")


def _validate_object_schema(
    value: dict[Any, Any],
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: str,
) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError(f"{path}: properties must be an object")

    required = schema.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(item, str) for item in required
    ):
        raise ValueError(f"{path}: required must be a string list")

    missing = [field_name for field_name in required if field_name not in value]
    if missing:
        raise ValueError(f"{path}: missing required field {', '.join(missing)}")

    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{path}: object keys must be strings")
        if key in properties:
            subschema = properties[key]
            if not isinstance(subschema, Mapping):
                raise ValueError(f"{path}.{key}: property schema must be an object")
            _validate_schema_node(
                item,
                subschema,
                root_schema,
                f"{path}.{key}",
            )
            continue

        additional_properties = schema.get("additionalProperties", True)
        if additional_properties is False:
            raise ValueError(f"{path}: unexpected field {key}")
        if isinstance(additional_properties, Mapping):
            _validate_schema_node(
                item,
                additional_properties,
                root_schema,
                f"{path}.{key}",
            )


def _validate_array_schema(
    value: list[Any],
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: str,
) -> None:
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        raise ValueError(f"{path}: expected at least {min_items} items")

    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        raise ValueError(f"{path}: expected at most {max_items} items")

    items_schema = schema.get("items")
    if items_schema is None:
        return
    if not isinstance(items_schema, Mapping):
        raise ValueError(f"{path}: items must be a schema object")

    for index, item in enumerate(value):
        _validate_schema_node(item, items_schema, root_schema, f"{path}[{index}]")


def _validate_string_schema(
    value: str, schema: Mapping[str, Any], path: str
) -> None:
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        raise ValueError(f"{path}: expected string length >= {min_length}")

    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        raise ValueError(f"{path}: expected string length <= {max_length}")

    pattern = schema.get("pattern")
    if isinstance(pattern, str) and re.search(pattern, value) is None:
        raise ValueError(f"{path}: string does not match pattern")


def _validate_number_schema(
    value: int | float, schema: Mapping[str, Any], path: str
) -> None:
    minimum = schema.get("minimum")
    if isinstance(minimum, int | float) and value < minimum:
        raise ValueError(f"{path}: expected number >= {minimum}")

    maximum = schema.get("maximum")
    if isinstance(maximum, int | float) and value > maximum:
        raise ValueError(f"{path}: expected number <= {maximum}")
