"""S21 per-system adapters. Each implements `run(query, seed) -> RunResult`."""
from .base import Adapter, df_to_result, is_decline_text
from .network import NetworkAdapter
from .planner_upfront import PlannerUpfrontAdapter
from .react import ReactAdapter
from .code_agent import CodeAgentAdapter
from .text_to_sql import TextToSQLAdapter

ADAPTER_CLASSES = {
    "network":      NetworkAdapter,
    "planner":      PlannerUpfrontAdapter,
    "react":        ReactAdapter,
    "code_agent":   CodeAgentAdapter,
    "text_to_sql":  TextToSQLAdapter,
}

__all__ = [
    "Adapter", "df_to_result", "is_decline_text", "ADAPTER_CLASSES",
    "NetworkAdapter", "PlannerUpfrontAdapter", "ReactAdapter",
    "CodeAgentAdapter", "TextToSQLAdapter",
]
