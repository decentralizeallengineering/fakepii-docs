"""fakepii-docs: synthetic office documents full of FAKE personal data, with
exact ground truth, for testing masking and unmasking systems."""

__version__ = "0.1.0"

FORMATS = ("docx", "xlsx", "pptx", "pdf", "pdf-image", "csv", "txt", "md")

from .corpus import generate_corpus, generate_document  # noqa: E402
from .scorer import score  # noqa: E402
from .truth import load_truth  # noqa: E402

__all__ = ["FORMATS", "generate_corpus", "generate_document", "score", "load_truth", "__version__"]
