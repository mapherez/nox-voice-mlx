"""PersonaPlex MLX - Voice-conditioned conversational speech model for Apple Silicon."""

from .config import PersonaPlexConfig
from .model import PersonaPlexLm, load_personaplex

__all__ = ["PersonaPlexConfig", "PersonaPlexLm", "load_personaplex"]
__version__ = "0.1.0"
