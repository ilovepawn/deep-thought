import math

import chess

from deep_thought.classifier import eval_to_cp
from deep_thought.messages import SideSummary, Summary
from deep_thought.stockfish import Eval


def _accuracy_from_cp_loss(cp_loss: int) -> float:
    """Lichess-style win-percent → accuracy approximation, bounded to [0, 100]."""
    cp_loss = max(0, cp_loss)
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * cp_loss) - 3.1669))


# Cap individual cp_loss to keep ACPL meaningful when a move allows mate.
# Without this, a single mate-allowing blunder dominates the average and ACPL
# values become uninterpretable. Lichess uses a similar cap.
_CP_LOSS_CAP = 1000


def build_summary(
    evals_before_mover_pov: list[Eval],
    evals_after_mover_pov: list[Eval],
    plies: list[int],
) -> Summary:
    """Compute per-side accuracy and ACPL.

    Both eval lists must be from the moving side's POV at that ply, so
    cp_loss is positive when the move was bad.
    """
    white_losses: list[int] = []
    black_losses: list[int] = []

    for ev_b, ev_a, ply in zip(evals_before_mover_pov, evals_after_mover_pov, plies, strict=True):
        raw_loss = max(0, eval_to_cp(ev_b) - eval_to_cp(ev_a))
        cp_loss = min(raw_loss, _CP_LOSS_CAP)
        side = chess.WHITE if ply % 2 == 1 else chess.BLACK
        (white_losses if side == chess.WHITE else black_losses).append(cp_loss)

    return Summary(white=_side(white_losses), black=_side(black_losses))


def _side(losses: list[int]) -> SideSummary:
    if not losses:
        return SideSummary(accuracy=100.0, acpl=0)
    accs = [_accuracy_from_cp_loss(loss) for loss in losses]
    avg_accuracy = sum(accs) / len(accs)
    acpl = round(sum(losses) / len(losses))
    return SideSummary(accuracy=round(avg_accuracy, 1), acpl=acpl)
