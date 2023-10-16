"""Candle universe loading tests."""

import datetime

import pandas
import pandas as pd
import pytest
from tradingstrategy.candle import GroupedCandleUniverse, is_candle_green, is_candle_red
from tradingstrategy.chain import ChainId
from tradingstrategy.client import Client
from tradingstrategy.pair import LegacyPairUniverse, PandasPairUniverse
from tradingstrategy.reader import read_parquet
from tradingstrategy.timebucket import TimeBucket
from tradingstrategy.transport.jsonl import JSONLMaxResponseSizeExceeded
from tradingstrategy.utils.groupeduniverse import resample_candles


def test_grouped_candles(persistent_test_client: Client):
    """Group downloaded candles by a trading pair."""

    client = persistent_test_client

    exchange_universe = client.fetch_exchange_universe()
    raw_pairs = client.fetch_pair_universe().to_pandas()
    raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()

    pair_universe = PandasPairUniverse(raw_pairs, build_index=False)
    candle_universe = GroupedCandleUniverse(raw_candles)

    # Do some test calculations for a single pair
    sushi_swap = exchange_universe.get_by_chain_and_name(ChainId.ethereum, "sushi")
    sushi_usdt = pair_universe.get_one_pair_from_pandas_universe(sushi_swap.exchange_id, "SUSHI", "USDT")
    assert sushi_usdt.get_trading_pair_page_url() == "https://tradingstrategy.ai/trading-view/ethereum/sushi/sushi-usdt"
    sushi_usdt_candles = candle_universe.get_candles_by_pair(sushi_usdt.pair_id)

    # Get max and min weekly candle of SUSHI-USDT on SushiSwap
    import ipdb ; ipdb.set_trace()
    high_price = sushi_usdt_candles["high"]
    max_price = high_price.max()

    low_price = sushi_usdt_candles["low"]
    min_price = low_price.min()

    # Do a timezone / summer time sanity check
    ts_column = sushi_usdt_candles["timestamp"]
    ts_list = ts_column.to_list()
    sample_timestamp = ts_list[0]
    assert sample_timestamp.tz is None
    assert sample_timestamp.tzinfo is None
    assert sample_timestamp.hour == 0
    assert sample_timestamp.minute == 0

    # Min and max prices of SUSHI-USDT ever
    assert max_price == pytest.approx(24.120058)

    # Depends on real sushi price, let's give up testing this
    # assert min_price == pytest.approx(0.47701323)


def test_empty_candle_universe():
    universe = GroupedCandleUniverse.create_empty()
    assert universe.get_candle_count() == 0


def test_samples_by_timestamp(persistent_test_client: Client):
    """Get all OHLCV candles at a certain timestamp."""

    client = persistent_test_client

    raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()
    candle_universe = GroupedCandleUniverse(raw_candles)

    # The internal weekly start before Dec 2021
    ts = pd.Timestamp("2021-11-29")
    candles = candle_universe.get_all_samples_by_timestamp(ts)
    assert len(candles) > 1000
    assert candles.iloc[0].timestamp == ts
    assert candles.iloc[0].open > 0
    assert candles.iloc[0].close > 0


def test_samples_by_timestamp_range(persistent_test_client: Client):
    """Get samples for multiple pairs by range."""

    client = persistent_test_client

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

    # there is one week between the start and the end
    middle = start + (end - start) / 2

    candles = candle_universe.get_all_samples_by_range(start, end)

    # We have pair data for 3 different weeks
    assert len(candles.index.unique()) == 3

    # Each week has its of candles broken down by a pair
    # and can be unique addressed by their pair_id
    assert len(candles.loc[start]) >= 1000
    assert len(candles.loc[middle]) >= 1000
    assert len(candles.loc[end]) >= 1000


def test_iterate_pairs_by_timestamp_range(persistent_test_client: Client):
    """Iterate pairs candles by given timestamp range."""

    client = persistent_test_client

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
        # In this example, we iterate 3 weeks ranges, so we can have data for
        # 1, 2 or 3 weeks.
        # If there was no data at all pair_id is not present in the result.
        range_start = pair_df.index[0]
        range_end = pair_df.index[-1]
        assert range_start <= range_end
        # Calculate the momentum for the full range of all samples
        first_candle = pair_df.iloc[0]
        last_candle = pair_df.iloc[-1]
        # Calculate
        if last_candle["close"] > 0 and first_candle["open"] > 0:
            try:
                momentum = (last_candle["close"] - first_candle["open"]) / first_candle["open"] - 1
            except RuntimeWarning:
                # RuntimeWarning: overflow encountered in float_scalars
                # float23 calculation may overflow for some pairs
                pass


