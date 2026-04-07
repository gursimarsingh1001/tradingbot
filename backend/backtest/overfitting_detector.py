from __future__ import annotations


def detect_overfitting(train_score: float, test_score: float, threshold: float = 0.35) -> bool:
    if train_score <= 0:
        return False
    relative_drop = (train_score - test_score) / abs(train_score)
    return relative_drop > threshold
