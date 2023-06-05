"""Helpers to create Pandas dataframes for per-pair analytics."""
import datetime
import logging
import warnings
from typing import Optional, Tuple, Iterable

import numpy as np
import pandas as pd

from tradingstrategy.pair import DEXPair
from tradingstrategy.timebucket import TimeBucket
from tradingstrategy.types import PrimaryKey
from tradingstrategy.utils.forward_fill import forward_fill
from tradingstrategy.utils.time import assert_compatible_timestamp


logger = logging.getLogger(__name__)


class NoDataAvailable(Exception):
    """Raises when the user is asking data that is empty."""


class PairGroupedUniverse:
    """A base class for manipulating columnar sample data by a pair.

    The server dumps all pairs in a single continuous data frame.
    For most the use cases, we want to look up and manipulate data by pairs.
    To achieve this, we use Pandas :py:class:`pd.GroupBy` and
    recompile the data on the client side.

    This works for

    - OHLCV candles

    - Liquidity candles

    The input :py:class:`pd.DataFrame` is sorted by default using `timestamp`
    column and then made this column as an index. This is not optimised (not inplace).
    """

    def __init__(self,
                 df: pd.DataFrame,
                 time_bucket=TimeBucket.d1,
                 timestamp_column="timestamp",
                 index_automatically=True,
                 fix_wick_threshold: tuple | None = (0.1, 1.9)):
        """
        :param time_bucket:
            What bar size candles we are operating at. Default to daily.
            TODO: Currently not used. Will be removed in the future versions.

        :param timestamp_column:
            What column use to build a time index. Used for QStrader / Backtrader compatibility.

        :param index_automatically:
            Convert the index to use time series. You might avoid this with QSTrader kind of data.

        :param fix_wick_threshold:
            Apply abnormal high/low wick fix filter.

            Percent value of maximum allowed high/low wick relative to close.
            By default fix values where low is 90% lower than close and high is 90% higher than close.

            See :py:func:`tradingstrategy.utils.groupeduniverse.fix_bad_wicks` for more information.
        """
        self.index_automatically = index_automatically
        assert isinstance(df, pd.DataFrame)

        if index_automatically:
            self.df = df \
                .set_index(timestamp_column, drop=False)\
                .sort_index(inplace=False)
            # https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.sort_index.html
        else:
            self.df = df

        if fix_wick_threshold:
            self.df = fix_bad_wicks(self.df, fix_wick_threshold)

        self.pairs: pd.GroupBy = self.df.groupby(["pair_id"])

        self.timestamp_column = timestamp_column
        self.time_bucket = time_bucket

        self.candles_cache: dict[int, pd.DataFrame] = {}

    def clear_cache(self):
        """Clear candles cached by pair."""
        self.candles_cache = {}

    def get_columns(self) -> pd.Index:
        """Get column names from the underlying pandas.GroupBy object"""
        return self.pairs.obj.columns

    def get_sample_count(self) -> int:
        """Return the dataset size - how many samples total for all pairs"""
        return len(self.df)

    def get_pair_count(self) -> int:
        """Return the number of pairs in this dataset"""
        return len(self.pairs.groups)

    def get_samples_by_pair(self, pair_id: PrimaryKey) -> pd.DataFrame:
        """Get samples for a single pair.

        After the samples have been extracted, set `timestamp` as the index for the data.

        :return:
            Data frame group

        :raise KeyError:
            If we do not have data for pair_id
        """
        try:
            pair = self.pairs.get_group(pair_id)
        except KeyError as e:
            raise KeyError(f"No OHLC samples for pair id {pair_id} in {self}") from e
        return pair

    def get_last_entries_by_pair_and_timestamp(self,
            pair_id: PrimaryKey,
            timestamp: pd.Timestamp,
            small_time=pd.Timedelta(seconds=1),
        ) -> pd.DataFrame:
        """Get samples for a single pair before a timestamp.

        Return a DataFrame slice containing all datapoints before the timestamp.

        :param pair_id:
            Integer id for a trading pair

        :param timestamp:
            Get all samples excluding this timestamp.

        :return:
            Dataframe that contains samples for a single trading pair.

            Indexed by timestamp.

        :raise KeyError:
            If we do not have data for pair_id
        """
        pair_candles = self.get_samples_by_pair(pair_id)
        # Watch out for inclusive timestamp
        # https://stackoverflow.com/questions/49962417/why-does-loc-have-inclusive-behavior-for-slices
        adjusted_timestamp = timestamp - small_time
        return pair_candles.loc[:adjusted_timestamp]

    def get_all_pairs(self) -> Iterable[Tuple[PrimaryKey, pd.DataFrame]]:
        """Go through all liquidity samples, one DataFrame per trading pair."""
        for pair_id, data in self.pairs:
            yield pair_id, data

    def get_pair_ids(self) -> Iterable[PrimaryKey]:
        """Get all pairs present in the dataset"""
        with warnings.catch_warnings():
            # FutureWarning: In a future version of pandas, a length 1 tuple will be returned when 
            # iterating over a groupby with a grouper equal to a list of length 1. 
            # Don't supply a list with a single grouper to avoid this warning.
            
            warnings.simplefilter("ignore")
        
            for pair_id, data in self.pairs:
                yield pair_id

    def get_all_samples_by_timestamp(self, ts: pd.Timestamp) -> pd.DataFrame:
        """Get list of candles/samples for all pairs at a certain timepoint.

        :raise KeyError: The universe does not contain a sample for a given timepoint
        :return: A DataFrame that contains candles/samples at the specific timeout
        """
        assert_compatible_timestamp(ts)
        samples = self.df.loc[self.df[self.timestamp_column] == ts]
        return samples

    def get_all_samples_by_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Get list of candles/samples for all pairs at a certain range.

        Useful to get the last few samples for multiple pairs.

        Example:

        .. code-block:: python

                # Set up timestamps for 3 weeks range, one week in middle
                end = Timestamp('2021-10-25 00:00:00')
                start = Timestamp('2021-10-11 00:00:00')
                middle = start + (end - start) / 2

                # Get weekly candles
                raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()
                candle_universe = GroupedCandleUniverse(raw_candles)
                candles = candle_universe.get_all_samples_by_range(start, end)

                # We have pair data for 3 different weeks
                assert len(candles.index.unique()) == 3

                # Each week has its of candles broken down by a pair
                # and can be unique addressed by their pair_id
                assert len(candles.loc[start]) >= 1000
                assert len(candles.loc[middle]) >= 1000
                assert len(candles.loc[end]) >= 1000

        :param start: start of the range (inclusive)
        :param end: end of the range (inclusive)
        :return: A DataFrame that contains candles/samples for all pairs at the range.
        """
        assert_compatible_timestamp(start)
        assert_compatible_timestamp(end)
        assert start < end, f"Got reverse timestamp range {start} - {end}"

        # https://stackoverflow.com/a/69605701/315168
        samples = self.df.loc[
            (self.df.index >= start) &
            (self.df.index <= end)
        ]
        return samples

    def iterate_samples_by_pair_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Get list of candles/samples for all pairs at a certain range.

        Useful to get the last few samples for multiple pairs.

        Example:

        .. code-block:: python

            raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()
            candle_universe = GroupedCandleUniverse(raw_candles)

            # Calibrate our week
            random_date = pd.Timestamp("2021-10-29")
            end = candle_universe.get_prior_timestamp(random_date)
            assert end == pd.Timestamp("2021-10-25")

            # Because we ar using weekly candles,
            # and start and end are inclusive endpoints,
            # we should get 3 weeks of samples
            start = pd.Timestamp(end) - pd.Timedelta(weeks=2)

            for pair_id, pair_df in candle_universe.iterate_samples_by_pair_range(start, end):
                # Because of missing samples, some pairs may have different ranges.
                # In this example, we iterate 3 weeks ranges, so we can have
                # 1, 2 or 3 weekly candles.
                # If there was no data at all pair_id is not present in the result.
                range_start = pair_df.index[0]
                range_end = pair_df.index[-1]
                assert range_start <= range_end
                # Calculate the momentum for the full range of all samples
                first_candle = pair_df.iloc[0]
                last_candle = pair_df.iloc[-1]
                # Calculate
                momentum = (last_candle["close"] - first_candle["open"]) / first_candle["open"] - 1

        :param start: start of the range (inclusive)

        :param end: end of the range (inclusive)

        :return: `DataFrame.groupby` result
        """
        samples = self.get_all_samples_by_range(start, end)
        return samples.groupby("pair_id")

    def get_timestamp_range(self, use_timezone=False) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
        """Return the time range of data we have for.

        :param use_timezone:
            The resulting timestamps will have their timezone set to UTC.
            If not set then naive timestamps are generated.

        :return:
            (start timestamp, end timestamp) tuple, UTC-timezone aware
            If the data frame is empty, return `None, None`.
        """

        if len(self.df) == 0:
            return None, None
        
        if(self.index_automatically == True):
            if use_timezone:
                start = (self.df[self.timestamp_column].iat[0]).tz_localize(tz='UTC')
                end = (self.df[self.timestamp_column].iat[-1]).tz_localize(tz='UTC')
            else:
                start = self.df[self.timestamp_column].iat[0]
                end = self.df[self.timestamp_column].iat[-1]
        else:
            if use_timezone:
                start = min(self.df[self.timestamp_column]).tz_localize(tz='UTC')
                end = max(self.df[self.timestamp_column]).tz_localize(tz='UTC')
            else:
                start = min(self.df[self.timestamp_column])
                end = max(self.df[self.timestamp_column])

        return start, end

    def get_prior_timestamp(self, ts: pd.Timestamp) -> pd.Timestamp:
        """Get the first timestamp in the index that is before the given timestamp.

        This allows us to calibrate weekly/4 hours/etc. indexes to any given time..

        Example:

        .. code-block:: python

            raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()
            candle_universe = GroupedCandleUniverse(raw_candles)

            # Calibrate our week
            random_date = pd.Timestamp("2021-10-29")
            weekly_ts_before = candle_universe.get_prior_timestamp(random_date)

            assert weekly_ts_before == pd.Timestamp("2021-10-25")

        :return: Any timestamp from the index that is before or at the same time of the given timestamp.
        """
        index = self.df.index
        return index[index <= ts][-1]

    def get_single_pair_data(self,
                             timestamp: Optional[pd.Timestamp] = None,
                             sample_count: Optional[int] = None,
                             allow_current=False,
                             raise_on_not_enough_data=True,
                             time_range_epsilon_seconds=0.5,
                             ) -> pd.DataFrame:
        """Get all candles/liquidity samples for the single alone pair in the universe by a certain timestamp.

        A shortcut method for trading strategies that trade only one pair.
        Designed to be backtesting and live trading friendly function to access candle data.

        Example:

        .. code-block: python


                from tradingstrategy.utils.groupeduniverse import NoDataAvailable

                try:
                    candles: pd.DataFrame = universe.candles.get_single_pair_data(
                        timestamp,
                        sample_count=moving_average_long,
                    )
                except NoDataAvailable:
                    # This can be raised if
                    # - Data source has not yet data available in the timestamp
                    # - You are asking `sample_count` worth of data and timestamp
                    #   has not yet enough data in the backtest buffer
                    pass

        .. note ::

            By default get_single_pair_da   ta() returns the candles prior to the `timestamp`,
            the behavior can be changed with get_single_pair_data(allow_current=True).
            At the start of the backtest, we do not have any previous candle available yet,
            so this function may raise :py:class:`NoDataAvailable`.

        :param timestamp:
            Get the sample until this timestamp and all previous samples.

        :param allow_current:
            Allow to read any candle precisely at the timestamp.
            If you read the candle of your current strategy cycle timestamp,
            bad things may happen.

            In backtesting, reading the candle at the current timestamp
            introduces forward-looking bias. In live trading,
            reading the candle at the current timestamp may
            give you no candle or an incomplete candle (trades are still
            piling up on it).

        :param sample_count:
            Minimum candle/liquidity sample count needed.

            Limit the returned number of candles N candles before the timestamp.

            If the data does not have enough samples before `timestamp`,
            then raise :py:class:`NoDataAvailable`.

        :param raise_on_not_enough_data:
            Raise an error if no data is available.

            This can be e.g. because the trading pair has

        :param time_range_epsilon_seconds:
            The time delta epsilon we use to determine between "current" and "previous" candle.

        :raise NoDataAvailable:
            Raised when there is no data available at the range.

            Set `fail_on_empty=False` to return an empty `DataFrame` instead.

        """

        pair_count = self.get_pair_count()
        assert pair_count == 1, f"This function only works for single pair univese, we have {pair_count} pairs"
        df = self.df

        # Get all df content before our timestamp
        if timestamp:
            if allow_current:
                after = timestamp + pd.Timedelta(seconds=time_range_epsilon_seconds)
            else:
                after = timestamp - pd.Timedelta(seconds=time_range_epsilon_seconds)

            df = df.truncate(after=after)

        # Do candle count clip
        if sample_count:
            df = df.iloc[-sample_count:]
        else:
            pass

        # Be helpful with a possible error
        if raise_on_not_enough_data:
            if (sample_count is None and len(df) == 0) or (sample_count is not None and len(df) < sample_count):
                start_at = self.df["timestamp"].min()
                end_at = self.df["timestamp"].max()
                raise NoDataAvailable(f"Tried to ask candle data for timestamp {timestamp}. Truncating data after {after}. Minimum sample count needed is set to {sample_count}.\n"
                                      f"\n"
                                      f"The result was {len(df)} candles. The trading pair or the time period does not have enough data.\n"
                                      f"The total loaded candle data is {len(self.df)} candles at range {start_at} - {end_at}.\n"
                                      f"\n"
                                      f"You cannot ask data for the current candle (same as the timestamp) unless you set allow_current=True.\n"
                                      f"\n"
                                      f"The current timestamp is ignored byt default protect against accidental testing of future data.\n"
                                      f"If you want to access empty or not enough data, set raise_on_not_enough_data=False.")

        return df

    def forward_fill(
        self,
        columns: Tuple[str] = ("open", "close"),
        drop_other_columns=True,
    ):
        """Forward-fill sparse OHLCV candle data.

        Forward fills the missing candle values for non-existing candles.
        Trading Strategy data does not have candles unless there was actual trades
        happening at the markets.

        See :py:mod:`tradingstrategy.utils.forward_fill` for details.

        .. note ::

            Does not touch the original `self.df` DataFrame any way.
            Only `self.pairs` is modified with forward-filled data.

        :param columns:
            Columns to fill.

            To save memory and speed, only fill the columns you need.
            Usually `open` and `close` are enough and also filled
            by default.

        :param drop_other_columns:
            Remove other columns before forward-fill to save memory.

            The resulting DataFrame will only have columns listed in `columns`
            parameter.

            The removed columns include ones like `high` and `low`, but also Trading Strategy specific
            columns like `start_block` and `end_block`. It's unlikely we are going to need
            forward-filled data in these columns.
        """

        self.pairs = forward_fill(
            self.pairs,
            self.time_bucket.to_frequency(),
            columns=columns,
            drop_other_columns=drop_other_columns,
        )

        # Clear candle cache
        self.clear_cache()


