# Current

- Add: `wrangle.normalise_volume` to deal with different volume formats of Uniswap v2 and v3 
- Add: Support for Coingecko data loading and metadata cross referencing. See `tradingstrategy.alternative_data.coingecko`.
- Add: `tradingstrategy.alternative_data.coingecko.categorise_pairs()` to tag trading pair data with their CoinGecko category
- Add: `deduplicate_pairs_by_volume()` to make it easier to construct trading pair baskets from open-ended universes 

# 0.24.3

- Fix: Allow to run without Jupyter notebook/IPython installed. Make sure you use `Client.create_live_client()` instead of `Client.create_jupyter_client()`
- Fix: Python version pindown `<3.13` instead of `<=3.12` as the pip did not allow minor versions like `3.12.7`

# 0.24.2

- Fix: Bad import line in `tradingstrategty.utils.wrangle` making the library not to import properly

# 0.24.1

- Add: `Client.fetch_top_pairs()` - create a helper function to create always expanding trading universe for external signal providers
- Add: `forward_fill(forward_fill_until)`. The default behavior is to forward fill gaps between first and last candle. However the last candle might not be updated if we load live sparse data and there has been no trades (no candles). Force the forward fill to go until a certain timestamp.
- Fix: Remove `eth_defi` imports, an optional dependency, in the core library  

# 0.24

