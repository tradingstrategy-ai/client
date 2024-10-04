"""Trading strategy client.

A Python client class to downlaod different datasets from `Trading Strategy oracle <https://tradingstrategy.ai>`_.

For usage see

- :py:class:`Client` class

"""
import datetime
import logging
import os
import tempfile
import time
import warnings
from abc import abstractmethod, ABC
from functools import wraps
from json import JSONDecodeError
from pathlib import Path
from typing import Final, Optional, Union, Collection, Dict

import pandas as pd

from tradingstrategy.candle import TradingPairDataAvailability
from tradingstrategy.reader import BrokenData, read_parquet
from tradingstrategy.transport.pyodide import PYODIDE_API_KEY
from tradingstrategy.types import PrimaryKey, AnyTimestamp
from tradingstrategy.utils.jupyter import is_pyodide
from tradingstrategy.lending import LendingReserveUniverse, LendingCandleType, LendingCandleResult

# TODO: Must be here because  warnings are very inconveniently triggered import time
from tqdm import TqdmExperimentalWarning
warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)
from tqdm_loggable.auto import tqdm


with warnings.catch_warnings():
    # Work around this warning
    # ../../../Library/Caches/pypoetry/virtualenvs/tradeexecutor-Fzci9y7u-py3.9/lib/python3.9/site-packages/marshmallow/__init__.py:17
    #   /Users/mikkoohtamaa/Library/Caches/pypoetry/virtualenvs/tradeexecutor-Fzci9y7u-py3.9/lib/python3.9/site-packages/marshmallow/__init__.py:17: DeprecationWarning: distutils Version classes are deprecated. Use packaging.version instead.
    #     __version_info__ = tuple(LooseVersion(__version__).version)
    warnings.simplefilter("ignore")
    import dataclasses_json  # Trigger marsmallow import to supress the warning

import pyarrow
import pyarrow as pa
from pyarrow import Table

from tradingstrategy.chain import ChainId
from tradingstrategy.environment.base import Environment, download_with_progress_plain
from tradingstrategy.environment.config import Configuration
from tradingstrategy.environment.jupyter import (
    JupyterEnvironment,
    download_with_tqdm_progress_bar, DEFAULT_SETTINGS_PATH,
)
from tradingstrategy.exchange import ExchangeUniverse
from tradingstrategy.timebucket import TimeBucket
from tradingstrategy.transport.cache import CachedHTTPTransport, DataNotAvailable

logger = logging.getLogger(__name__)


RETRY_DELAY: Final[int] = 30  # seconds

MAX_ATTEMPTS: Final[int] = 3


def _retry_corrupted_parquet_fetch(method):
    """A helper decorator to down with download/Parquet corruption issues.

    Attempt download and read 3 times. If download is corrpted, clear caches.
    """
    # https://stackoverflow.com/a/36944992/315168
    @wraps(method)
    def impl(self, *method_args, **method_kwargs):
        attempts = MAX_ATTEMPTS
        while attempts > 0:
            try:
                return method(self, *method_args, **method_kwargs)
            # TODO: Build expection list over the time by
            # observing issues in production
            except (OSError, BrokenData) as e:
                # This happens when we download Parquet file, but it is missing half
                # e.g. due to interrupted download
                attempts -= 1
                path_to_remove = e.path if isinstance(e, BrokenData) else None

                if attempts > 0:
                    logger.error("Damaged Parquet file fetch detected for method %s, attempting to re-fetch. Error was: %s", method, e)
                    logger.exception(e)

                    self.clear_caches(filename=path_to_remove)

                    logger.info(
                        f"Next parquet download retry in {RETRY_DELAY} seconds, "
                        f"{attempts}/{MAX_ATTEMPTS} attempt(s) left"
                    )
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error(
                        f"Exhausted all {MAX_ATTEMPTS} attempts, fetching parquet data failed."
                    )
                    self.clear_caches(filename=path_to_remove)
                    raise

        raise AssertionError(f"Should not be reached. Download issue on {self}, {attempts} / {MAX_ATTEMPTS}, {method_args}, {method_kwargs}")

    return impl


