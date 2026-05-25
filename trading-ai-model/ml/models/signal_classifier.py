"""Main signal prediction model — LightGBM with rule fallback."""

from ml.models.lightgbm_classifier import LightGBMSignalClassifier


class SignalClassifier(LightGBMSignalClassifier):
    """Alias for production classifier."""

    def predict(self, features: dict) -> dict:
        return super().predict(features)
