"""Abstract base model class."""

from abc import ABC, abstractmethod


class BaseModel(ABC):
    @abstractmethod
    def predict(self, features: dict) -> dict:
        ...

