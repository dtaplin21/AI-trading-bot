"""Time-series DB interface (InfluxDB/Parquet)."""

class TimeseriesStore:
    def write(self, symbol: str, data) -> None:
        pass

    def read(self, symbol: str, start: str, end: str):
        return []

