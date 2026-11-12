"""Business rules that depend on runtime settings (so they can't live in the schema)."""
from app.config import Settings
from app.errors import InvalidInputError


def image_registry(image: str) -> str:
    first = image.split("/")[0]
    if "/" in image and ("." in first or ":" in first or first == "localhost"):
        return "docker.io" if first == "index.docker.io" else first
    return "docker.io"  # no explicit registry => Docker Hub


def check_image_allowed(image: str, settings: Settings) -> None:
    registry = image_registry(image)
    if registry not in settings.allowed_registries:
        raise InvalidInputError(f"Image registry '{registry}' is not allowed")


def check_replicas(replicas: int, settings: Settings) -> None:
    if replicas > settings.max_replicas:
        raise InvalidInputError(f"replicas may not exceed {settings.max_replicas}")
