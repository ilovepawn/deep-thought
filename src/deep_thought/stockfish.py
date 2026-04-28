from contextlib import contextmanager
from dataclasses import dataclass

import chess
import chess.engine

from deep_thought import metrics
from deep_thought.config import StockfishConfig


@dataclass(frozen=True)
class Eval:
    """Engine evaluation from the side-to-move's perspective.

    cp is in centipawns; mate is set when forced mate is detected (positive = mating side wins).
    Exactly one of (cp, mate) is set.
    """

    cp: int | None
    mate: int | None

    @classmethod
    def from_pov_score(cls, score: chess.engine.PovScore, pov: chess.Color) -> "Eval":
        rel = score.pov(pov)
        mate = rel.mate()
        if mate is not None:
            return cls(cp=None, mate=mate)
        cp = rel.score()
        return cls(cp=cp if cp is not None else 0, mate=None)


@contextmanager
def open_engine(cfg: StockfishConfig):
    engine = chess.engine.SimpleEngine.popen_uci(cfg.path)
    try:
        engine.configure({"Threads": cfg.threads, "Hash": cfg.hash_mb})
        yield engine
    finally:
        engine.quit()


def analyse_position(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    depth: int,
) -> tuple[Eval, chess.Move | None]:
    """Return (eval-from-side-to-move, engine's best move) for the current position."""
    with metrics.stockfish_analyse_seconds.time():
        info = engine.analyse(board, chess.engine.Limit(depth=depth))
    score = info["score"]
    eval_ = Eval.from_pov_score(score, board.turn)
    pv = info.get("pv") or []
    best = pv[0] if pv else None
    return eval_, best