- Dependencies: Upgrade to Pandas 2.x. [NumPy 2.x is still incompatible](https://stackoverflow.com/questions/78634235/numpy-dtype-size-changed-may-indicate-binary-incompatibility-expected-96-from).
- Add: `aggregate_ohlcv_across_pairs()`: Aggregate volumen-weighted open/high/low/close/volume/liquidity across multiple trading pairs to create unified view of volume and liquidity for a single base token
- Add: `Client.fetch_tvl_by_pair_ids()` to allow TVL/liquidity data loading for selected trading pairs
- Add: `examine_price_between_time_anomalies()`- anomaly examination if open/close between days is a strange value
- Add: `fix_prices_in_between_time_frames()`- to heal broken open/close entries caused by MEV bots
- Add: `remove_min_max_price()`: Remove candles where open value is outside the floating point range
- Add: `Client.fetch_clmm_liquidity_provision_candles_by_pair_ids()` - CLMM candle fetching, needed for Demeter based LP backtests

# 0.23

- Add: `fix_dex_price_data` for wrangling DEX price feeds and separate reusable `wrangle` module
- Add: `resample_dataframe` utility function. Useful for resampling dataframes with multiple columns that are not candlestick data 
- Add: `fix_bad_wicks(bad_open_close_threshold)` to massage the data - There are ~60 broken open price data points for U/ni v3 price feeds, cause unknown
- Add: `filter_for_blacklisted_tokens`
- Add: `DEXPair.from_series` helper
- Add: `filter_pairs_default()` that will remove known bad tokens from the trading pair universe with multiple heurestics rules
- Add: `build_liquidity_summary()` for creating trading universes based on the available liquidity
- Internal change: Move trading pair filtering logic to its own module

# 0.22.14

- Fix: Fix for inconcistencies in lending data downloads

# 0.22.13

- Update: Having pd.MultiIndex instead of per-pair pd.DateTimeIndex on candles slows down price access on backtesting considerably
- Update: Binance progress bar now shows individual progress for each pair when there are less than 5 pairs

# 0.22.12

- Update `fetch_all_spot_symbols` API call to Binance
- Add: `LendingReserveUniverse.limit_to_protocol()`
- Fix: If there is an API key error when saving exchange universe, abort the operation with an exception

# 0.22.11

- Add support for Aave v2 dataset

# 0.22.10

- Change `pyarrow` schemas of `Candle` and `XYLiquidity`to use double precision (float64) instead of float32
- Add `GroupedCandleUniverse(forward_fill)` option to the constructor
- Add `PandasPairUniverse.iterate_tokens()`
- Add `get_prior_timestamp(series: pd.Series, ts: pd.Timestamp)` as a separate utility function
- Add `resample_candles(shift)` argument
- Add `format_price(decimals)` argument
- Add `Universe.get_default_chain()` method
- Add `resample_price_series()` function
- Fix Binance data using local timezone
- Fix Binance multipair data resampling not working correctly

# 0.22.9

- Add `remove_zero_candles()` function
- Add `DEXPair.exchange_name` is set if `PandasPairUniverse(exchange_universe)` is given
- Add `ChainId.centralised_exchange = -1` id that is used now for Binance loaded data

# 0.22.8

- Add fee argument to `generate_pairs_for_binance()`

# 0.22.7

- Fix for Binance lending data, change daily rates to annual

# 0.22.6

- Add `PairNotFound(description)` to have better error messages on pair look-up failures
- Stricter input type checks for exchanges when creating `Universe`
- Add `UniswapV2MockClient.clear_caches` for compatibility with `Client` in unit testing

# 0.22.5

- Add `PandasPairUniverse.get_token_by_symbol()`
- Add `PandasPairUniverse.get_token(chain_id)` argument

# 0.22.4

- Update error message for Binance symbols

# 0.22.3

- Add more Binance data sideloading related utility functions to `tradingstrategy.binance.utils`

# 0.22.2

Updates to `BinanceDownloader`

- Add `BinanceDownloader.fetch_approx_asset_trading_start_date()` to figure out when asset was listed on Binance
- Change `BinanceDownloader.fetch_candlestick_data()` and `BinanceDownloader.fetch_lending_rates()` to accept multiple pairs at once
- Add progress bars
- Add symbol validation

# 0.22.1

- Fix `get_by_chain_and_factory()` was returning `ExchangeNotFoundError` instead of raising it 

# 0.22

Binance data downloading added. Python 3.11, Python 3.12 and Pandas 2.x compatibility fixes.

- Add: `BinanceDownloader.fetch_approx_asset_trading_start_date()` to figure out when asset was listed on Binance
- Fix: `BinanceDownloader` timestamps to be UTC
- Add: Example script how to download Binance data for multiple assets and write in a single Parquet file
- Upgrade to support Python 3.12 with breaking `datetime.datetime.utcnow()` changes
- Upgrade to support Pandas 2.x with breaking `pd.Timestamp.utcnow()` changes
- Upgrade to web3-ethereum-defi latest with Python 3.12 compatibilty
- Upgrade to `pyarrow` 14.0 for Python 3.12 compatibility
- `datetime.datetime.utcnow()` and `datetime.datetime.utcfromtimestamp` workarounds - we do not need timezones that unnecessarily bloat data size
- Add `naive_utcnow` for Python 3.12 compatibility
- Add `naive_utcfromtimestamp` for Python 3.12 compatibility
- Change from `pd.Timestamp.utcfromtimestamp()` to `pd.Timestamp.utcfromtimestamp().tz_localize(None)` when needed
- 
# 0.21.1

- Fix `estimate_accrued_interest` crashing when there are gaps in data
- Fix `resample_candles` to retain `pair_id` column if one is present in data

# 0.21

- Add functionality for downloading and manipulating Binance candle and lending data
  to have alternative datasets to benchmark the strategies for overfit and data quality issues
- Add `resample_series()` method to work for single column pd.Series price data
- Fix `CandleUniverse.get_candles_by_pair()` (add return value)
- Fix `resample_candles()` method

# 0.20.18

- API update: `CandleUniverse.get_candles_by_pair()` accepts `DEXPair` besides `pair_id` as an argument
- `forward_fill()` can handle `volume` column

# 0.20.17

- Fix: DataFrame copy warning in `estimate_accrued_interest`
- Add: `get_price_with_tolerance(pair_name_hint)` to have better exception messages 
- Fix: `CandleSampleUnavailable` exception message was tuple instead of newline separated string

# 0.20.16

- Add `LendingMetricUniverse.estimate_accrued_interest` so we can estimate the position cost and interest profit
  in backtesting

# 0.20.15

- Update candle mappings so volume doesn't get set to None

# 0.20.14

- Internal change: move `make_clickable` to module level, so it can be reused

# 0.20.13

- API change: `Client.create_live_client(settings_file_path)`
  argument allows to disable and reading of settings file,
  to avoid any confusion in Dockerised environments
- Internal change: Unit tests do not try to poke settings file 
  anymore

# 0.20.12

- Internal change: Use `parquet.read_table(memory_map=True)` to decrease RAM usage

# 0.20.11

- Add: `LendingReserveUniverse.limit_to_assets()`
- Add: `filter_for_trading_fee(pairs_dataframe)`
- Internal change: Add more asserts to `Universe` creation to catch human mistakes early
- API change: `get_by_chain_and_symbol()` raises `UnknownLendingReserve` instead of returning `None`
- Add more asserts to `Universe` creation to catch human mistakes early

# 0.20.10

- Remove duplicate timestamp label on visualisation  

# 0.20.9

- Add: `DEXPair.get_link()` to get a direct link to the trading pair page
- Add: `LendingReserve.get_link()` to get a direct link to the trading pair page
- Add: `DEXPair.is_tradeable()` for quick checks if a pair has decent volume
- Add: `PandasPairUniverse.get_exchange_for_pair()`
- Add: `LendingMetricUniverse.get_single_value()`
- Add: Aave Ghost (GHST) stablecoin
- Add: Jarvis Synthetic Euro (jEUR) stablecoin
- Add: `format_links_for_html_output()` helper to work with `DataFrame` objects
- API update: `get_pair_by_human_description()` can have exchange slug set to `None`
  matching the best trading pair across with the lowest fee across all DEXes
- API update: `get_closest_pair(pair)` can now now take `DEXPair` instead of a primary key
  as a lookup parameter and also display the pair name in error message
- Various exception message improvements
- Remove duplicate timestamp from candle labels

# 0.20.8

- Add: Progress bar to lending rate download
- Fix: JSONL download progress bar was missing the last refresh, leaving the progress bar a bit under 100%
- Use `tqdm_loggable` package everywhere to have more control over progress bars

# 0.20.6

- Multiple API changes to make working with lending easier
- Lending universe construction examples
- Add: `filter_for_base_tokens()`
- Add: `filter_for_chain()`
- Add: `DEXPair.get_base_token()` and `DEXPair.get_quote_token()`
- Add: `LendingReserveUniverse.can_leverage(token)`
- Add: `LendingReserveUniverse.limit_to_chain()`
- API update: Make all `Universe` members optional, as we can have lending only universes 
- API update: `get_rates_by_reserve()` can take a lending reserve object as an argument
- API update: `Universe` checks constructor argument types are correct
- Deprecate: Useless methods on `Universe`

# 0.20.5

- Add concurrent write control to `CachedHTTPTransport` to allow
  multiple processes to write the same cached download file on the same system
- Enable parallel tests

# 0.20.4

- Rename `UniswapV2MockClient.initialise_mock_data` to be more descriptive

# 0.20.3

- Refactor some common code from GenericMockClient and UniswapV2MockClient into MockClient. Note: GenericMockClient resides in tradeexecutor due to its reliance on certain imports
- More lending data points

# 0.20.2

- Add vToken details to Aave lending reserves
- Add liquidation threshold and such denormalised information
  to Aave lending reserves

# 0.20.1

- Fix: Lending rates cache bug - different datasets were using
  the same cache filename

# 0.20

- Add lending reserves and candles universe.
  Now you can easily download and explore Aave v3 lending rates 
  with functions available in `tradingstrategy.lending` module

# 0.19

- Change `PandasPairUniverse.get_pair_by_human_description()` signature
  to be easier to use
- More user friendly data not available / 404 error handling for lending candles 

# 0.18.2

- Add `exchange_universe` argument to `get_pair()`

# 0.18.1

- `GroupedCandleUniverse.create_from_single_pair_dataframe(time_bucket)` parameter added
- `TimeBucket.from_pandas_timedelta()` added

# 0.18

- Add support for fetching lending candle data with `fetch_lending_candles_by_reserve_id()`
- Rename `fetch_all_lending_protocol_reserves()` to `fetch_all_lending_reserves()`
- Delete `summarydataframe.py` and its dependents. This includes `backtrader.py` and the `TradeSummary.to_dataframe` method.
  Backtrader has been unmaintained for a while.

# 0.17.6

- [Add loaded trading pair data preprocesing and working around Parquet data problems](https://github.com/tradingstrategy-ai/trading-strategy/issues/104)
- Add `DataDecodeFailed(Exception)` that gives more context information if there is something wrong
  with the trading pair

# 0.17.5

- Fix test warnings by changing function calls from `create_single_pair_universe()` to `create_pair_universe()`
- Add `PairNotFoundError` and `ExchangeNotFoundError` for more helpful error messages
- Add `last_supposed_candle_at` data to `fetch_trading_data_availability()`

# 0.17.4

- Fix `fetch_trading_data_availability()` using wrong format for pair id array

# 0.17.3

- `get_one_pair_from_pandas_universe(exchange)` support

# 0.17.2

- Bump [web3-ethereum-defi](https://web3-ethereum-defi.readthedocs.io/) to version 0.21

# 0.17.1

- Add `candle_decimals` parameter `make_candle_labels()` that defaults to showing 4 decimal places on candlestick charts

# 0.17

- Optimisation: `get_price_with_tolerance()`: 
  candle price lookup is much faster with sparse data match
- Added `trading_strategy.utils.forward_fill` module for 
  dealing with sparse data

# 0.16.1

- Optimisation: `get_price_with_tolerance()`: 
  candle price lookup is 40x faster for exact timestamp matches

# 0.16

- API change: Now `get_single_pair_data()` raises `NoDataAvailable` instead
  of returning an empty data frame by default if there is not enough data at source 
- Fix unnecessary upper() in `is_stablecoin_like`
- Fix Anvil chain id not fitting to Parquet data frame (assumed 16 bit uint for chain id)

# 0.15.2

- Add more stablecoins
- Add `DEXPair.volume_30d` shortcut
- Add `DEXPair.from_row` shortcut

# 0.15.1

- Fix: Better error messages when `fetch_trading_data_availability()` cannot process input

# 0.15

- Add `DEXPair.fee_tier` to get the trading pair fee as 0..1 float
- Add `PandasPairUniverse.create_pair_universe`
- Deprecate `PandasPairUniverse.create_single_pair_universe`
- Deprecate `PandasPairUniverse.create_limited_pair_universe`
- Add `USDT.e` and `USDC.e` bridged stablecoins on Avalanche

# 0.14.1

- Add: caching to `get_candles_by_pair()` and `get_single()` methods.
This can result in backtesting time being more than halved. 
- Fix: Bad wick filtering code crashed on empty dataframes

# 0.14

- Add: `tradingstrategy.utils.groupeduniverse.fix_bad_wicks` to deal with candle data where high and low
  values are abnormal due to various price manipulation issues. 
- Update: Automatically filter bad wicks when creating a candle universe
- Add a shortcut function `tradingstrategy.pair.generate_address_columns()` to generate `base_token_address` and `quote_token_address` columns
- Update API: `resolve_pairs_based_on_ticker()` supports `HumanReadableTradingPairDescription`
- Fix: Some error message polish

# 0.13.18

- Have classical `Candle.volume` field as buy volume and sell volume cannot be separated for Uniswap v3

# 0.13.17

- change `resample_candles()` to use timedelta instead of timebucket

# 0.13.16

- Fix JSON data loading for Uni v3 exchanges that lack trade count

# 0.13.15

- Add `fee_tier` option to `get_one_pair_from_pandas_universe()` and `get_pair_by_human_description()`

# 0.13.14

- Bump max pairs to load over JSONL to 75

# 0.13.13

- Chart visualization fixes
  - remove `price_chart_rel_size` and `subplot_rel_size` options (will all be specified in `relative_sizing`)
  - refactor and clean code in `tradingstrategy/charting/candle_chart.py`

# 0.13.12

- Fix SyntaxError caused by a typo

# 0.13.11

- We have now `tradingstrategey.client.BaseClient` as the base class for different client implementations
- Adding `UniswapV2MockClient` implementation that mimics `Client`, but reads all data
  from an EVM test backend. Makes live trading tests much easier.
- Small improvements to PairUniverse

# 0.13.10

- Fix detached indicator charting bugs for different `volume_bar_modes`
- Adds support for different subtitle font sizes
- Adds tests to make to catch charting bugs earlier in the future

# 0.13.9

- Bump depenedncies
- Add `ChainId.anvil`
- Add support for detached technical indicators
- Web3 6.0 fixes

# 0.13.8

- Match dependencies and Python version pindowns with Web3.py 6.0
- Adding [Ethereum Tester](https://github.com/ethereum/eth-tester/) chain id 

# 0.13.7

- Silence pandas warning in `PairGroupedUniverse.get_pair_ids()`
- Rename "Binance Smart Chain" to "BNB Smart Chain"

# 0.13.6

- Add a new method `Client.fetch_all_lending_protocol_reserves()` for fetching data on
  supported decentralized lending protocols (only AAVE v3 at present).
- Improve `validate_ohclv_dataframe()` to check for date/timestamp index as well as column

# 0.13.5

- Fixes for filtering by `chain_id` in `ExchangeUniverse`

# 0.13.4

- Improve `resolve_pairs_based_on_ticker()` to be able to filter pairs with specified fee. Change 
default sorting criteria `buy_volume_all_time` to be function argument for more flexible filtering

# 0.13.3

- Add `GroupedCandleUniverse.get_last_entries_by_pair_and_timestamp(pair_id, timestamp)` for simplified 
  candle data access
- Add `PandasPairUniverse.get_pair(chain_id, exchange_id, base_token, quote_token)` for simplified 
  pair access
- Support passing `exchange_universe` in `PandasPairUniverse` construction, so we have all metadata
  needed to look up pairs
- Fixes to various token data accessor

# 0.13.2
- 
- Same as 0.13.1 - fixing broken release

# 0.13.1

- Support Arbitrum L2 in `ChainId` class

# 0.13.1

A large impact optimisation update.

- Optimise `PandasPairUniver.get_pair_by_id` to cache full `DEXPair` objects to speed up
  strategies using large number of trading pairs

- Make `DEXPair` Python `__slots__` based to make the object property access faster

- Added `ResampledLiquidityUniverse` for faster backtesting with luidity data

- Allow different granularity of OHLCV and liquidity data in `Universe`

# 0.13

- Release failed

# 0.12.5
- 
- Add `LiquidityUniverse.get_liquidity_with_tolerance`

# 0.12.4
- 
- Quick fix on `get_pair_by_human_description` error message

# 0.12.3

- Improve `CandleSampleUnavailable` error messge further to offer friendly advises how to fix
- Improve `ExchangeUniverse` interface with more helpful shorthand methods

# 0.12.2

- Add `ExchangeUniverse.from_collection()`
- Pass `ExchangeUniverse` instance as a part of `Universe.exchange_universe` instead just raw array of
  of `univer.exchanges`

# 0.12.1

- Better multipair lookup when typing out pair names by hand 
- Add `tradingstrategy.pair.HumanReadableTradingPairDescription`
- Add `PandasPairUniverse.get_pair_by_human_description`
- 
# 0.12

- Add default HTTP request retry policy to `Client`
- Add default HTTP user agent to `Client`
- More direct data feed infrastructure
- Fix `create_requests_client(add_exception_hook=True)` not working correctly
- Add `GroupedCandleUniverse.create_from_multiple_candle_datafarames`

# 0.11.1

- Add: Make `DEXPair` hashable
- Fix: Historical candle download for JSONL endpoint - make time ranges to work correctly 
- Change: Make JSONL cache filenames more descriptive 

# 0.11

- Adding `Client.fetch_trading_data_availability` API
- Moved `types.py` to properly Python 3.10 TypeAlias system

# 0.10.2 

- Add `TradeSummary.show` to simplify usage in notebooks

# 0.10.1

- Fix missing dependencies because web3-ethereum-defi data extras
- Fix `make_candle_labels` not working correctly if some keys were not present in the OHCLV DataFrame
- Add `VolumeBarMode` option to `visualise_ohlcv` to allow different rendering modes for volume bars, in-chart 
  or outside chart
- More implementation of direct-to-blockchain node data feeds

# 0.10.0

- Add the initial implementation for direct-to-blockchain node data feeds

# 0.9.1

- Add support for average duration statistic to be expressed in terms of `bars`
- Framework for direct, real-time, date feeds directly from a blockchain node
- Add `uniswap_v2_incompatible` exchange type

# 0.9.0

- Make the default behavior not to return the current candle if asked by timestamp

# 0.8.8

- Switch to `tqdm-loggable` to have better download status behavior on headless configurations
- Fine tune `visualise_ohlcv` label configuration

# 0.8.7

- Fix spelling `visualise_ohlcv`

# 0.8.6

- Add candle charts with `visualise_ohclv`

# 0.8.5

- Optimize `groupeduniverse` class, specifically `get_timestamp_range()` method

- Optimize get pair speed

- Remove the "no Content-Length header" warning if HTTP response lacks file size information

- Fix `get_token()` not returning symbol information

# 0.8.4

- Added `Client.close()` and `CachedHTTPTransport.close()` for explicit closing of streams
- Fix `JUPYTER_PLATFORM_DIRS` warning when importing
- Fixed bunch of Pandas warnings
- Add `pytest -Werror` to ensure the lib does not raise any warnings

# 0.8.3

- Escape hatch for interactive API key setup loop
- Added `Candle.generate_synthetic_sample` for mocking test data
- Added `GroupedCandleUniverse.get_price_with_tolerance` to safely get the latest price
  in backtesting

# 0.8.2

- Make `matplotlib` optional in `Client.setup_notebook`
- Added `DEXPair.exchange_type` to support Uniswap v3 pairs

# 0.8.1

- Change: List `requests` as an explicit dependency for the client
  (Might have been causing issues with Pyodide builds)

# 0.8.0

- Change: Remove a lot of dependencies and cleaning up old code.
  The package with its dependencies should be now much more compact.
  Any old Backtrader or QSTrader code is behind an extra dependency in 
  the Python package definition. There is no longer dependency to 
  Ethereum or crypto packages that had C and were problematic
  install. 
- Change: preparing for Pyodide support.
- Change: Bumped Python 3.10 because 3.10 is minimum for Pyodide

# 0.7.2

- Fix: `PairGroupedUniverse` did not have OHLCV data sorted by `timestamp`, but instead of was
  `pair_id`, `timestamp` causing some failures of managing expectations when accessing data.
- Fix: Update tests to reflect new datasets from Trading Strategy API
- Added `Client.fetch_candles_by_pair_ids` that loads trading data for certain pairs  
  data over JSONL endpoint  and avoids loading large Parquest candle and liquidity files.
  This makes it possible to run backtests on low memory environments.
- Added `Client.fetch_candle_dataset` and `read_parquest(filters)`
  to filter Parquest files when loading candle datasets to optimise memory usage.
  (At the moment this path is not used outside the tests.)
- Change `ChainId` base class to `IntEnum` instead of `Enum` for better type hinting
- Change `PandasPairUniverse.build_index` to use Pandas DataFrame frame transpose,
  greatly speeding up the creation of the index
- Fix: `get_closest_price` gets a closest price even if the timestamp and  
  and time bucket are not in the same internal 
- Added `environment/jupyterlite` for the upcoming WebAssembly integration

# 0.7.1

- Added `is_candle_green()`, `is_candle_red()` helpers
- Added `TimeBucket.to_pandas_timedelta()` helper
- Added `upsample_candles()` helper
- Added `unknown` and `osmosis` blockchains
 
# 0.7

- Add `Token` presentation
- Add `get_single_pair_data` shortcut
- Add `PandasPairUniverse.get_by_symbols` shortcut
- Add `PandasPairUniverse.get_all_tokens` shortcut
- Add `get_exchange_by_id` shortcut
- Add `summarydata.as_duration` Pandas summary table cell formatter
- Fix `filter_for_quote_tokens` to be more strict (was checking either side of the pair, not quote token)
- API change `create_single_pair_universe` now to accept `pick_by_highest_vol`
- API change: Rename `Universe.time_frame` -> `Universe.time_bucket` to be consistent
- Make download progress bars more human friendly 

# 0.6.9

- Add `check_schema` option when creating PyArrow table exports for trading pairs
- Fix type hint for `token1_decimals`
- Better error message for `get_one_pair_from_pandas_universe`
- Make `token0_symbol`  and `token1_symbol` optional as not all tokens have a symbol
- 
# 0.6.8

- Patch the previous release to `token0_decimals` and `token1_decimals` instead of `base_token_decimals` to be more aligned with the other Uniswap pair data
- Cleaning up [API and API documentation](https://tradingstrategy.ai/docs/programming/index.html#api-documentation)

# 0.6.7

- Export `base_token_decimals` and `quote_token_decimals` in [trading pair datasets](https://tradingstrategy.ai/docs/programming/api/pair.html)

# 0.6.6

- New time points for the [time buckets](https://tradingstrategy.ai/docs/programming/api/timebucket.html) 

# 0.6.5

- Add `exchange_address` as a part of [trading pair datasets](https://tradingstrategy.ai/docs/programming/api/pair.html)
- Add `get_by_chain_and_factory()` to [decentralised exchange universe](https://tradingstrategy.ai/docs/programming/api/exchange.html)

# 0.6.4

- Add `buy_tax`, `transfer_tax`, `sell_tax` to [trading pair datasets](https://tradingstrategy.ai/docs/programming/api/pair.html)

# 0.6.3

- Fix download retries in live trading
- Add `stablecoin` module for upcoming stablecoin support fuctions
- Add `DEXPair.convert_to_dataframe` 
- Add `filter_for_stablecoins` 
  

# 0.6.2

- Fix broken build

# 0.6.1

- Fix Macbook M1, macOS, arm64 compatibility. Updated Scipy, Pyarrow and Numpy dependencies.

# 0.6

- Renaming the package `tradingstrategy` -> `trading-strategy` to be consistent with the package naming practices.
  [See the new Github repository](https://github.com/tradingstrategy-ai/trading-strategy).

# 0.5.3

- Fix download responses being buffered in CLI
- Test for `get_trading_pair_page_url()` in the downloaded dataset
- Added support for Ganacher tester chain

# 0.5.2

- Added `Client.create_live_client` for live trading
- More helper functions to help unit testing
- `tradingstrategy.utils.time` to check for incompatible `pandas.Timestamp` formats
- Added `tradingstrategy.pair.filter_for_quote_tokens`
- Added `get_all_samples_by_range`, `iterate_samples_by_pair_range` shortcuts to consume trading pair data in strategies
- Added `get_prior_timestamp` to calibrate your clock with the existing time index
- Added `Client.clear_caches` to ensure fresh data downloads
- Added `DEXPair.exchange_slug` and `DEXPair.pair_slug` so we can point to web page URLs
- Renamed `Sushiswap` -> `Sushi` as per their branding

# 0.5.1

- Adding chain id 61 for Ethereum Classic / [Ethereum Tester](https://github.com/ethereum/eth-tester/)
- Adding `GroupedCandleUniverse.create_empty()` and `GroupedLiquidityUniverse.create_empty()`

# 0.5.0

This is a release to get Trading Strategy client towards live trading.

- Created `tradingstrategy.universe.Universe` helper class to encapsulate trading universe
- Added convenience method `PairUniverse.create_from_pyarrow_table_with_filters` so creating different trading universes is less lines of code
- Added convenience method `tradingstrategy.utils.groupeduniverse.filter_for_pairs_on_exchanges`
- Added helper methods to construct single trading pair universe, see `PandasPairUniverse.create_single_pair_universe`
- Separate QSTrader to an optional dependency. Please install as `pip install tradingstrategy[qstrader]`.
  This is because QSTrader depends on Seaborn that depends on SciPy that does not work so good on Apple M1 macs.
- Add the default connection and read timeout 15 seconds to all client downloads
 
# 0.4.0

- Reworked how QSTrader framework integration works. QSTrader was originally designed for stock markets, so it required few changes to make it more suitable for 24/7 cryptomarkets. **Warning**: QSTrader and its integration are both in beta. More work is needed to be done e.g. in fee calculations to make the integration smooth. See example on [PancakeSwap momentum trading algorithm](https://tradingstrategy.ai/docs/programming/algorithms/pancakeswap-momentum-naive.html).
- Added [portfolioanalyzer](https://tradingstrategy.ai/docs/programming/api/portfolioanalyzer.html) to make timeline of portfolio construction over the time. See example on [PancakeSwap momentum trading algorithm](https://tradingstrategy.ai/docs/programming/algorithms/pancakeswap-momentum-naive.html).
- Include `seaborn` as a dependency as it is required for `qstrader` plot output, otherwise `qstrader` was crashing.
- `PairUniverse` supports indexed lookups to deal with the high pair count.
- More convenience methods for candle and liquidity manipulation. 


# 0.3.4

- Adding the preliminaty price impact analysis. [See the example notebook](https://tradingstrategy.ai/docs/programming/examples/price-impact.html). [Read the blog post about the liquidity formation](https://tradingstrategy.ai/blog/announcing-support-for-liquidity-charts).

# 0.3.3

- Make fastquant dependency optional, as it was causing installation issues with its ccxt dependency

# 0.3.2

- Updated inline interactive setup from within Jupyter notebook

# 0.3.1

- Polish README, release process and docs

# 0.3.0

- Multiple changes to make the multichain backtesting possible.

- [The documentation code examples](https://tradingstrategy.ai/docs/programming/index.html#code-examples)
  will be updated to reflect multichain support over time and may work incorrectly
  at the moment. [Getting Started](https://tradingstrategy.ai/docs/programming/examples/getting-started.html) 
  tutorial is already updated. 

- This is a major release deprecating activity flags in the trading pair universe.
  The multichain trading pair data is too big to include inactivate trading pairs (800k+ total trading pairs).
  Thus, the pair universe set only contains [active trading pairs](https://tradingstrategy.ai/docs/programming/tracking.html) after this release, 
  making the trading pair universe less than 100k trading pairs again, making it more feasible to download the data.

- `ExchangeUniverse.get_by_chain_and_slug()` is now canonical way to refer to an exchange
 
- `PairUniverse.get_pair_by_ticker_by_exchange()` is now canonical way to refer to a trading pair

- Clarify primary keys may not be stable and should no longer referred permanently 

- `Exchange.homepage` and `Exchange.active_pair_count` fields added
  
# 0.2.14

- Make `ChainId` database more deterministic by loading all supported chains once and only once

# 0.2.13

- Add `chain_name` output field for exchange data
 
# 0.2.12

- Adding slug lookups for chains 

# 0.2.11

- Add Binance Smart Chain and Polygon logos

# 0.2.10

- Fix the default API endpoint URL

# 0.2.9

- Adding embedded chain list repository
- Adding support for Binance Smart Chain

# 0.2.8

- Adding more metadata information on `ChainId` objects

# 0.2.7

- Adding `exchange_slug` and `chain_slug` on the `Exchange` object

# 0.2.6

- Allow to customise color range for the trade success distribution diagram

# 0.2.5

- Added custom `DEXTrader` base class, as the default logic with Backtrader was a bit insufficient 

- Added trade analysis support for Backtrader strategies (earlier was QSTrader only)

- Reworked trade analysis summaries and views 

- Added [advanced example for Double 7 trading strategy](https://tradingstrategy.ai/docs/programming/algorithms/double-7-advanced.html)

# 0.2.4

- Polish API key welcome setup

# 0.2.3

- Fixed not reading settings file from Google Drive