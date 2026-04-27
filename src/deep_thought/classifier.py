from deep_thought.messages import Classification
from deep_thought.stockfish import Eval

_MATE_CP = 100_000


def eval_to_cp(ev: Eval) -> int:
    """Collapse mate/cp into a single signed cp scalar for delta math."""
    if ev.mate is not None:
        return _MATE_CP if ev.mate > 0 else -_MATE_CP
    return ev.cp or 0


def classify(eval_before: Eval, eval_after: Eval) -> Classification:
    """Map a single move to a quality bucket using cp-loss thresholds.

    Both evals are from the moving side's POV (so a drop = bad move).
    Why not also check `move == engine_best`: classification needs to be
    re-derivable from the annotated PGN alone (cache-hit path doesn't re-run
    Stockfish). cp-loss == 0 already covers the "matched engine choice or
    equal alternative" case, matching Lichess's convention.
    """
    cp_loss = eval_to_cp(eval_before) - eval_to_cp(eval_after)
    if cp_loss <= 0:
        return "best"
    if cp_loss < 50:
        return "good"
    if cp_loss < 100:
        return "inaccuracy"
    if cp_loss < 300:
        return "mistake"
    return "blunder"
