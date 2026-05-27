"""Price opposition and mirror zones."""

class OppositionReflectionService:
    def mirror_target(self, price: float, pivot: float) -> float:
        return pivot + (pivot - price)