def test_data_for_single_pair(persistent_test_client: Client):
    """Get data from the single pair candle universe."""

    client = persistent_test_client

    exchange_universe = client.fetch_exchange_universe()
    columnar_pair_table = client.fetch_pair_universe()
    pairs_df = columnar_pair_table.to_pandas()

    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")

    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")]
        )

    pair = pair_universe.get_single()
    assert pair.base_token_symbol == "WBNB"
    assert pair.quote_token_symbol == "BUSD"

    raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()

    # Filter down candles to a single pair
    single_pair_candles = raw_candles.loc[raw_candles["pair_id"] == pair.pair_id]

    candle_universe = GroupedCandleUniverse(single_pair_candles)

    # Get last 10 candles for WBNB-BUSD
    df = candle_universe.get_single_pair_data(sample_count=10)
    assert len(df) == 10
    assert df.iloc[-1]["timestamp"] > pd.Timestamp("2021-1-1")


def test_data_for_two_pairs(persistent_test_client: Client):
    """Get data from the two pair candle universe."""

    client = persistent_test_client

    exchange_universe = client.fetch_exchange_universe()
    columnar_pair_table = client.fetch_pair_universe()
    pairs_df = columnar_pair_table.to_pandas()

    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")

    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD"), (exchange.chain_id, exchange.exchange_slug, "Cake", "WBNB")]
        )

    assert pair_universe.get_count() == 2

    raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()

    # Filter down candles to two pairs
    two_pair_candles = raw_candles.loc[raw_candles["pair_id"].isin(pair_universe.df)]
    candle_universe = GroupedCandleUniverse(two_pair_candles)


def test_candle_colour(persistent_test_client: Client):
    """Green and red candle coloring functions work."""

    client = persistent_test_client

    exchange_universe = client.fetch_exchange_universe()
    columnar_pair_table = client.fetch_pair_universe()
    pairs_df = columnar_pair_table.to_pandas()

    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")

    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")]
        )

    pair = pair_universe.get_single()
    assert pair.base_token_symbol == "WBNB"
    assert pair.quote_token_symbol == "BUSD"

    raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()

    # Filter down candles to a single pair
    single_pair_candles = raw_candles.loc[raw_candles["pair_id"] == pair.pair_id]

    candle_universe = GroupedCandleUniverse(single_pair_candles)

    # candle = single_pair_candles.loc[pd.Timestamp("2021-04-19")]
    indexed_candles = candle_universe.get_single_pair_data()

    # Handpicked random entry

    # pair_id                      1015916
    # timestamp        2022-02-14 00:00:00
    # exchange_rate                    1.0
    # open                      399.781891
    # close                     380.350555
    # high                      439.315765
    # low                       374.533813
    # buys                          520815
    # sells                         502240
    # buy_volume               246035072.0
    # sell_volume              248500144.0
    # avg                       408.007294
    # start_block                 15233866
    # end_block                   15434838

    candle = indexed_candles.loc[pd.Timestamp("2022-02-14")]
    assert not is_candle_green(candle)
    assert is_candle_red(candle)


def test_candle_upsample(persistent_test_client: Client):
    """Upsample OHLCV candles."""

    client = persistent_test_client

    exchange_universe = client.fetch_exchange_universe()
    columnar_pair_table = client.fetch_pair_universe()
    pairs_df = columnar_pair_table.to_pandas()

    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")

    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")]
        )

    pair = pair_universe.get_single()
    assert pair.base_token_symbol == "WBNB"
    assert pair.quote_token_symbol == "BUSD"

    raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()

    # Filter down candles to a single pair
    single_pair_candles = raw_candles.loc[raw_candles["pair_id"] == pair.pair_id]
    single_pair_candles = single_pair_candles.set_index("timestamp", drop=False)
    monthly_candles = resample_candles(single_pair_candles, pd.Timedelta(days=30))
    assert len(monthly_candles) <= len(single_pair_candles) / 4


def test_candle_get_last_entries(persistent_test_client: Client):
    """Get candles before a certain timestamp."""

    client = persistent_test_client

    exchange_universe = client.fetch_exchange_universe()
    columnar_pair_table = client.fetch_pair_universe()
    pairs_df = columnar_pair_table.to_pandas()

    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")

    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")]
        )

    pair = pair_universe.get_single()
    raw_candles = client.fetch_all_candles(TimeBucket.d7).to_pandas()
    candle_universe = GroupedCandleUniverse(raw_candles)

    candles = candle_universe.get_last_entries_by_pair_and_timestamp(
        pair.pair_id,
        pd.Timestamp("2022-01-01"),
    )
    last = candles.iloc[-1]
    assert last["timestamp"] == pd.Timestamp("2021-12-27")
    assert last["pair_id"] == pair.pair_id



