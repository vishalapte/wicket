def print___VERB_ID__(result: __RESULT__) -> None:
    """``__NOUN__ __VERB__`` as text, one ``key: value`` line per field."""
    for key, value in result.items():
        print(f"{key}: {value}")
