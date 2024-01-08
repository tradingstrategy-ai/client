"""Get candlestick price and volume data from Binance.
"""

import requests
import datetime
import pandas as pd
import numpy as np
import logging
import shutil
import os
from pathlib import Path
from types import NoneType
from typing import Dict, Literal, Iterable
from tqdm.auto import tqdm

from tradingstrategy.timebucket import TimeBucket
from pathlib import Path
from tradingstrategy.utils.time import (
    generate_monthly_timestamps,
    naive_utcnow,
    naive_utcfromtimestamp,
)
from tradingstrategy.utils.groupeduniverse import resample_series
from tradingstrategy.lending import (
    LendingCandleType,
    convert_binance_lending_rates_to_supply,
)
from tradingstrategy.types import PrimaryKey
from tradingstrategy.lending import convert_interest_rates_to_lending_candle_type_map
from tradingstrategy.binance.constants import BINANCE_SUPPORTED_QUOTE_TOKENS, split_binance_symbol, DAYS_IN_YEAR


logger = logging.getLogger(__name__)


class BinanceDataFetchError(ValueError):
    """Something wrong with Binance."""


class BinanceDownloader:
    """Class for downloading Binance candlestick OHLCV data."""

    def __init__(self, cache_directory: Path = Path("/tmp/binance_data")):
        """Initialize BinanceCandleDownloader and create folder for cached data if it does not exist."""
        cache_directory.mkdir(parents=True, exist_ok=True)
        self.cache_directory = cache_directory
        self.api_server = "api.binance.com"

    def fetch_candlestick_data(
        self,
        symbols: list[str] | str,
        time_bucket: TimeBucket,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        force_download=False,
    ) -> pd.DataFrame:
        """Get clean candlestick price and volume data from Binance. If saved, use saved version, else create saved version.

        Note, if you want to use this data in our framework, you will need to add informational columns to the dataframe and overwrite it. See code below.

        .. code-block:: python
            symbol = "ETHUSDT"
            df = get_binance_candlestick_data(symbol, TimeBucket.h1, datetime.datetime(2021, 1, 1), datetime.datetime(2021, 4, 1))
            df = add_informational_columns(df, pair, EXCHANGE_SLUG)
            path = get_parquet_path(symbol, TimeBucket.h1, datetime.datetime(2021, 1, 1), datetime.datetime(2021, 4, 1))
            df.to_parquet(path)

        :param symbol:
            Trading pair symbol E.g. ETHUSDC

        :param interval:
            Can be one of `1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M`

        :param start_at:
            Start date of the data

        :param end_at:
            End date of the data

        :param force_download:
            Force redownload of data from Binance and overwrite cached version

        :return:
            Pandas dataframe with the OHLCV data for the columns and datetimes as the index
        """
        if isinstance(symbols, str):
            symbols = [symbols]

        dataframes = []
        total_size = 0

        with tqdm(total=len(symbols)) as progress_bar:
            for symbol in symbols:
                df = self.fetch_candlestick_data_single_pair(
                    symbol, time_bucket, start_at, end_at, force_download
                )
                dataframes.append(df)

                # Count the cached file size
                path = self.get_parquet_path(symbol, time_bucket, start_at, end_at)
                total_size += os.path.getsize(path)

                progress_bar.set_postfix(
                    {"pair": symbol, "total_size (MBytes)": total_size / (1024**2)}
                )
                progress_bar.update()

        combined_dataframe = pd.concat(dataframes, axis=0)

        return combined_dataframe

    def fetch_candlestick_data_single_pair(
        self,
        symbol: str,
        time_bucket: TimeBucket,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        force_download=False,
    ) -> pd.DataFrame:
        """Fetch candlestick data for a single pair.

        Using this function directly will not include progress bars. Use `fetch_candlestick_data` instead.
        """
        if not force_download:
            try:
                return self.get_data_parquet(symbol, time_bucket, start_at, end_at)
            except:
                pass

        if symbol not in self.fetch_all_spot_symbols():
            raise BinanceDataFetchError(f"Symbol {symbol} is not a valid spot symbol")

        # to include the end date, we need to add one day
        end_at = end_at + datetime.timedelta(days=1)
        df = self._fetch_candlestick_data(
            symbol,
            time_bucket,
            start_at,
            end_at,
        )
        df["pair_id"] = symbol

        # write to parquet
        end_at = end_at - datetime.timedelta(days=1)
        path = self.get_parquet_path(symbol, time_bucket, start_at, end_at)
        df.to_parquet(path)

        return df

    def _fetch_candlestick_data(
        self,
        symbol: str,
        time_bucket: TimeBucket,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
    ) -> pd.DataFrame:
        """Private function to fetch candlestick data from Binance. This function does will always download data from Binance"""
        interval = get_binance_interval(time_bucket)

        params_str = f"symbol={symbol}&interval={interval}"

        if start_at:
            assert end_at, "If you specify a start_at, you must also specify an end_at"
            assert isinstance(
                start_at, datetime.datetime
            ), "start_at must be a datetime.datetime object"
            assert isinstance(
                end_at, datetime.datetime
            ), "end_at must be a datetime.datetime object"
            start_timestamp = int(start_at.timestamp() * 1000)
            end_timestamp = int(end_at.timestamp() * 1000)
        else:
            start_at = self.fetch_approx_asset_trading_start_date(symbol)

        if end_at:
            assert start_at < end_at, "end_at must be after start_at"
        else:
            end_at = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

        # generate timestamps for each iteration
        dates = [start_at]
        current_date = start_at
        while current_date < end_at:
            if (end_at - current_date) / time_bucket.to_timedelta() > 999:
                dates.append((current_date + time_bucket.to_timedelta() * 999))
                current_date += time_bucket.to_timedelta() * 999
            else:
                dates.append(end_at)
                current_date = end_at

        timestamps = [int(date.timestamp() * 1000) for date in dates]
        open_prices, high_prices, low_prices, close_prices, volume, dates = (
            [],
            [],
            [],
            [],
            [],
            [],
        )

        for i in range(0, len(timestamps) - 1):
            start_timestamp = timestamps[i]
            end_timestamp = timestamps[i + 1]
            full_params_str = (
                f"{params_str}&startTime={start_timestamp}&endTime={end_timestamp}"
            )
            url = f"https://api.binance.com/api/v3/klines?{full_params_str}&limit=1000"
            response = requests.get(url)
            if response.status_code == 200:
                json_data = response.json()
                if len(json_data) > 0:
                    for item in json_data:
                        date_time = naive_utcfromtimestamp(item[0] / 1000)
                        dates.append(date_time)
                        open_prices.append(float(item[1]))
                        high_prices.append(float(item[2]))
                        low_prices.append(float(item[3]))
                        close_prices.append(float(item[4]))
                        volume.append(float(item[5]))
            else:
                raise BinanceDataFetchError(
                    f"Error fetching data between {start_timestamp} and {end_timestamp}. \nResponse: {response.status_code} {response.text} \nMake sure you are using valid pair symbol e.g. `ETHUSDC`, not just ETH"
                )

        df = pd.DataFrame(
            {
                "open": open_prices,
                "high": high_prices,
                "low": low_prices,
                "close": close_prices,
                "volume": volume,
            },
            index=dates,
        )

        # df = clean_time_series_data(df)

        # Each timestamp in `timestamps` besides the first and last entry will be duplicated, so remove
        return df[df.index.duplicated(keep="first") == False]

    def fetch_lending_rates(
        self,
        asset_symbols: list[str] | str,
        time_bucket: TimeBucket,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        force_download=False,
    ) -> pd.DataFrame:
        """Get daily lending interest rates for a given asset from Binance, resampled to the given time bucket.

        :param asset_symbol:
            See py:method:`tradingstrategy.binance.downloader.get_all_lending_symbols` for valid symbols

        :param time_bucket:
            Time bucket to resample the data to

        :param start_date:
            Start date for the data. Note this value cannot be eariler than datetime.datetime(2019,4,1) due to Binance data limitations

        :param end_date:
            End date for the data

        :param force_download:
            Force redownload of data from Binance and overwrite cached version

        :return:
            Pandas dataframe with the interest rates for the column and datetimes as the index
        """
        if isinstance(asset_symbols, str):
            asset_symbols = [asset_symbols]

        dataframes = []
        total_size = 0

        with tqdm(total=len(asset_symbols)) as progress_bar:
            for asset_symbol in asset_symbols:
                df = self.fetch_lending_rates_single_pair(
                    asset_symbol, time_bucket, start_at, end_at, force_download
                )
                dataframes.append(df)

                # Count the cached file size
                path = self.get_parquet_path(
                    asset_symbol, time_bucket, start_at, end_at, is_lending=True
                )
                total_size += os.path.getsize(path)

                progress_bar.set_postfix(
                    {
                        "pair": asset_symbol,
                        "total_size (MBytes)": total_size / (1024**2),
                    }
                )
                progress_bar.update()

        combined_dataframe = pd.concat(dataframes, axis=0)

        return combined_dataframe

    def fetch_lending_rates_single_pair(
        self,
        asset_symbol: str,
        time_bucket: TimeBucket,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        force_download=False,
    ) -> pd.DataFrame:
        """Fetch lending rates for a single asset.

        Using this function directly will not include progress bars. Use `fetch_lending_rates` instead.
        """
        if not force_download:
            try:
                return self.get_data_parquet(
                    asset_symbol, time_bucket, start_at, end_at, is_lending=True
                )
            except:
                pass
        
        if asset_symbol not in self.fetch_all_lending_symbols():
            raise BinanceDataFetchError(f"Symbol {asset_symbol} is not a valid lending symbol")

        series = self._fetch_lending_rates(asset_symbol, start_at, end_at, time_bucket)

        path = self.get_parquet_path(
            asset_symbol, time_bucket, start_at, end_at, is_lending=True
        )
        df = series.to_frame(name="lending_rates")
        df["pair_id"] = asset_symbol

        df.to_parquet(path)

        return df

    def _fetch_lending_rates(
        self,
        asset_symbol: str,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        time_bucket: TimeBucket,
    ) -> pd.Series:
        assert type(asset_symbol) == str, "asset_symbol must be a string"
        assert (
            type(start_at) == datetime.datetime
        ), "start_date must be a datetime.datetime object"
        assert (
            type(end_at) == datetime.datetime
        ), "end_date must be a datetime.datetime object"
        assert (
            type(time_bucket) == TimeBucket
        ), "time_delta must be a pandas Timedelta object"
        # assert start_date >= datetime.datetime(2019,4,1), "start_date cannot be earlier than 2019-04-01 due to Binance data limitations"

        monthly_timestamps = generate_monthly_timestamps(start_at, end_at)
        response_data = []

        # API calls to get the data
        for i in range(len(monthly_timestamps) - 1):
            start_timestamp = monthly_timestamps[i] * 1000
            end_timestamp = monthly_timestamps[i + 1] * 1000
            url = f"https://www.binance.com/bapi/margin/v1/public/margin/vip/spec/history-interest-rate?asset={asset_symbol}&vipLevel=0&size=90&startTime={start_timestamp}&endTime={end_timestamp}"
            response = requests.get(url)
            if response.status_code == 200:
                json_data = response.json()
                data = json_data["data"]
                if len(data) > 0:
                    response_data.extend(data)
            else:
                raise BinanceDataFetchError(
                    f"No data found for {asset_symbol} between {start_at} and {end_at}. Check your symbol matches with valid symbols in method description. \nResponse: {response.status_code} {response.text}"
                )
        
        dates = []
        interest_rates = []
        for data in response_data:
            dates.append(pd.to_datetime(data["timestamp"], unit="ms"))
            interest_rates.append(float(data["dailyInterestRate"]) * 100 * DAYS_IN_YEAR) # convert daily to annual and multiply by 100

        unsampled_rates = pd.Series(data=interest_rates, index=dates).sort_index()

        # doesn't always raise error
        if unsampled_rates.empty:
            raise BinanceDataFetchError(
                f"No data found for {asset_symbol} between {start_at} and {end_at}. Check your symbol matches with valid symbols in method description. \nResponse: {response.status_code} {response.text}"
            )

        resampled_rates = resample_series(
            unsampled_rates, time_bucket.to_pandas_timedelta(), forward_fill=True
        )

        return resampled_rates

    def fetch_approx_asset_trading_start_date(self, symbol) -> datetime.datetime:
        """Get the asset trading start date at Binance.

        Binance was launched around 2017-08-01.

        :raise BinanceDataFetchError:
            If the asset does not exist.
        """

        monthly_candles = self.fetch_candlestick_data(
            symbol,
            TimeBucket.d30,
            datetime.datetime(2017, 1, 1),
            naive_utcnow(),
        )

        assert (
            len(monthly_candles) > 0
        ), f"Could not find starting date for asset {symbol}"
        return monthly_candles.index[0].to_pydatetime()

    def get_data_parquet(
        self,
        symbol: str,
        time_bucket: TimeBucket,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        is_lending: bool = False,
    ) -> pd.DataFrame:
        """Get parquet file for the candlestick data.

        :param symbol: Trading pair symbol E.g. ETHUSDC
        :param time_bucket: TimeBucket instance
        :param start_at: Start date of the data
        :param end_at: End date of the data
        :return: Path to the parquet file
        """
        path = self.get_parquet_path(symbol, time_bucket, start_at, end_at, is_lending)
        try:
            return pd.read_parquet(path)
        except Exception as e:
            raise e

    def get_parquet_path(
        self,
        symbol: str,
        time_bucket: TimeBucket,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        is_lending: bool = False,
    ) -> Path:
        """Get parquet path for the candlestick data.

        :param symbol: Trading pair symbol E.g. ETHUSDC
        :param time_bucket: TimeBucket instance
        :param start_at: Start date of the data
        :param end_at: End date of the data
        :return: Path to the parquet file
        """
        if is_lending:
            file_str = "lending"
        else:
            file_str = "candles"

        file = Path(
            file_str + f"-{symbol}-{time_bucket.value}-{start_at}-{end_at}.parquet"
        )
        return self.cache_directory.joinpath(file)

    def overwrite_cached_data(
        self,
        df: pd.DataFrame,
        symbol,
        STOP_LOSS_TIME_BUCKET,
        START_AT_DATA,
        END_AT,
        is_lending: bool = False,
    ) -> None:
        """Overwrite specific cached candle data file.

        :param symbol: Trading pair symbol E.g. ETHUSDC
        :param time_bucket: TimeBucket instance
        :param start_at: Start date of the data
        :param end_at: End date of the data
        :param path: Path to the parquet file. If not specified, it will be generated from the other parameters.
        """
        # TODO, assert exists
        path = self.get_parquet_path(
            symbol, STOP_LOSS_TIME_BUCKET, START_AT_DATA, END_AT, is_lending
        )
        assert path.exists(), f"File {path} does not exist."
        df.to_parquet(path)

    def purge_cached_file(
        self,
        *,
        symbol: str = None,
        time_bucket: TimeBucket = None,
        start_at: datetime.datetime = None,
        end_at: datetime.datetime = None,
        path: Path = None,
    ) -> None:
        """Purge specific cached candle data file.

        :param symbol: Trading pair symbol E.g. ETHUSDC
        :param time_bucket: TimeBucket instance
        :param start_at: Start date of the data
        :param end_at: End date of the data
        :param path: Path to the parquet file. If not specified, it will be generated from the other parameters.
        """
        if not path:
            path = self.get_parquet_path(symbol, time_bucket, start_at, end_at)
        if path.exists():
            path.unlink()
        else:
            logger.warn(f"File {path} does not exist.")

    def purge_all_cached_data(self) -> None:
        """Purge all cached candle data. This delete all contents of a cache directory, but not the directory itself. I.e. the cache directory will be left empty

        :param path: Path to the parquet file
        """
        for item in self.cache_directory.iterdir():
            if item.is_dir():
                # Recursively delete directories
                shutil.rmtree(item)
            else:
                # Delete files
                item.unlink()

    def load_lending_candle_type_map(
        self,
        asset_symbols_dict: dict[PrimaryKey, str],
        time_bucket: TimeBucket,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        force_download=False,
    ) -> Dict[LendingCandleType, pd.DataFrame]:
        """Load lending candles for all assets.

        See py:method:`tradingstrategy.binance.downloader.fetch_lending_rates` for valid symbols

        :param symbols:
            Dictionary of reserve_id to token symbol. The token symbol should be a valid symbol
            that can be used in .. py:method:`tradingstrategy.binance.downloader.fetch_lending_rates`.

        :return: LendingCandleUniverse
        """
        data = []

        lending_data = self.fetch_lending_rates(
            list(asset_symbols_dict.values()),
            time_bucket,
            start_at,
            end_at,
            force_download=force_download,
        )
        supply_data = convert_binance_lending_rates_to_supply(lending_data["lending_rates"])

        df = lending_data.copy()

        df["supply_rates"] = supply_data

        for reserve_id, asset_symbol in asset_symbols_dict.items():
            lending_data_for_asset = df.loc[df["pair_id"] == asset_symbol, "lending_rates"]
            supply_data_for_asset = df.loc[df["pair_id"] == asset_symbol, "supply_rates"]
            assert len(lending_data_for_asset) == len(supply_data_for_asset), "Lending and supply data must have the same length"

            data.append(
                {
                    "reserve_id": reserve_id,
                    "lending_data": lending_data_for_asset,
                    "supply_data": supply_data_for_asset,
                    "asset_symbol": asset_symbol,
                }
            )

        lending_candle_type_map = convert_interest_rates_to_lending_candle_type_map(
            *data
        )

        return lending_candle_type_map
    
    def fetch_assets(self, market: Literal["SPOT"] | Literal["MARGIN"] | NoneType = "MARGIN") -> Iterable[str]:
        """Load available assets on binance.
        Example:
            # Show all pairs that
            downloader = BinanceDownloader()
            pairs = {ticker for ticker in downloader.fetch_assets(market="MARGIN") if ticker.endswith("USDT")}
            print(f"USDT margin trading pairs: {pairs}")
        :param market:
            Are we looking for MARGIN or SPOT or both markets.
        :return:
            Iterable of all asset symbols.
            E.g. "ETHUSDT", "BTCUSDT"
        """

        # https://binance-docs.github.io/apidocs/spot/en/#exchange-information
        resp = requests.get(f"https://{self.api_server}/api/v3/exchangeInfo")
        data = resp.json()
        symbols = data["symbols"]
        for s in symbols:
            if market:
                if market not in s["permissions"]:
                    continue

            yield s["symbol"]

    def fetch_all_lending_symbols(self):
        """List of all valid asset symbols for fetching lending data
        """
        return set(BINANCE_SUPPORTED_QUOTE_TOKENS).union({split_binance_symbol(ticker)[0] for ticker in self.fetch_assets(market="MARGIN") if ticker.endswith(BINANCE_SUPPORTED_QUOTE_TOKENS)})
    
    def fetch_all_spot_symbols(self):
        """List of all valid pool symbols for fetching candle data
        """
        return self.fetch_assets(market="SPOT")