def filter_for_pairs(samples: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Filter dataset so that it only contains data for the trading pairs from a certain exchange.

    Useful as a preprocess step for creating :py:class:`tradingstrategy.candle.GroupedCandleUniverse`
    or :py:class:`tradingstrategy.liquidity.GroupedLiquidityUniverse`.

    :param samples: Candles or liquidity dataframe

    :param pairs: Pandas dataframe with :py:class:`tradingstrategy.pair.DEXPair` content.
    """
    ids = pairs["pair_id"]
    our_pairs: pd.DataFrame = samples.loc[
        (samples['pair_id'].isin(ids))
    ]
    return our_pairs


def filter_for_single_pair(samples: pd.DataFrame, pair: DEXPair) -> pd.DataFrame:
    """Filter dataset so that it only contains data for a single trading pair.

    Useful to construct single trading pair universe.

    :param samples: Candles or liquidity dataframe
    """
    assert isinstance(pair, DEXPair), f"We got {pair}"
    our_pairs: pd.DataFrame = samples.loc[
        (samples['pair_id'] == pair.pair_id)
    ]
    return our_pairs


def resample_candles(df: pd.DataFrame, new_timedelta: pd.Timedelta) -> pd.DataFrame:
    """Downsample OHLCV candles or liquidity samples to less granular time bucket.

    E.g. transform 1h candles to 24h candles.

    Example:

    .. code-block:: python

        single_pair_candles = raw_candles.loc[raw_candles["pair_id"] == pair.pair_id]
        single_pair_candles = single_pair_candles.set_index("timestamp", drop=False)
        monthly_candles = upsample_candles(single_pair_candles, TimeBucket.d30)
        assert len(monthly_candles) <= len(single_pair_candles) / 4

    """
    
    assert isinstance(new_timedelta, pd.Timedelta), f"We got {new_timedelta}, supposed to be pd.Timedelta. E.g. pd.Timedelta(hours=2)"
    
    #pandas_time_delta = new_bucket.to_pandas_timedelta()
    # https://stackoverflow.com/questions/21140630/resampling-trade-data-into-ohlcv-with-pandas
    candles = df.resample(new_timedelta).mean(numeric_only=True)

    # TODO: Figure out right way to preserve timestamp column,
    # resample seems to destroy it
    candles["timestamp"] = candles.index

    return candles


def fix_bad_wicks(
        df: pd.DataFrame,
        threshold=(0.1, 1.9),
        too_slow_threshold=15,
) -> pd.DataFrame:
    """Correct out bad high/low values in OHLC data.

    On :term:`Uniswap` v2 and compatibles, Bad wicks are caused by e.g. very large flash loan, oracle price manipulation attacks,
    and misbheaving bots.

    This function removes bad high/low values and sets them to open/close if they seem to be wildly out of sample.

    :param threshold:
        How many pct % wicks are allowed through.

        Tuple (low threshold, high threshold) relative to close.

        Default to 50%. A high wick cannot be more than 50% of close.

    :param too_slow_threshold:
        Complain if this takes too long
    """

    start = datetime.datetime.utcnow()

    if len(df) == 0:
        return df

    # Optimised with np.where()
    # https://stackoverflow.com/a/65729035/315168
    df["high"] = np.where(df["high"] > df["close"] * threshold[1], df["close"], df["high"])
    df["low"] = np.where(df["low"] < df["close"] * threshold[0], df["close"], df["low"])

    duration = datetime.datetime.utcnow() - start

    if duration > datetime.timedelta(seconds=too_slow_threshold):
        logger.warning("Very slow fix_bad_wicks(): %s", duration)

    # The following code chokes
    # mask = (df["high"] > df["close"] * (1+threshold)) | (df["low"] < df["close"] * threshold)
    #df.loc[mask, "high"] = df["close"]
    #df.loc[mask, "low"] = df["close"]
    #df.loc[mask, "wick_filtered"] = True
    return df


def filter_bad_wicks(df: pd.DataFrame, threshold=(0.1, 1.9)) -> pd.DataFrame:
    """Mark the bad wicks.

    On :term:`Uniswap` v2 and compatibles, Bad wicks are caused by e.g. very large flash loan, oracle price manipulation attacks,
    and misbheaving bots.

    This function removes bad high/low values and sets them to open/close if they seem to be wildly out of sample.

    :param threshold:
        How many pct % wicks are allowed through.

        Default to 50%. A high wick cannot be more than 50% of close.

    """

    df_matches = df.loc[
        (df["high"] > df["close"] * threshold[1]) | (df["low"] < df["close"] * threshold[0])
    ]

    return df_matches
