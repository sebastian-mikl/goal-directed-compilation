"""Constants and logging for the GDC engine."""
import os, sys, logging
from pathlib import Path
from datetime import datetime
from anthropic import Anthropic
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

# Per-call model tiering. Call 1 (the GDC goal emission) needs the stronger
# model; Call 3 (verify/interpret) is cheaper. Each is overridable by env var.
# MODEL_CALL1 is the one the paper cites (Appendix B: "Model").
_DEFAULT_STRONG = "claude-sonnet-4-5"
_DEFAULT_CHEAP  = "claude-haiku-4-5"
MODEL_CALL1 = os.getenv("MODEL_CALL1", _DEFAULT_STRONG)   # goal emission (Call 1)
MODEL_CALL2 = os.getenv("MODEL_CALL2", _DEFAULT_STRONG)
MODEL_CALL3 = os.getenv("MODEL_CALL3", _DEFAULT_CHEAP)

WORKSPACE   = Path("workspace")
ENVIRONMENT = Path("environment")   # read-only source data; never cleared

CLIENT = Anthropic() if os.getenv("ANTHROPIC_API_KEY") else None


def init_logger(debug: bool = False) -> None:
    WORKSPACE.mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    lvl = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s [%(levelname)-8s] %(message)s",
        handlers=[
            logging.FileHandler(f"logs/gdc_{ts}.log"),
            logging.StreamHandler(),
        ]
    )


def log(msg, level: str = "info") -> None:
    getattr(logging, level)(msg)