@pytest.mark.skip(reason="This test currently downloads a 3.4G parquet and load it to RAM, TODO: move to manual test")
def test_filter_pyarrow(persistent_test_client: Client):
    """Filter loaded pyarrow files without loading them fully to the memory.

    Ensures that we can work on candle and liquidity data files on low memory servers.

    .. note ::

        Looks like the current 4h candle dataset peaks at 2.7GB

    .. code-block:: plain

        Do nothing test:  1276.796875 MB
        Load exchanges: 1253.359375 MB
        Load pairs: 1373.125 MB
        Create filtered pair universe: 1408.84375 MB
        Read candles parquet 4h, using single pair filter: 2626.796875 MB
        Convert it to Pandas:  2710.203125 MB
        Using 1h candles:  4725.296875 MB
    """

    # TODO: This test is still experimental

    client = persistent_test_client
    exchange_universe = client.fetch_exchange_universe()
    pairs_df = client.fetch_pair_universe().to_pandas()

    # Create filtered exchange and pair data
    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")
    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")]
        )

    method = "filtered_parquet"

    # # Load candles for the named pair only
    if method == "filtered_parquet":
        # Load by using Parquet filter functoin
        candle_file = client.fetch_candle_dataset(TimeBucket.h1)
        filter = pair_universe.create_parquet_load_filter()
        pq = read_parquet(candle_file, filter)
        single_pair_candles: pandas.DataFrame = pq.to_pandas()
    else:
        # Load everything to Pandas,
        # then filter down
        df = client.fetch_candle_dataset(TimeBucket.h1).to_pandas()
        pair = pair_universe.get_single()
        single_pair_candles = df.loc[df["pair_id"] == pair.pair_id]

    pair_ids = single_pair_candles["pair_id"].unique()
    assert len(pair_ids) == 1

    #mem_used = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # print(f"Max mem {mem_used/(1024*1024)} MB")


def test_load_candles_using_jsonl(persistent_test_client: Client):
    """Load data using JSONL endpoint"""

    client = persistent_test_client
    exchange_universe = client.fetch_exchange_universe()
    pairs_df = client.fetch_pair_universe().to_pandas()

    # Create filtered exchange and pair data
    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")
    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")]
        )

    pair = pair_universe.get_single()
    candles_df = client.fetch_candles_by_pair_ids([pair.pair_id], TimeBucket.h1)

    assert len(candles_df) >= 10_000

    candle_universe = GroupedCandleUniverse(candles_df)

    first_at, last_at = candle_universe.get_timestamp_range()
    assert first_at == pd.Timestamp('2021-04-24 01:00:00')
    assert last_at >= pd.Timestamp('2022-08-10 11:00:00')
    test_price = candle_universe.get_closest_price(pair.pair_id, pd.Timestamp("2022-01-01"))
    assert test_price == pytest.approx(516.9167236844088)



def test_load_candles_using_jsonl_max_bytes(persistent_test_client: Client):
    """OverloadJSONL endpoint max_bytes"""

    client = persistent_test_client
    exchange_universe = client.fetch_exchange_universe()
    pairs_df = client.fetch_pair_universe().to_pandas()

    # Create filtered exchange and pair data
    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")
    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")],
        )

    pair = pair_universe.get_single()
    with pytest.raises(JSONLMaxResponseSizeExceeded):
        client.fetch_candles_by_pair_ids(
            [pair.pair_id],
            TimeBucket.h1,
            max_bytes=5_000, # 5kBytes
        )


def test_load_candles_using_json_historical(persistent_test_client: Client):
    """Load historical candles using JSONL endpoint"""

    client = persistent_test_client
    exchange_universe = client.fetch_exchange_universe()
    pairs_df = client.fetch_pair_universe().to_pandas()

    # Create filtered exchange and pair data
    exchange = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")
    pair_universe = PandasPairUniverse.create_pair_universe(
            pairs_df,
            [(exchange.chain_id, exchange.exchange_slug, "WBNB", "BUSD")],
        )

    pair = pair_universe.get_single()
    candles_df = client.fetch_candles_by_pair_ids(
        {pair.pair_id},
        TimeBucket.h1,
        start_time=datetime.datetime(2023, 1, 1),
        end_time=datetime.datetime(2023, 1, 2)
    )
    assert len(candles_df) == 25  # 24 hours + 1 inclusive
