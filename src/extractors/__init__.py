from . import ap

REGISTRY = {
    "ap": ap,
}


def get(name):
    if name not in REGISTRY:
        raise ValueError(f"Unknown extractor '{name}'. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]
