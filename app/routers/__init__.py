from pydantic import ValidationError


def validation_message(exc: ValidationError) -> str:
    """Turn a Pydantic error into one short, friendly sentence."""
    err = exc.errors()[0]
    field = " ".join(str(p) for p in err.get("loc", []) if p != "body").replace("_", " ")
    msg = err.get("msg", "Invalid value").replace("Value error, ", "")
    return f"{field.capitalize()}: {msg}" if field else msg
