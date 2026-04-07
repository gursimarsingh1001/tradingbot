from backend.strategies.bb_squeeze import BollingerBandSqueezeStrategy
from backend.strategies.base_strategy import StrategyContext
from backend.strategies.breakout_volume import BreakoutVolumeStrategy
from backend.strategies.macd_momentum import MACDMomentumStrategy
from backend.strategies.news_driven import NewsDrivenMomentumStrategy
from backend.strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from backend.strategies.supertrend import SupertrendStrategy
from backend.strategies.support_resistance import SupportResistanceStrategy


def test_major_strategies_have_non_trivial_parameter_grids():
    strategies = [
        MACDMomentumStrategy(),
        BollingerBandSqueezeStrategy(),
        BreakoutVolumeStrategy(),
        SupertrendStrategy(),
        SupportResistanceStrategy(),
        NewsDrivenMomentumStrategy(),
        RSIMeanReversionStrategy(),
    ]

    for strategy in strategies:
        assert len(strategy.parameter_grid()) > 1


def test_intraday_defaults_are_distinct_from_daily_defaults():
    intraday = StrategyContext(timeframe="INTRADAY", signal_type="INTRADAY")
    daily = StrategyContext(timeframe="DAILY", signal_type="INTRADAY")

    assert MACDMomentumStrategy().default_parameters(intraday) != MACDMomentumStrategy().default_parameters(daily)
    assert BreakoutVolumeStrategy().default_parameters(intraday) != BreakoutVolumeStrategy().default_parameters(daily)
    assert NewsDrivenMomentumStrategy().default_parameters(intraday) != NewsDrivenMomentumStrategy().default_parameters(daily)