class BaseClient(ABC):
    """Base class for all real and test mocks clients."""

    # TODO: Move to its own module, add rest of the methods

    @abstractmethod
    def clear_caches(self, fname: str | None):
        pass


class Client(BaseClient):
    """An API client for querying the Trading Strategy datasets from a server.

    - The client will download datasets.

    - In-built disk cache is offered, so that large datasets are not redownloaded
      unnecessarily.

    - There is protection against network errors: dataset downloads are retries in the case of
      data corruption errors.

    - Nice download progress bar will be displayed (when possible)

    You can :py:class:`Client` either in

    - Jupyter Notebook environments - see :ref:`tutorial` for an example

    - Python application environments, see an example below

    - Integration tests - see :py:meth:`Client.create_test_client`

    Python application usage:

    .. code-block:: python

        import os

        trading_strategy_api_key = os.environ["TRADING_STRATEGY_API_KEY"]
        client = Client.create_live_client(api_key)
        exchanges = client.fetch_exchange_universe()
        print(f"Dataset contains {len(exchange_universe.exchanges)} exchanges")

    """

    def __init__(self, env: Environment, transport: CachedHTTPTransport):
        """Do not call constructor directly, but use one of create methods. """
        self.env = env
        self.transport = transport

    def close(self):
        """Close the streams of underlying transport."""
        self.transport.close()

    def clear_caches(self, filename: Optional[Union[str, Path]] = None):
        """Remove any cached data.

        Cache is specific to the current transport.

        :param filename:
            If given, remove only that specific file, otherwise clear all cached data.
        """
        self.transport.purge_cache(filename)

    @_retry_corrupted_parquet_fetch
    def fetch_pair_universe(self) -> pa.Table:
        """Fetch pair universe from local cache or the candle server.

        The compressed file size is around 5 megabytes.

        If the download seems to be corrupted, it will be attempted 3 times.
        """
        path = self.transport.fetch_pair_universe()
        return read_parquet(path)

    def fetch_exchange_universe(self) -> ExchangeUniverse:
        """Fetch list of all exchanges form the :term:`dataset server`.
        """
        path = self.transport.fetch_exchange_universe()
        with path.open("rt", encoding="utf-8") as inp:
            data = inp.read()
            try:
                return ExchangeUniverse.from_json(data)
            except JSONDecodeError as e:
                raise RuntimeError(f"Could not read ExchangeUniverse JSON file {path}\nData is {data}") from e

    @_retry_corrupted_parquet_fetch
    def fetch_all_candles(self, bucket: TimeBucket) -> pyarrow.Table:
        """Get cached blob of candle data of a certain candle width.

        The returned data can be between several hundreds of megabytes to several gigabytes
        and is cached locally.

        The returned data is saved in PyArrow Parquet format.

        For more information see :py:class:`tradingstrategy.candle.Candle`.

        If the download seems to be corrupted, it will be attempted 3 times.
        """
        path = self.transport.fetch_candles_all_time(bucket)
        assert path is not None, "fetch_candles_all_time() returned None"
        return read_parquet(path)

    def fetch_candles_by_pair_ids(self,
          pair_ids: Collection[PrimaryKey],
          bucket: TimeBucket,
          start_time: Optional[datetime.datetime | pd.Timestamp] = None,
          end_time: Optional[datetime.datetime | pd.Timestamp] = None,
          max_bytes: Optional[int] = None,
          progress_bar_description: Optional[str] = None,
        ) -> pd.DataFrame:
        """Fetch candles for particular trading pairs.

        This is right API to use if you want data only for a single
        or few trading pairs. If the number
        of trading pair is small, this download is much more lightweight
        than Parquet dataset download.

        The fetch is performed using JSONL API endpoint. This endpoint
        always returns real-time information.

        :param pair_ids:
            Trading pairs internal ids we query data for.
            Get internal ids from pair dataset.

        :param time_bucket:
            Candle time frame

        :param start_time:
            All candles after this.
            If not given start from genesis.

        :param end_time:
            All candles before this

        :param max_bytes:
            Limit the streaming response size

        :param progress_bar_description:
            Display on download progress bar.

        :return:
            Candles dataframe

        :raise tradingstrategy.transport.jsonl.JSONLMaxResponseSizeExceeded:
                If the max_bytes limit is breached
        """

        if isinstance(start_time, pd.Timestamp):
            start_time = start_time.to_pydatetime()

        if isinstance(end_time, pd.Timestamp):
            end_time = end_time.to_pydatetime()

        assert len(pair_ids) > 0

        return self.transport.fetch_candles_by_pair_ids(
            pair_ids,
            bucket,
            start_time,
            end_time,
            max_bytes=max_bytes,
            progress_bar_description=progress_bar_description,
        )

    def fetch_tvl_by_pair_ids(self,
        pair_ids: Collection[PrimaryKey],
        bucket: TimeBucket,
        start_time: Optional[AnyTimestamp] = None,
        end_time: Optional[AnyTimestamp] = None,
        progress_bar_description: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch TVL/liquidity candles for particular trading pairs.

        This is right API to use if you want data only for a single
        or few trading pairs. If the number
        of trading pair is small, this download is much more lightweight
        than Parquet dataset download.

        The returned TVL/liquidity data is converted to US dollars by the server.

        .. note ::

            TVL data is an estimation. Malicious tokens are known to manipulate
            their TVL/liquidity/market depth, and it is not possible
            to detect and eliminate all manipulations.

        Example:

        .. code-block:: python

            exchange_universe = client.fetch_exchange_universe()
            pairs_df = client.fetch_pair_universe().to_pandas()

            pair_universe = PandasPairUniverse(
                pairs_df,
                exchange_universe=exchange_universe,
            )

            pair = pair_universe.get_pair_by_human_description(
                (ChainId.ethereum, "uniswap-v3", "WETH", "USDC", 0.0005)
            )

            pair_2 = pair_universe.get_pair_by_human_description(
                (ChainId.ethereum, "uniswap-v2", "WETH", "USDC")
            )

            start = datetime.datetime(2024, 1, 1)
            end = datetime.datetime(2024, 2, 1)

            liquidity_df = client.fetch_tvl_by_pair_ids(
                [pair.pair_id, pair_2.pair_id],
                TimeBucket.d1,
                start_time=start,
                end_time=end,
            )

        :param pair_ids:
            Trading pairs internal ids we query data for.
            Get internal ids from pair dataset.

        :param bucket:
            Candle time frame.

            Ask `TimeBucker.d1` or higher. TVL data may not be indexed for
            for lower timeframes.

        :param start_time:
            All candles after this.
            If not given start from genesis.

        :param end_time:
            All candles before this

        :param progress_bar_description:
            Display a download progress bar using `tqdm_loggable` if given.

        :return:
            TVL dataframe.

            Has columns "open", "high", "low", "close", "pair_id" presenting
            TVL at the different points of time. The index is `DateTimeIndex`.

            This data is not forward filled.

        """

        assert bucket >= TimeBucket.d1, f"It does not make sense to fetch TVL/liquidity data with higher frequency than a day,got {bucket}"

        if isinstance(start_time, pd.Timestamp):
            start_time = start_time.to_pydatetime()

        if isinstance(end_time, pd.Timestamp):
            end_time = end_time.to_pydatetime()

        assert len(pair_ids) > 0

        return self.transport.fetch_tvl_by_pair_ids(
            pair_ids,
            bucket,
            start_time,
            end_time,
            progress_bar_description=progress_bar_description,
        )

    def fetch_clmm_liquidity_provision_candles_by_pair_ids(self,
        pair_ids: Collection[PrimaryKey],
        bucket: TimeBucket,
        start_time: Optional[AnyTimestamp] = None,
        end_time: Optional[AnyTimestamp] = None,
        progress_bar_description: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch CLMM liquidity provision candles..

        Get Uniswap v3 liquidity provision data for liquidity provider position backtesting.

        - Designed to be used with `Demeter backtesting framework <https://github.com/zelos-alpha/demeter/tree/master/demeter>`__ but works with others.

        - For the candles format see :py:mod:`tradingstrategy.clmm`.

        - Responses are cached on the local file system

        :param pair_ids:
            Trading pairs internal ids we query data for.
            Get internal ids from pair dataset.

            Only works with Uniswap v3 pairs.

        :param bucket:
            Candle time frame.

            Ask `TimeBucker.d1` or higher. TVL data may not be indexed for
            for lower timeframes.

        :param start_time:
            All candles after this.

            Inclusive.

        :param end_time:
            All candles before this.

            Inclusive.

        :param progress_bar_description:
            Display a download progress bar using `tqdm_loggable` if given.

        :return:
            CLMM dataframe.

            See :py:mod:`tradingstrategy.clmm` for details.
        """

        assert bucket <= TimeBucket.d1, f"It does not make sense to fetch CLMM data with higher frequency than a 1 day, got {bucket}"

        if isinstance(start_time, pd.Timestamp):
            start_time = start_time.to_pydatetime()

        if isinstance(end_time, pd.Timestamp):
            end_time = end_time.to_pydatetime()

        assert len(pair_ids) > 0

        return self.transport.fetch_clmm_liquidity_provision_candles_by_pair_ids(
            pair_ids,
            bucket,
            start_time,
            end_time,
            progress_bar_description=progress_bar_description,
        )

    def fetch_trading_data_availability(self,
          pair_ids: Collection[PrimaryKey],
          bucket: TimeBucket,
        ) -> Dict[PrimaryKey, TradingPairDataAvailability]:
        """Check the trading data availability at oracle's real time market feed endpoint.

        - Trading Strategy oracle uses sparse data format where candles
          with zero trades are not generated. This is better suited
          for illiquid DEX markets with few trades.

        - Because of sparse data format, we do not know if there is a last
          candle available - candle may not be available yet or there might not be trades
          to generate a candle

        This endpoint allows to check the trading data availability for multiple of trading pairs.

        Example:

        .. code-block:: python

            exchange_universe = client.fetch_exchange_universe()
            pairs_df = client.fetch_pair_universe().to_pandas()

            # Create filtered exchange and pair data
            exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")
            pair_universe = PandasPairUniverse.create_pair_universe(
                    pairs_df,
                    [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")]
                )

            pair = pair_universe.get_single()

            # Get the latest candle availability for BNB-BUSD pair
            pairs_availability = client.fetch_trading_data_availability({pair.pair_id}, TimeBucket.m15)

        :param pair_ids:
            Trading pairs internal ids we query data for.
            Get internal ids from pair dataset.

        :param time_bucket:
            Candle time frame

        :return:
            Map of pairs -> their trading data availability

        """
        return self.transport.fetch_trading_data_availability(
            pair_ids,
            bucket,
        )

    def fetch_candle_dataset(self, bucket: TimeBucket) -> Path:
        """Fetch candle data from the server.

        Do not attempt to decode the Parquet file to the memory,
        but instead of return raw
        """
        path = self.transport.fetch_candles_all_time(bucket)
        return path
    
    def fetch_lending_candles_by_reserve_id(
        self,
        reserve_id: PrimaryKey,
        bucket: TimeBucket,
        candle_type: LendingCandleType = LendingCandleType.variable_borrow_apr,
        start_time: Optional[datetime.datetime] = None,
        end_time: Optional[datetime.datetime] = None,
    ) -> pd.DataFrame:
        """Fetch lending candles for a particular reserve.

        :param reserve_id:
            Lending reserve's internal id we query data for.
            Get internal id from lending reserve universe dataset.

        :param bucket:
            Candle time frame.

        :param candle_type:
            Lending candle type.

        :param start_time:
            All candles after this.
            If not given start from genesis.

        :param end_time:
            All candles before this

        :return:
            Lending candles dataframe
        """
        if bucket.to_pandas_timedelta() < pd.Timedelta("1h"):
            bucket = TimeBucket.h1

        return self.transport.fetch_lending_candles_by_reserve_id(
            reserve_id,
            bucket,
            candle_type,
            start_time,
            end_time,
        )

    def fetch_lending_candles_for_universe(
        self,
        lending_reserve_universe: LendingReserveUniverse,
        bucket: TimeBucket,
        candle_types: Collection[LendingCandleType] = (LendingCandleType.variable_borrow_apr, LendingCandleType.supply_apr),
        start_time: datetime.datetime | pd.Timestamp = None,
        end_time: datetime.datetime | pd.Timestamp = None,
        construct_timestamp_column=True,
        progress_bar_description: str | None=None,
    ) -> LendingCandleResult:
        """Load lending reservers for several assets as once.

        - Display a progress bar during download

        - For usage examples see :py:class:`tradingstrategy.lending.LendingCandleUniverse`.

        .. note ::

            This download method is still upoptimised due to small number of reserves

        :param candle_types:
            Data for candle types to load

        :param construct_timestamp_column:
            After loading data, create "timestamp" series based on the index.

            We need to convert index to column if we are going to have
            several reserves in :py:class:`tradingstrategy.lending.LendingCandleUniverse`.

        :param progress_bar_description:
            Override the default progress bar description.

        :return:
            Dictionary of dataframes.

            One DataFrame per candle type we asked for.
        """

        # TODO: Replace the current loaded with JSONL based one to have better progress bar

        assert isinstance(lending_reserve_universe, LendingReserveUniverse)
        assert isinstance(bucket, TimeBucket)
        assert type(candle_types) in (list, tuple,)

        result = {}

        if lending_reserve_universe.get_count() > 30:
            logger.warning("This method is not designed to load data for long list of reserves.\n"
                           "Currently loading data for %s reverses.",
                           lending_reserve_universe.get_count()
                           )


        total = len(candle_types) * lending_reserve_universe.get_count()

        if not progress_bar_description:
            progress_bar_description = "Downloading lending rates"

        with tqdm(desc=progress_bar_description, total=total) as progress_bar:
            # Perform data load by issung several HTTP requests,
            # one for each reserve and candle type
            for candle_type in candle_types:

                bits = []

                for reserve in lending_reserve_universe.iterate_reserves():
                    progress_bar.set_postfix({"Asset": reserve.asset_symbol})
                    try:
                        piece = self.fetch_lending_candles_by_reserve_id(
                            reserve.reserve_id,
                            bucket,
                            candle_type,
                            start_time,
                            end_time,
                        )
                        bits.append(piece)
                    except DataNotAvailable as e:
                        # Some of the reserves do not have full data available yet
                        logger.warning(
                            "Lending candles could not be fetch for reserve: %s, bucket: %s, candle: %s, start: %s, end: %s, error: %s",
                            reserve,
                            bucket,
                            candle_type,
                            start_time,
                            end_time,
                            e,
                        )

                    progress_bar.update()

                if len(bits) == 0:
                    raise DataNotAvailable("No data available for any of the reserves. Check the logs for details.")

                data = pd.concat(bits)

                if construct_timestamp_column:
                    data["timestamp"] = data.index.to_series()

                result[candle_type] = data

        return result

    @_retry_corrupted_parquet_fetch
    def fetch_all_liquidity_samples(self, bucket: TimeBucket) -> Table:
        """Get cached blob of liquidity events of a certain time window.

        The returned data can be between several hundreds of megabytes to several gigabytes
        and is cached locally.

        The returned data is saved in PyArrow Parquet format.
        
        For more information see :py:class:`tradingstrategy.liquidity.XYLiquidity`.

        If the download seems to be corrupted, it will be attempted 3 times.
        """
        path = self.transport.fetch_liquidity_all_time(bucket)
        return read_parquet(path)

    @_retry_corrupted_parquet_fetch
    def fetch_lending_reserve_universe(self) -> LendingReserveUniverse:
        """Load a cache the lending reserve universe.
        """
        path = self.transport.fetch_lending_reserve_universe()

        try:
            return LendingReserveUniverse.from_json(path.read_text())
        except JSONDecodeError as e:
            raise RuntimeError(f"Could not read JSON file {path}") from e

    @_retry_corrupted_parquet_fetch
    def fetch_lending_reserves_all_time(self) -> Table:
        """Get a cached blob of lending protocol reserve events and precomupted stats.

        The returned data can be between several hundreds of megabytes to several
        gigabytes in size, and is cached locally.

        Note that at present the only available data is for the AAVE v3 lending
        protocol.

        The returned data is saved in a PyArrow Parquet format.

        If the download seems to be corrupted, it will be attempted 3 times.
        """
        path = self.transport.fetch_lending_reserves_all_time()
        assert path
        assert os.path.exists(path)
        return read_parquet(path)

    def fetch_chain_status(self, chain_id: ChainId) -> dict:
        """Get live information about how a certain blockchain indexing and candle creation is doing."""
        return self.transport.fetch_chain_status(chain_id.value)

    @classmethod
    def preflight_check(cls):
        """Checks that everything is in ok to run the notebook"""

        # Work around Google Colab shipping with old Pandas
        # https://stackoverflow.com/questions/11887762/how-do-i-compare-version-numbers-in-python
        import pandas
        from packaging import version
        pandas_version = version.parse(pandas.__version__)
        assert pandas_version >= version.parse("1.3"), f"Pandas 1.3.0 or greater is needed. You have {pandas.__version__}. If you are running this notebook in Google Colab and this is the first run, you need to choose Runtime > Restart and run all from the menu to force the server to load newly installed version of Pandas library."

    @classmethod
    def setup_notebook(cls):
        """Legacy."""
        warnings.warn('This method is deprecated. Use tradeexecutor.utils.notebook module', DeprecationWarning, stacklevel=2)
        # https://stackoverflow.com/a/51955985/315168
        try:
            import matplotlib as mpl
            mpl.rcParams['figure.dpi'] = 600
        except ImportError:
            pass

    @classmethod
    async def create_pyodide_client_async(cls,
                                    cache_path: Optional[str] = None,
                                    api_key: Optional[str] = PYODIDE_API_KEY,
                                    remember_key=False) -> "Client":
        """Create a new API client inside Pyodide enviroment.

        `More information about Pyodide project / running Python in a browser <https://pyodide.org/>`_.

        :param cache_path:
            Virtual file system path

        :param cache_api_key:
            The API key used with the server downloads.
            A special hardcoded API key is used to identify Pyodide
            client and its XmlHttpRequests. A referral
            check for these requests is performed.

        :param remember_key:
            Store the API key in IndexDB for the future use

        :return:
            pass
        """
        from tradingstrategy.environment.jupyterlite import IndexDB

        # Store API
        if remember_key:

            db = IndexDB()

            if api_key:
                await db.set_file("api_key", api_key)

            else:
                api_key = await db.get_file("api_key")

        return cls.create_jupyter_client(cache_path, api_key, pyodide=True)

    @classmethod
    def create_jupyter_client(cls,
                              cache_path: Optional[str] = None,
                              api_key: Optional[str] = None,
                              pyodide=None,
                              settings_path=DEFAULT_SETTINGS_PATH,
                              ) -> "Client":
        """Create a new API client.

        This function is intended to be used from Jupyter notebooks

        - Any local or server-side IPython session

        - JupyterLite notebooks

        :param api_key:
            If not given, do an interactive API key set up in the Jupyter notebook
            while it is being run.

        :param cache_path:
            Where downloaded datasets are stored. Defaults to `~/.cache`.

        :param pyodide:
            Detect the use of this library inside Pyodide / JupyterLite.
            If `None` then autodetect Pyodide presence,
            otherwise can be forced with `True`.

        :param settings_path:
            Where do we write our settings file.

            Set ``None`` to disable settings file in Docker/web browser environments.

        """

        if pyodide is None:
            pyodide = is_pyodide()

        cls.preflight_check()
        env = JupyterEnvironment(settings_path=settings_path)

        # Try Pyodide default key
        if not api_key:
            if pyodide:
                api_key = PYODIDE_API_KEY

        # Try file system stored API key,
        # if not prompt interactively
        if not api_key:
            assert settings_path, \
                "Trading Strategy API key not given as TRADING_STRATEGY_API_KEY environment variable or an argument.\n" \
                "Interactive setup is disabled for this data client.\n" \
                "Cannot continue."

            config = env.setup_on_demand(api_key=api_key)
            api_key = config.api_key

        cache_path = cache_path or env.get_cache_path()
        transport = CachedHTTPTransport(
            download_with_tqdm_progress_bar,
            cache_path=cache_path,
            api_key=api_key)
        return Client(env, transport)

    @classmethod
    def create_test_client(cls, cache_path=None) -> "Client":
        """Create a new Trading Strategy client to be used with automated test suites.

        Reads the API key from the environment variable `TRADING_STRATEGY_API_KEY`.
        A temporary folder is used as a cache path.

        By default, the test client caches data under `/tmp` folder.
        Tests do not clear this folder between test runs, to make tests faster.
        """
        if cache_path:
            os.makedirs(cache_path, exist_ok=True)
        else:
            cache_path = tempfile.mkdtemp()

        api_key = os.environ.get("TRADING_STRATEGY_API_KEY")
        assert api_key, "Unit test data client cannot be created without TRADING_STRATEGY_API_KEY env"

        env = JupyterEnvironment(cache_path=cache_path, settings_path=None)
        config = Configuration(api_key=api_key)
        transport = CachedHTTPTransport(download_with_progress_plain, "https://tradingstrategy.ai/api", api_key=config.api_key, cache_path=env.get_cache_path(), timeout=15)
        return Client(env, transport)

    @classmethod
    def create_live_client(
        cls,
        api_key: Optional[str] = None,
        cache_path: Optional[Path] = None,
        settings_path: Path | None = DEFAULT_SETTINGS_PATH,
    ) -> "Client":
        """Create a live trading instance of the client.

        - The live client is non-interactive and logs using Python logger

        - No interactive progress bars are set up

        :param api_key:
            Trading Strategy oracle API key, starts with `secret-token:tradingstrategy-...`

        :param cache_path:
            Where downloaded datasets are stored. Defaults to `~/.cache`.

        :param settings_path:
            Where do we write our settings file.

            Set ``None`` to disable settings file in Docker environments.
        """

        cls.preflight_check()

        if settings_path is None:
            assert api_key, "Either API key or settings file must be given"

        env = JupyterEnvironment(settings_path=settings_path)
        if cache_path:
            cache_path = cache_path.as_posix()
        else:
            cache_path = env.get_cache_path()

        config = Configuration(api_key)

        transport = CachedHTTPTransport(
            download_with_progress_plain,
            "https://tradingstrategy.ai/api",
            cache_path=cache_path,
            api_key=config.api_key,
            add_exception_hook=False)

        return Client(env, transport)
