"""Main signal prediction model."""

from ml.models.base_model import BaseModel


class SignalClassifier(BaseModel):
    def predict(self, features: dict) -> dict:
        return {"signal_probability": 0.5}

