def coverage(hit: set[str], total: set[str]) -> float:
    return len(hit & total) / len(total) if total else 1.0
