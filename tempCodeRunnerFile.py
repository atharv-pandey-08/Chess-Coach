
    return white_score.score(), None


def pov_score(cp_white, mate_white, color):
    """Converts a White-perspective (cp, mate) pair into `color`'s perspective."""
    if mate_white is not None:
        return None, (mate_white if color == chess.WHITE else -mate_white)
    if cp_white is None:
        return None, None
    return (cp_white if color == chess.WHITE else -cp_white), None


def effective_cp(cp, mate):
    """Folds mate scores into a single comparable centipawn-like number."""
    if mate is not None:
        return (100000 - abs(mate)) if mate > 0 else -(100000 - abs(mate))
    return cp if cp is not None else 0


def classify_loss(loss_cp):
    for threshold, label in CLASSIFICATION_THRESHOLDS:
        if loss_cp <= threshold:
            return label
    return "Blunder"

