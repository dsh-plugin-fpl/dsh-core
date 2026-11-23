from dataclasses import dataclass
@dataclass(frozen=True)
class ImmediateGenerationResult:
    result_url: str
    mime_type: str = ""
