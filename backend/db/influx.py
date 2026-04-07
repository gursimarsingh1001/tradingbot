from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from backend.config import get_settings
from backend.logging_utils import get_logger


settings = get_settings()
logger = get_logger(__name__)


class InfluxMarketDataStore:
    def __init__(self) -> None:
        self.client = InfluxDBClient(
            url=settings.influx_url,
            token=settings.influx_token,
            org=settings.influx_org,
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()
        self._warned_write_failure = False
        self._warned_query_failure = False

    def write_price_history(self, symbol: str, frame: pd.DataFrame) -> None:
        points: list[Point] = []
        for timestamp, row in frame.iterrows():
            point = (
                Point("daily_ohlcv")
                .tag("symbol", symbol)
                .field("open", float(row["Open"]))
                .field("high", float(row["High"]))
                .field("low", float(row["Low"]))
                .field("close", float(row["Close"]))
                .field("volume", float(row["Volume"]))
                .time(timestamp.to_pydatetime(), WritePrecision.S)
            )
            for column, value in row.items():
                if column in {"Open", "High", "Low", "Close", "Volume"} or pd.isna(value):
                    continue
                point = point.field(column.lower(), float(value))
            points.append(point)
        if points:
            try:
                self.write_api.write(bucket=settings.influx_bucket, org=settings.influx_org, record=points)
                self._warned_write_failure = False
            except Exception as exc:
                if not self._warned_write_failure:
                    logger.warning("Influx write failed for %s: %s", symbol, exc)
                    self._warned_write_failure = True

    def query_symbol_history(self, symbol: str, start: datetime, stop: datetime) -> pd.DataFrame:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if stop.tzinfo is None:
            stop = stop.replace(tzinfo=timezone.utc)
        start_str = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        stop_str = stop.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        query = f"""
        from(bucket: "{settings.influx_bucket}")
          |> range(start: time(v: "{start_str}"), stop: time(v: "{stop_str}"))
          |> filter(fn: (r) => r["_measurement"] == "daily_ohlcv" and r["symbol"] == "{symbol}")
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["_time"])
        """
        try:
            tables = self.query_api.query_data_frame(query)
            self._warned_query_failure = False
        except Exception as exc:
            if not self._warned_query_failure:
                logger.warning("Influx query failed for %s: %s", symbol, exc)
                self._warned_query_failure = True
            return pd.DataFrame()
        if isinstance(tables, list):
            frame = pd.concat(tables, ignore_index=True)
        else:
            frame = tables
        if frame.empty:
            return pd.DataFrame()
        frame = frame.rename(
            columns={
                "_time": "Datetime",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
                "ema_9": "EMA_9",
                "ema_20": "EMA_20",
                "ema_50": "EMA_50",
                "sma_50": "SMA_50",
                "sma_100": "SMA_100",
                "sma_200": "SMA_200",
                "rsi_14": "RSI_14",
                "macd": "MACD",
                "macd_signal": "MACD_Signal",
                "macd_hist": "MACD_Hist",
                "atr_14": "ATR_14",
                "atr_20_avg": "ATR_20_AVG",
                "bb_upper": "BB_Upper",
                "bb_lower": "BB_Lower",
                "bb_width": "BB_Width",
                "bb_width_avg_20": "BB_Width_Avg_20",
                "adx": "ADX",
                "di_plus": "DI_Plus",
                "di_minus": "DI_Minus",
                "supertrend": "Supertrend",
                "obv": "OBV",
                "vwap": "VWAP",
                "volume_sma_20": "Volume_SMA_20",
                "high_20": "High_20",
                "low_20": "Low_20",
                "high_63": "High_63",
                "low_63": "Low_63",
                "hammer": "HAMMER",
                "engulfing": "ENGULFING",
                "morning_star": "MORNING_STAR",
                "evening_star": "EVENING_STAR",
                "doji": "DOJI",
                "shooting_st": "SHOOTING_ST",
                "hammer_inv": "HAMMER_INV",
                "piercing": "PIERCING",
                "dark_cloud": "DARK_CLOUD",
                "three_white": "THREE_WHITE",
                "three_black": "THREE_BLACK",
            }
        )
        drop_columns = [column for column in ["result", "table", "_start", "_stop", "_measurement", "symbol"] if column in frame.columns]
        if drop_columns:
            frame = frame.drop(columns=drop_columns)
        frame["Datetime"] = pd.to_datetime(frame["Datetime"])
        return frame.set_index("Datetime").sort_index()


_store: InfluxMarketDataStore | None = None
_store_lock = Lock()


def get_influx_store() -> InfluxMarketDataStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = InfluxMarketDataStore()
    return _store
