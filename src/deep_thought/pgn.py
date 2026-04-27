import io
from dataclasses import dataclass

import chess
import chess.pgn

from deep_thought.stockfish import Eval


@dataclass(frozen=True)
class GameDate:
    yyyy: str
    mm: str
    dd: str


def parse_game(pgn_text: str) -> chess.pgn.Game:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("pgn does not contain a parseable game")
    return game


def extract_date(game: chess.pgn.Game) -> GameDate:
    """Pull date from PGN headers. Falls back to UTCDate / today as needed.

    Why: we partition annotated PGNs in S3 by date (matches tactician's expected
    layout `<YYYY>/<MM>/<DD>/<gameId>.pgn`).
    """
    raw = game.headers.get("UTCDate") or game.headers.get("Date") or ""
    parts = raw.split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        yyyy, mm, dd = parts
        return GameDate(yyyy=yyyy, mm=mm.zfill(2), dd=dd.zfill(2))
    raise ValueError(f"pgn missing or malformed Date header: {raw!r}")


def annotate_with_evals(game: chess.pgn.Game, evals: list[Eval]) -> str:
    """Return PGN text with %eval comments injected on each move node.

    Uses Lichess/chess.com convention: `[%eval 0.25]` for centipawns (in pawns,
    one decimal) or `[%eval #3]` for mate. Comment per node, side-to-move POV
    matches the position before the move (i.e. the player who just moved).
    """
    nodes = list(game.mainline())
    if len(nodes) != len(evals):
        raise ValueError(f"eval count {len(evals)} != move count {len(nodes)}")
    for node, ev in zip(nodes, evals, strict=True):
        node.comment = _format_eval_comment(ev)
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=True)
    return game.accept(exporter)


def _format_eval_comment(ev: Eval) -> str:
    if ev.mate is not None:
        return f"[%eval #{ev.mate}]"
    pawns = (ev.cp or 0) / 100
    return f"[%eval {pawns:+.2f}]"


def read_evals_from_pgn(game: chess.pgn.Game) -> list[Eval]:
    """Inverse of annotate_with_evals — extracts %eval values from comments.

    Used on the cache-hit path: when an annotated PGN already exists in S3, we
    re-derive classifications from these without re-running Stockfish.
    """
    out: list[Eval] = []
    for node in game.mainline():
        out.append(_parse_eval_comment(node.comment))
    return out


def _parse_eval_comment(comment: str) -> Eval:
    # Comments may contain other annotations ([%clk ...] etc.); find the eval token.
    start = comment.find("[%eval ")
    if start < 0:
        raise ValueError(f"node missing %eval comment: {comment!r}")
    end = comment.find("]", start)
    if end < 0:
        raise ValueError(f"unterminated %eval comment: {comment!r}")
    token = comment[start + len("[%eval ") : end].strip()
    if token.startswith("#"):
        return Eval(cp=None, mate=int(token[1:]))
    return Eval(cp=round(float(token) * 100), mate=None)
