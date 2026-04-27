import logging

import chess
import chess.engine
import chess.pgn

from deep_thought.classifier import classify
from deep_thought.config import Config
from deep_thought.messages import (
    AnalysisError,
    AnalysisRequest,
    AnalysisResponse,
    MoveAnalysis,
)
from deep_thought.pgn import (
    annotate_with_evals,
    extract_date,
    parse_game,
    read_evals_from_pgn,
)
from deep_thought.s3 import S3Client, S3Uri
from deep_thought.stockfish import Eval, analyse_position, open_engine
from deep_thought.summary import build_summary

log = logging.getLogger(__name__)


def run_analysis(req: AnalysisRequest, cfg: Config, s3: S3Client) -> AnalysisResponse:
    """Top-level orchestration. Catches expected errors and returns a `failed` response.

    Pipeline:
      1. Download raw PGN, parse, extract date.
      2. Build deterministic analyses-bucket key.
      3. If key exists → load cached annotated PGN, re-derive payload, done.
      4. Else → run Stockfish, build annotated PGN, upload, derive payload.
    """
    try:
        raw_pgn_text = s3.get_object_text(S3Uri.parse(req.s3_uri))
    except Exception as exc:
        log.exception("failed to fetch raw pgn")
        return _failed(req, "S3_FETCH_FAILED", str(exc))

    try:
        game = parse_game(raw_pgn_text)
        date = extract_date(game)
    except ValueError as exc:
        return _failed(req, "INVALID_PGN", str(exc))

    out_uri = s3.analysis_uri(req.game_id, date.yyyy, date.mm, date.dd)

    if s3.object_exists(out_uri):
        log.info("cache hit for game_id=%s; reusing %s", req.game_id, out_uri.to_str())
        cached_pgn = s3.get_object_text(out_uri)
        cached_game = parse_game(cached_pgn)
        evals_white_pov = read_evals_from_pgn(cached_game)
        return _build_response(req, cached_game, evals_white_pov, out_uri)

    log.info("cache miss for game_id=%s; running stockfish", req.game_id)
    try:
        with open_engine(cfg.stockfish) as engine:
            evals_white_pov = _analyse_full_game(engine, game, cfg.stockfish.depth)
    except chess.engine.EngineError as exc:
        log.exception("engine failed")
        return _failed(req, "ENGINE_FAILED", str(exc))

    annotated_pgn = annotate_with_evals(game, evals_white_pov)
    s3.put_object_text(out_uri, annotated_pgn)

    return _build_response(req, game, evals_white_pov, out_uri)


def _analyse_full_game(
    engine: chess.engine.SimpleEngine,
    game: chess.pgn.Game,
    depth: int,
) -> list[Eval]:
    """Return one White-POV eval per move, representing the position AFTER that move."""
    moves = list(game.mainline_moves())
    board = game.board()
    out: list[Eval] = []
    for move in moves:
        board.push(move)
        if board.is_checkmate():
            # After push, board.turn points to the side that was just mated.
            white_won = board.turn == chess.BLACK
            out.append(_mate_eval_white_pov(white_won=white_won))
            continue
        if board.is_stalemate() or board.is_insufficient_material():
            out.append(Eval(cp=0, mate=None))
            continue
        ev_stm, _ = analyse_position(engine, board, depth)
        out.append(_to_white_pov(ev_stm, board.turn))
    return out


def _to_white_pov(ev: Eval, side_to_move: chess.Color) -> Eval:
    if side_to_move == chess.WHITE:
        return ev
    return _flip(ev)


def _flip(ev: Eval) -> Eval:
    return Eval(
        cp=-ev.cp if ev.cp is not None else None,
        mate=-ev.mate if ev.mate is not None else None,
    )


def _mate_eval_white_pov(white_won: bool) -> Eval:
    """Synthetic eval for a position that's already checkmate.

    Stockfish doesn't analyse terminal positions; we fake the eval as mate=±1
    so cp/eval graphs and classification both behave sensibly.
    """
    return Eval(cp=None, mate=1 if white_won else -1)


def _build_response(
    req: AnalysisRequest,
    game: chess.pgn.Game,
    evals_white_pov: list[Eval],
    out_uri: S3Uri,
) -> AnalysisResponse:
    moves = list(game.mainline_moves())
    if len(moves) != len(evals_white_pov):
        return _failed(
            req,
            "INTERNAL_INVARIANT",
            f"move count {len(moves)} != eval count {len(evals_white_pov)}",
        )

    board = game.board()
    move_analyses: list[MoveAnalysis] = []
    evals_before_mover_pov: list[Eval] = []
    evals_after_mover_pov: list[Eval] = []
    plies: list[int] = []

    prev_eval_white_pov = Eval(cp=0, mate=None)  # initial position, before any move

    for i, move in enumerate(moves):
        ply = i + 1
        mover = board.turn
        san = board.san(move)
        uci = move.uci()

        ev_before_white = prev_eval_white_pov
        ev_after_white = evals_white_pov[i]

        ev_before_mover = ev_before_white if mover == chess.WHITE else _flip(ev_before_white)
        ev_after_mover = ev_after_white if mover == chess.WHITE else _flip(ev_after_white)

        evals_before_mover_pov.append(ev_before_mover)
        evals_after_mover_pov.append(ev_after_mover)
        plies.append(ply)

        move_analyses.append(
            MoveAnalysis(
                ply=ply,
                san=san,
                uci=uci,
                cp=ev_after_white.cp,
                mate=ev_after_white.mate,
                classification=classify(ev_before_mover, ev_after_mover),
            )
        )

        board.push(move)
        prev_eval_white_pov = ev_after_white

    summary = build_summary(evals_before_mover_pov, evals_after_mover_pov, plies)

    return AnalysisResponse(
        request_id=req.request_id,
        game_id=req.game_id,
        status="success",
        analysis_s3_uri=out_uri.to_str(),
        moves=move_analyses,
        summary=summary,
    )


def _failed(req: AnalysisRequest, code: str, message: str) -> AnalysisResponse:
    return AnalysisResponse(
        request_id=req.request_id,
        game_id=req.game_id,
        status="failed",
        error=AnalysisError(code=code, message=message),
    )
