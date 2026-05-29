"""Build compact planner audit payloads for MCTS and beam search."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from mcts.beam_search_planner import BeamPath
from pipeline.confluence_report import ConfluenceReport
from pipeline.schemas import TradePlan

if TYPE_CHECKING:
    from mcts.mcts_planner import MCTSNode


def summarize_mcts_node(node: MCTSNode, max_children: int = 8) -> dict[str, Any]:
    children = sorted(node.children, key=lambda c: (-c.visits, -c.avg_value))[:max_children]
    return {
        "action": node.action,
        "level": node.level,
        "visits": node.visits,
        "avg_value": round(node.avg_value, 4),
        "children": [
            {
                "action": c.action,
                "level": c.level,
                "visits": c.visits,
                "avg_value": round(c.avg_value, 4),
            }
            for c in children
        ],
    }


def build_mcts_audit(
    root: MCTSNode,
    path: list[MCTSNode],
    *,
    rollouts: int,
    exploration_c: float,
    route_reason: str = "",
) -> dict[str, Any]:
    path_nodes = path[1:] if len(path) > 1 else []
    path_state = path[-1].state if path else {}
    l1_alts = sorted(root.children, key=lambda c: (-c.visits, -c.avg_value))

    return {
        "planner": "mcts",
        "route_reason": route_reason,
        "rollouts": rollouts,
        "exploration_c": exploration_c,
        "root_visits": root.visits,
        "root_avg_value": round(root.avg_value, 4),
        "best_path": [n.action for n in path_nodes],
        "path_state": dict(path_state),
        "chosen_path_value": round(path[-1].avg_value, 4) if len(path) > 1 else 0.0,
        "alternative_paths": [summarize_mcts_node(c) for c in l1_alts],
        "search_stats": {
            "path_depth": len(path_nodes),
            "total_l1_children": len(root.children),
            "levels": {
                f"L{i}": n.action for i, n in enumerate(path_nodes, start=1)
            },
        },
    }


def build_beam_audit(
    beam: list[BeamPath],
    *,
    beam_width: int,
    route_reason: str = "",
) -> dict[str, Any]:
    return {
        "planner": "beam",
        "route_reason": route_reason,
        "beam_width": beam_width,
        "candidate_count": len(beam),
        "best_path": [beam[0].action] if beam else [],
        "alternative_paths": [
            {
                "rank": i,
                "action": p.action,
                "direction": p.direction,
                "score": round(p.score, 4),
                "p_success": round(p.p_success, 4),
                "ev_dollars": round(p.ev_dollars, 2),
                "entry_condition": p.entry_condition,
                "stop_condition": p.stop_condition,
                "target_condition": p.target_condition,
                "notes": p.notes,
            }
            for i, p in enumerate(beam)
        ],
        "search_stats": {
            "top_score": round(beam[0].score, 4) if beam else 0.0,
            "top_ev": round(beam[0].ev_dollars, 2) if beam else 0.0,
        },
    }


def envelope_audit(
    audit: dict[str, Any],
    *,
    snapshot_id: Optional[str],
    symbol: str,
    timeframe: str,
    confluence: ConfluenceReport,
    plan: TradePlan,
    p_success: float,
    ev_dollars: float,
    signal_rank: int,
) -> dict[str, Any]:
    """Merge search audit with signal context for DB insert."""
    full = {
        **audit,
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "confluence_score": confluence.confluence_score,
        "conflict_score": confluence.conflict_score,
        "news_aligned": confluence.news_aligned,
        "p_success": p_success,
        "ev_dollars": ev_dollars,
        "signal_rank": signal_rank,
        "chosen_action": plan.action.value if hasattr(plan.action, "value") else str(plan.action),
        "plan_ev": plan.plan_ev,
        "plan_confidence": plan.plan_confidence,
        "plan_notes": plan.plan_notes,
    }
    return {
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "planner": audit.get("planner"),
        "route_reason": audit.get("route_reason"),
        "confluence_score": confluence.confluence_score,
        "conflict_score": confluence.conflict_score,
        "news_aligned": confluence.news_aligned,
        "p_success": p_success,
        "ev_dollars": ev_dollars,
        "signal_rank": signal_rank,
        "chosen_action": full["chosen_action"],
        "plan_ev": plan.plan_ev,
        "plan_confidence": plan.plan_confidence,
        "rollouts": audit.get("rollouts"),
        "exploration_c": audit.get("exploration_c"),
        "root_value": audit.get("root_avg_value") or audit.get("search_stats", {}).get("top_score"),
        "best_path": audit.get("best_path"),
        "path_state": audit.get("path_state"),
        "alternative_paths": audit.get("alternative_paths"),
        "search_stats": audit.get("search_stats"),
        "full_audit": full,
    }