def clean_time_series_data(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Unused for now since data from Binance data occasionally has gaps. Not a huge deal.

    Cleans time series data to ensure:
    - No Nan values
    - Index contains no duplicates
    - Has equally spaced intervals with no gaps
    - Sorted index in ascending order by datetime

    :param df: Pandas dataframe or series
    :return: Cleaned dataframe or series
    """

    if df.isna().any(axis=None):
        raise ValueError("Dataframe contains NaN values")

    if df.duplicated().any():
        raise ValueError("Dataframe contains duplicate values")

    assert type(df.index) == pd.DatetimeIndex, "Index must be a DatetimeIndex"

    df.sort_index(inplace=True)

    if len(uneven_indices := get_indices_of_uneven_intervals(df)) > 0:
        raise ValueError(
            f"Dataframe contains uneven intervals at indices {uneven_indices}"
        )

    return df


def get_indices_of_uneven_intervals(df: pd.DataFrame | pd.Series) -> bool:
    """Checks if a time series contains perfectly evenly spaced time intervals with no gaps.

    :param df: Pandas dataframe or series
    :return: True if time series is perfectly evenly spaced, False otherwise
    """
    assert type(df.index) == pd.DatetimeIndex, "Index must be a DatetimeIndex"

    numeric_representation = df.index.astype(np.int64)

    differences = np.diff(numeric_representation)

    not_equal_to_first = differences != differences[0]

    return np.where(not_equal_to_first)[0]


def get_binance_interval(bucket: TimeBucket) -> str:
    """Convert our TimeBucket to Binance's internal format."""
    if bucket == TimeBucket.d30:
        # Can be one of `1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M`
        return "1M"
    else:
        return bucket.value
