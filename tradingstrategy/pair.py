"""Trading pair information and pair datasets.

The core classes to understand the data are

- :py:class:`DEXPair`: describe one trading pair on different DEXes on different blockchains

- :py:class:`PandasPairUniverse`: all available trading pairs across all blockchains
  and functions to look them up.

- :py:data:`HumanReadableTradingPairDescription`: define the format for symbolic look iup
  for trading pairs

Trading pairs are **not fungible** across DEXes and blockchains.

- The same token might have different address on a different blockchains

- The same trading pair may be on multiple DEXes, or even
  on the same DEX with different fee tiers e.g. Uniswap v3
  gives WETH-USDC at 0.05%, 0.30% and 1% fee tiers,
  most real trading happening on 0.05% tier

Here is an example how to look up a particular trading pair::

.. code-block:: python

    from pyarrow import Table
    from tradingstrategy.chain import ChainId
    from tradingstrategy.exchange import ExchangeUniverse
    from tradingstrategy.pair import PandasPairUniverse
    from tradingstrategy.pair import HumanReadableTradingPairDescription

    # Exchange map data is so small it does not need any decompression
    exchange_universe: ExchangeUniverse = client.fetch_exchange_universe()

    # Decompress the pair dataset to Python map
    # This is raw PyArrow data
    columnar_pair_table: Table = client.fetch_pair_universe()
    print(f"Total pairs {len(columnar_pair_table)}, total exchanges {len(exchange_universe.exchanges)}")

    # Wrap the data in a helper class with indexes for easier access
    pair_universe = PandasPairUniverse(columnar_pair_table.to_pandas(), exchange_universe=exchange_universe)

    # Get BNB-BUSD pair on PancakeSwap v2
    #
    # There are no fee tiers, so we
    #
    desc: HumanReadableTradingPairDescription = (ChainId.bsc, "pancakeswap-v2", "WBNB", "BUSD")
    bnb_busd = pair_universe.get_pair_by_human_description(desc)
    print(f"We have pair {bnb_busd} with 30d volume of USD {bnb_busd.volume_30d}")

See :ref:`tutorial` section for Pairs tutorial for more information.

For exploring the trading pairs through web you can use

- `Trading Strategy trading pair search <https://tradingstrategy.ai/search>`__

- `Trading Strategy trading pair listings (by blockchain, by DEX, etc. by trading fee) <https://tradingstrategy.ai/trading-view>`__

"""

import logging
import enum
import pprint
import warnings
from dataclasses import dataclass
from types import NoneType
from typing import Optional, List, Iterable, Dict, Union, Set, Tuple, TypeAlias, Collection

import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from dataclasses_json import dataclass_json
from numpy import isnan

from tradingstrategy.chain import ChainId
from tradingstrategy.token import Token
from tradingstrategy.exchange import ExchangeUniverse, Exchange, ExchangeType, ExchangeNotFoundError
from tradingstrategy.stablecoin import ALL_STABLECOIN_LIKE
from tradingstrategy.types import NonChecksummedAddress, BlockNumber, UNIXTimestamp, BasisPoint, PrimaryKey, Percent, \
    USDollarAmount, Slug, URL
from tradingstrategy.utils.columnar import iterate_columnar_dicts
from tradingstrategy.utils.schema import create_pyarrow_schema_for_dataclass, create_columnar_work_buffer, \
    append_to_columnar_work_buffer
from tradingstrategy.exceptions import DataNotFoundError


logger = logging.getLogger(__name__)


class PairNotFoundError(DataNotFoundError):
    """No trading pair found matching the given criteria."""

    advanced_search_url = "https://tradingstrategy.ai/search?q=&sortBy=liquidity%3Adesc&filters=%7B%22pool_swap_fee%22%3A%5B%5D%2C%22price_change_24h%22%3A%5B%5D%2C%22liquidity%22%3A%5B%5D%2C%22volume_24h%22%3A%5B%5D%2C%22type%22%3A%5B%5D%2C%22blockchain%22%3A%5B%5D%2C%22exchange%22%3A%5B%5D%7D"

    template = f"""This might be a problem in your data loading and filtering. 
                
    Use tradingstrategy.ai website to explore pairs. Once on a pair page, click on the `Copy Python identifier` button to get the correct pair information to use in your strategy.
    
    Here is a list of DEXes: https://tradingstrategy.ai/trading-view/exchanges
    
    Here is advanced search: {advanced_search_url}
    
    For any further questions join our Discord: https://tradingstrategy.ai/community"""

    def __init__(
        self, 
        *, 
        base_token: Optional[str]=None, 
        quote_token: Optional[str]=None, 
        fee_tier: Optional[Percent] = None, 
        pair_id: Optional[int]=None,
        exchange_slug: Optional[str] = None, 
        exchange_id: Optional[int] = None,
    ):

        if base_token:
            assert quote_token, "If base token is specified, quote token must be specified too."
        if quote_token:
            assert base_token, "If quote token is specified, base token must be specified too."

        if base_token and quote_token:
            message = f"No pair with base_token {base_token}, quote_token {quote_token}, fee tier {fee_tier}"
        else:
            assert exchange_slug or pair_id, "Either exchange_slug or pair_id must be specified if base_token and quote_token are not specified"
            message = "No pair with "

        if exchange_slug:
            message = message + f" exchange_slug {exchange_slug}"

        if exchange_id:
            message = message + f" exchange_id {exchange_id}"

        if pair_id:
            message = message + f" pair_id {pair_id}"


        super().__init__(message + " found. " + self.template)


class DuplicatePair(Exception):
    """Found multiple trading pairs for the same naive lookup."""


class DataDecodeFailed(Exception):
    """The parquet file has damaged data for this trading pair."""


#: Data needed to identify a trading pair with human description.
#:
#: This is `(chain, exchange slug, base token, quote token)`.
#:
#: See also
#:
#: - :py:data:`HumanReadableTradingPairDescription`

FeelessPair: TypeAlias = Tuple[ChainId, str | None, str, str]

#: Data needed to identify a trading pair with human description.
#:
#: A version that can sepearate different fee variants.
#: This is all Uniswap v3 pairs, as a single exchange
#: supports the same pair with different pools having different fees.
#:
#: This is `(chain, exchange slug, base token, quote token, pool fee)`.c
#:
#: Pool fee is expressed as floating point. E.g. 0.0005 for 5 BPS fee.
#:
#: See also
#:
#: - :py:data:`HumanReadableTradingPairDescription`
#:
#:
FeePair: TypeAlias = Tuple[ChainId, str | None, str, str, Percent]


#: Shorthand method to identify trading pairs when written down by a human.
#:
#: This is `(chain, exchange slug, base token, quote token)`.
#:
#: Each major trading pair is identifiable as (chain, exchange, base token, quote token tuple).
#: Note that because there can be multipe tokens and fake tokens with the same name,
#: we usually refer to the "best" token which is the highest liquidty/volume trading
#: pair on the particular exchange.
#:
#:
#: Example descriptions
#:
#: .. code-block:: python
#:
#:         (ChainId.arbitrum, "uniswap-v3", "ARB", "USDC", 0.0005),  # Arbitrum, 5 BPS fee
#:         (ChainId.ethereum, "uniswap-v2", "WETH", "USDC"),  # ETH
#:         (ChainId.ethereum, "uniswap-v3", "EUL", "WETH", 0.0100),  # Euler 100 bps fee
#:         (ChainId.bsc, "pancakeswap-v2", "WBNB", "BUSD"),  # BNB
#:         (ChainId.arbitrum, "camelot", "ARB", "WETH"),  # ARB
#:
#: For "best fee match" lookups you can also omit the exchange by setting it to null.
#:
#: .. code-block:: python
#:
#:     # Find any CRV-USDC pair across all DEXes on Polygon, pick one with the best fee tier
#:     (ChainId.polygon, None, "CRV", "USDC"),
#:
#: See also
#:
#: -:py:meth:`PandasPairUniverse.get_pair_by_human_description`
#:
#: - :py:data:`FeelessPair`
#:
#: - :py:data:`FeePair`
#:
HumanReadableTradingPairDescription: TypeAlias = FeePair | FeelessPair



@dataclass_json
@dataclass(slots=True)
class DEXPair:
    """ Trading pair information for a single pair.

    Presents a single trading pair on decentralised exchanges.

    DEX trading pairs can be uniquely identified by

    - Internal id.

    - (Chain id, address) tuple - the same address can exist on multiple chains.

    - (Chain slug, exchange slug, pair slug) tuple.

    - Token names and symbols are *not* unique - anyone can create any number of trading pair tickers and token symbols.
      Do not rely on token symbols for anything.

    About data:

    - There is a different between `token0` and `token1` and `base_token` and `quote_token` conventions -
      the former are raw DEX (Uniswap) data while the latter are preprocessed by the server to make the data
      more understandable. Base token is the token you are trading and the quote token is the token you consider
      "money" for the trading. E.g. in WETH-USDC, USDC is the quote token. In SUSHI-WETH, WETH is the quote token.

    - Optional fields may be available if the candle server 1) detected the pair popular enough 2) managed to fetch the third party service information related to the token

    When you download a trading pair dataset from the server, not all trading pairs are available.
    For more information about trading pair availability see :ref:`trading pair tracking <tracking>`.

    The class provides some JSON helpers to make it more usable with JSON based APIs.

    This data class is serializable via `dataclasses-json` methods. Example:

    .. code-block::

        info_as_string = pair.to_json()

    You can also do `__json__()` convention data export:

    .. code-block::

        info_as_dict = pair.__json__()

    .. note ::

        Currently all flags are disabled and will be removed in the future. The historical dataset does not contain any filtering flags,
        because the data has to be filtered prior to download, to keep the download dump in a reasonasble size.
        The current data set of 800k trading pairs produce 100 MB dataset of which most of the pairs
        are useless. The server prefilters trading pairs and thus you cannot access historical data of pairs
        that have been prefiltered.

    """

    #: Internal primary key for any trading pair
    pair_id: PrimaryKey

    #: The chain id on which chain this pair is trading. 1 for Ethereum.
    chain_id: ChainId

    #: The exchange where this token trades
    exchange_id: PrimaryKey

    #: Smart contract address for the pair.
    #: In the case of Uniswap this is the pair (pool) address.
    address: NonChecksummedAddress

    #: Token pair contract address on-chain.
    #: Lowercase, non-checksummed.
    token0_address: str

    #: Token pair contract address on-chain
    #: Lowercase, non-checksummed.
    token1_address: str

    #: Token0 as in raw Uniswap data.
    #: ERC-20 contracst are not guaranteed to have this data.
    token0_symbol: Optional[str]

    #: Token1 as in raw Uniswap data
    #: ERC-20 contracst are not guaranteed to have this data.
    token1_symbol: Optional[str]

    #: What kind of exchange this pair is on
    dex_type: Optional[ExchangeType] = None

    #: Naturalised base and quote token.
    #: Uniswap may present the pair in USDC-WETH or WETH-USDC order based on the token address order.
    #: However we humans always want the quote token to be USD, or ETH or BTC.
    #: For the reverse token orders, the candle serve swaps the token order
    #: so that the quote token is the more natural token of the pair (in the above case USD)
    base_token_symbol: Optional[str] = None

    #: Naturalised base and quote token.
    #: Uniswap may present the pair in USDC-WETH or WETH-USDC order based on the token address order.
    #: However we humans always want the quote token to be USD, or ETH or BTC.
    #: For the reverse token orders, the candle serve swaps the token order
    #: so that the quote token is the more natural token of the pair (in the above case USD)
    quote_token_symbol: Optional[str] = None

    #: Number of decimals to convert between human amount and Ethereum fixed int raw amount.
    #: Note - this information might be missing from ERC-20 smart contracts.
    #: If the information is missing the token is not tradeable in practice.
    token0_decimals: Optional[int] = None

    #: Number of decimals to convert between human amount and Ethereum fixed int raw amount
    #: Note - this information might be missing from ERC-20 smart contracts.
    #: If the information is missing the token is not tradeable in practice.
    token1_decimals: Optional[int] = None

    #: Denormalised web page and API look up information
    exchange_slug: Optional[str] = None

    #: Exchange factory address.
    #: Denormalised here, so we do not need an additional lookup.
    exchange_address: Optional[str] = None

    #: Denormalised web page and API look up information
    pair_slug: Optional[str] = None

    #: Block number of the first Uniswap Swap event
    first_swap_at_block_number: Optional[BlockNumber] = None

    #: Block number of the last Uniswap Swap event
    last_swap_at_block_number:  Optional[BlockNumber] = None

    #: Timestamp of the first Uniswap Swap event
    first_swap_at: Optional[UNIXTimestamp] = None

    #: Timestamp of the first Uniswap Swap event
    last_swap_at: Optional[UNIXTimestamp] = None

    #: Pair has been flagged inactive, because it has not traded at least once during the last 30 days.
    #: TODO - inactive, remove.
    flag_inactive: Optional[bool] = None

    #: Pair is blacklisted by operators.
    #: Current there is no blacklist process so this is always false.
    #: TODO - inactive, remove.
    flag_blacklisted_manually: Optional[bool] = None

    #: Quote token is one of USD, ETH, BTC, MATIC or similar popular token variants.
    #: Because all candle data is outputted in the USD, if we have a quote token
    #: for which we do not have an USD conversation rate reference price source,
    #: we cannot create candles for the pair.
    #: TODO - inactive, remove.
    flag_unsupported_quote_token: Optional[bool] = None

    #: Pair is listed on an exchange we do not if it is good or not
    #: TODO - inactive, remove.
    flag_unknown_exchange: Optional[bool] = None

    #: Swap fee in basis points if known
    fee: Optional[BasisPoint] = None

    #: Risk assessment summary data
    buy_count_all_time: Optional[int] = None

    #: Risk assessment summary data
    sell_count_all_time: Optional[int] = None

    #: Risk assessment summary data
    buy_volume_all_time: Optional[float] = None

    #: Risk assessment summary data
    sell_volume_all_time: Optional[float] = None

    #: Risk assessment summary data
    buy_count_30d: Optional[int] = None

    #: Risk assessment summary data
    sell_count_30d: Optional[int] = None

    #: Risk assessment summary data
    buy_volume_30d: Optional[float] = None

    #: Risk assessment summary data
    sell_volume_30d: Optional[float] = None

    #: Buy token tax for this trading pair.
    #: See :ref:`token-tax` for details.
    buy_tax: Optional[float] = None

    #: Transfer token tax for this trading pair.
    #: See :ref:`token-tax` for details.
    transfer_tax: Optional[float] = None

    #: Sell tax for this trading pair.
    #: See :ref:`token-tax` for details.
    sell_tax: Optional[float] = None

    def __repr__(self):
        chain_name = self.chain_id.get_slug()
        return f"<Pair #{self.pair_id} {self.base_token_symbol} - {self.quote_token_symbol} ({self.address}) at exchange #{self.exchange_id} on {chain_name}>"

    def __eq__(self, other: "DEXPair"):
        """Trade positions are unique by opening timestamp and pair id.]

        We assume there cannot be a position opened for the same asset at the same time twice.
        """
        return self.pair_id == other.pair_id

    def __hash__(self):
        """set() and dict() compatibility"""
        return hash(self.pair_id)

    @property
    def fee_tier(self) -> Optional[Percent]:
        """Return the trading pair fee as 0...1.

        This is a synthetic properly based on :py:attr:`fee`
        data column.

        :return:
            None if the fee information is not availble.
            (Should not happen on real data, but may happen in unit tests.)
        """
        if self.fee is None:
            return None
        return self.fee / 10_000

    @property
    def volume_30d(self) -> USDollarAmount:
        """Denormalise trading volume last 30 days.

        - Not an accurate figure, as this is based on rough 30 days
          batch job

        - Good enough for undertanding a trading pair is tradeable
        """
        vol = 0
        buy_vol = self.buy_volume_30d or 0
        sell_vol = self.sell_volume_30d or 0

        if not isnan(buy_vol):
            vol += buy_vol

        if not isnan(sell_vol):
            vol += sell_vol
        return vol

    @property
    def base_token_address(self) -> str:
        """Get smart contract address for the base token.

        :return: Lowercase, non-checksummed.
        """
        if self.token0_symbol == self.base_token_symbol:
            return self.token0_address
        else:
            return self.token1_address

    @property
    def quote_token_address(self) -> str:
        """Get smart contract address for the quote token

        :return: Token address in checksummed case
        """
        if self.token0_symbol == self.quote_token_symbol:
            return self.token0_address
        else:
            return self.token1_address

    @property
    def quote_token_decimals(self) -> Optional[str]:
        """Get token decimal count for the quote token"""
        if self.token0_symbol == self.quote_token_symbol:
            return self.token0_decimals
        else:
            return self.token1_decimals

    @property
    def base_token_decimals(self) -> Optional[int]:
        """Get token decimal count for the base token.
        """
        if self.token0_symbol == self.base_token_symbol:
            return self.token0_decimals
        else:
            return self.token1_decimals

    @property
    def quote_token_decimals(self) -> Optional[int]:
        """Get token decimal count for the quote token"""
        if self.token0_symbol == self.quote_token_symbol:
            return self.token0_decimals
        else:
            return self.token1_decimals

    def is_tradeable(
            self,
            liquidity_threshold=None,
            volume_threshold_30d=100_000.
    ) -> bool:
        """Can this pair be traded.

        .. note ::

            Liquidity threshold is TBD.

        :param liquidity_threshold:
            How much the trading pair pool needs to have liquidity
            to be tradeable.

        :param volume_threshold_30d:
            How much montly volume the pair needs to have to be tradeable.

            Only used if liquidity data is missing.
        """

        # Volume can be Nan as well
        return (self.volume_30d or 0) >= volume_threshold_30d

    def get_ticker(self) -> str:
        """Return trading 'ticker'"""
        return f"{self.base_token_symbol}-{self.quote_token_symbol}"

    def get_friendly_name(self, exchange_universe: ExchangeUniverse) -> str:
        """Get a very human readable name for this trading pair.

        We need to translate the exchange id to someething human readable,
        and for this we need to have the access to the exchange universe.
        """
        exchange = exchange_universe.get_by_id(self.exchange_id)
        if exchange:
            exchange_name = exchange.name
        else:
            exchange_name = f"Exchange #{self.exchange_id}"
        return f"{self.base_token_symbol} - {self.quote_token_symbol}, pair #{self.pair_id} on {exchange_name}"

    def get_trading_pair_page_url(self) -> Optional[str]:
        """Get information page for this trading pair.

        :return: URL of the trading pair page or None if page/data not available.
        """
        chain_slug = self.chain_id.get_slug()
        if not self.exchange_slug:
            return None
        if not self.pair_slug:
            return None
        return f"https://tradingstrategy.ai/trading-view/{chain_slug}/{self.exchange_slug}/{self.pair_slug}"

    def __json__(self, request) -> dict:
        """Return dictionary presentation when this DEXPair is serialised as JSON.

        Provided for pyramid JSON renderer compatibility.

        This method is provided for API endpoints returned

        `More information <https://docs.pylonsproject.org/projects/pyramid/en/latest/narr/renderers.html#using-a-custom-json-method>`_.
        """
        return self.__dict__

    @classmethod
    def to_pyarrow_schema(cls) -> pa.Schema:
        """Construct schema for reading writing :term:`Parquet` filss for pair information."""

        # Enums must be explicitly expressed
        hints = {
            "chain_id": pa.uint64(),
            "dex_type": pa.string(),
        }

        return create_pyarrow_schema_for_dataclass(cls, hints=hints)

    @classmethod
    def convert_to_pyarrow_table(cls, pairs: List["DEXPair"], check_schema=False) -> pa.Table:
        """Convert a list of DEXPair instances to a Pyarrow table.

        Used to prepare a data export on a server.

        :param pairs: The list wil be consumed in the process

        :param check_schema:
            Run additional checks on the data.
            Slow. Use only in tests. May be give happier
            error messages instead of "OverflowError" what pyarrow spits out.
        """
        buffer = create_columnar_work_buffer(cls)

        for p in pairs:
            assert isinstance(p, DEXPair), f"Got {p}"
            append_to_columnar_work_buffer(buffer, p)

        schema = cls.to_pyarrow_schema()

        if check_schema:
            field: pa.Field
            for field in schema:
                try:
                    pa.array(buffer[field.name], field.type)
                except Exception as e:
                    # Usually cannot fit data into a column, like negative or none values
                    raise RuntimeError(f"Cannot process field {field}") from e

        return pa.Table.from_pydict(buffer, schema)

    @classmethod
    def convert_to_dataframe(cls, pairs: List["DEXPair"]) -> pd.DataFrame:
        """Convert Python DEXPair objects back to the Pandas dataframe presentation.

        As this is super-inefficient, do not use for large amount of data.
        """

        # https://stackoverflow.com/questions/20638006/convert-list-of-dictionaries-to-a-pandas-dataframe
        dicts = [p.to_dict() for p in pairs]
        return pd.DataFrame.from_dict(dicts)

    @classmethod
    def create_from_row(cls, row: pd.Series) -> "DEXPair":
        """Convert a DataFrame for to a DEXPair instance.

        Allow using of helper methods on the pair data.
        It is recommend you avoid this if you do not need row-like data.
        """
        items = {k: v for k,v in row.items()}
        return DEXPair.from_dict(items)

    def to_human_description(self) -> HumanReadableTradingPairDescription:
        """Get human description for this pair."""
        return (self.chain_id, self.exchange_slug, self.base_token_symbol, self.quote_token_symbol, self.fee)

    def get_base_token(self) -> Token:
        """Return token class presentation of base token in this trading pair."""
        return Token(
            chain_id=self.chain_id,
            symbol=self.base_token_symbol,
            address=self.base_token_address,
            decimals=self.base_token_decimals,
        )

    def get_quote_token(self) -> Token:
        """Return token class presentation of quote token in this trading pair."""
        return Token(
            chain_id=self.chain_id,
            symbol=self.quote_token_symbol,
            address=self.quote_token_address,
            decimals=self.quote_token_decimals,
        )

    def get_link(self) -> URL:
        """Get the trading pair link page on TradingStrategy.ai"""
        return f"https://tradingstrategy.ai/trading-view/{self.chain_id.get_slug()}/{self.exchange_slug}/{self.pair_slug}"


class PandasPairUniverse:
    """A pair universe implementation that is created from Pandas dataset.

    This is a helper class, as :py:class:`pandas.DataFrame` is somewhat more difficult to interact with.
    This class will read the raw data frame and convert it to `DEXPair` objects with a lookup index.
    Because the DEXPair conversion is expensive for 10,000s of Python objects,
    it is recommended that you filter the raw :py:class:`pandas.DataFrame` by using filtering functions
    in :py:mod:`tradingstrategy.pair` first, before initializing :py:class:`PandasPairUniverse`.

    About the usage:

    - Single trading pairs can be looked up using :py:meth:`PandasPairUniverse.get_pair_by_smart_contract`
      and :py:meth:`PandasPairUniverse.get_pair_by_id`

    - Multiple pairs can be looked up by directly reading `PandasPairUniverse.df` Pandas dataframe

    Example how to use:

    .. code-block::

        # Get dataset from the server as Apache Pyarrow table
        columnar_pair_table = client.fetch_pair_universe()

        # Convert Pyarrow -> Pandas -> in-memory DEXPair index
        pair_universe = PandasPairUniverse(columnar_pair_table.to_pandas())

        # Lookup SUSHI-WETH trading pair from DEXPair index
        # https://tradingstrategy.ai/trading-view/ethereum/sushi/sushi-eth
        pair: DEXPair = pair_universe.get_pair_by_smart_contract("0x795065dcc9f64b5614c407a6efdc400da6221fb0")

    If the pair index is too slow to build, or you want to keep it lean,
    you can disable the indexing with `build_index`.
    In this case, some of the methods won't work:

    .. code-block::

        # Get dataset from the server as Apache Pyarrow table
        columnar_pair_table = client.fetch_pair_universe()

        # Convert Pyarrow -> Pandas -> in-memory DEXPair index
        pair_universe = PandasPairUniverse(columnar_pair_table.to_pandas(), build_index=False)

    """

    def __init__(self,
                 df: pd.DataFrame,
                 build_index=True,
                 exchange_universe: Optional[ExchangeUniverse]=None):
        """
        :param df:
            The source DataFrame that contains all DEXPair entries

        :param build_index:
            Build quick lookup index for pairs

        :param exchange_universe:
            Optional exchange universe needed for human-readable pair lookup.

            We cannot properly resolve pairs unless we can map exchange names to their ids.
            Currently optional, only needed by `get_pair()`.
        """
        assert isinstance(df, pd.DataFrame)
        self.df = df.set_index(df["pair_id"])

        #: pair_id -> raw dict data mappings
        #:
        #: Constructed in one pass from Pandas DataFrame.
        #:
        #: Don't access directly, use :py:meth:`iterate_pairs`.
        self.pair_map: Dict[int, dict] = {}

        #: pair_id -> constructed DEXPair cache
        #:
        #: Don't access directly, use :py:meth:`iterate_pairs`.
        self.dex_pair_obj_cache: Dict[int, DEXPair] = {}

        # pair smart contract address -> DEXPair
        self.smart_contract_map = {}

        # Internal cache for get_token() lookup
        # address -> info tuple mapping
        self.token_cache: Dict[str, Token] = {}

        if build_index:
            self.build_index()

        self.exchange_universe = exchange_universe

        self.single_pair_cache: DEXPair = None

    def iterate_pairs(self) -> Iterable[DEXPair]:
        """Iterate over all pairs in this universe."""
        for pair_id in self.pair_map.keys():
            yield self.get_pair_by_id(pair_id)

    def build_index(self):
        """Create pair_id -> data mapping.

        Allows fast lookup of individual pairs.

        .. warning::

            This function assumes the universe contains
            data for only one blockchain. The same address
            can exist across multiple EVM chains.
            The created smart contract address index
            does not index chain id and thus is invalid.

        """
        # https://stackoverflow.com/a/73638890/315168
        self.pair_map = self.df.T.to_dict()
        self.smart_contract_map = {d["address"].lower(): d for d in self.pair_map.values()}

    def get_all_pair_ids(self) -> Collection[PrimaryKey]:
        """Get all pair ids in the data frame."""
        return self.df["pair_id"].unique()

    def get_pair_ids_by_exchange(self, exchange_id: PrimaryKey) -> pd.DataFrame:
        """Get all pair ids on a specific exchange.

        :return:
            Raw slide of DataFrame
        """
        return self.df.loc[self.df["exchange_id"] == exchange_id]["pair_id"]

    def get_count(self) -> int:
        """How many trading pairs there are."""
        return len(self.df)

    def get_pair_by_id(self, pair_id: PrimaryKey)  -> Optional[DEXPair]:
        """Look up pair information and return its data.

        Uses a cached path. Constructing :py:class:`DEXPair`
        objects is a bit slow, so this is a preferred method
        if you need to access multiple pairs in a hot loop.

        :raise PairNotFoundError: If pair is not found

        :return:
            Nicely presented :py:class:`DEXPair`.
        """

        # First try the cached paths
        if self.pair_map:

            # First try object cache
            obj = self.dex_pair_obj_cache.get(pair_id)
            if not obj:

                # Convert any pairs in-fly to DEXPair objects and store them.
                # We do not initially construct these objects,
                # as we do not know what pairs a strategy might access.
                data = self.pair_map.get(pair_id)

                obj = _convert_to_dex_pair(data)

                self.dex_pair_obj_cache[pair_id] = obj
            return obj

        # We did not build this universe with pair index
        # Not sure why anyone would really want to do this
        # maybe eliminate this code path altogether in the future
        df = self.df

        pairs: pd.DataFrame = df.loc[df["pair_id"] == pair_id]

        if len(pairs) > 1:
            raise DuplicatePair(f"Multiple pairs found for id {pair_id}")

        if len(pairs) == 1:
            data = next(iter(pairs.to_dict("index").values()))
            obj = _convert_to_dex_pair(data)
            return obj

        raise PairNotFoundError(pair_id=pair_id)

    def get_pair_by_smart_contract(self, address: str) -> Optional[DEXPair]:
        """Resolve a trading pair by its pool smart contract address.

        .. warning::

            This function assumes the universe contains
            data for only one blockchain. The same address
            can exist across multiple EVM chains.

        :param address: Ethereum smart contract address of the Uniswap pair contract
        """
        address = address.lower()
        assert self.smart_contract_map, "You need to build the index to use this function"
        data = self.smart_contract_map.get(address)
        return self.get_pair_by_id(data["pair_id"])

    def get_token(self, address: str, chain_id=None) -> Optional[Token]:
        """Get a token that is part of any trade pair.

        Get a token details for a token that is base or quotetoken of any trading pair.

        ..note ::

            TODO: Not a final implementation subject to chage.

        :param address:
            ERC-20 address of base or quote token in a trading pair.

        :param chain_id:
            Currently unsupported.

            Assumes all tokens are on a single chain.

        :return:
            Tuple (name, symbol, address, decimals)
            or None if not found.
        """

        if chain_id:
            raise NotImplementedError()

        address = address.lower()

        token: Optional[Token] = None

        assert len(self.pair_map) > 0, "This method can be only used with in-memory pair index"

        if address not in self.token_cache:
            for pair_id in self.pair_map.keys():
                p = self.get_pair_by_id(pair_id)
                if p.token0_address == address:
                    token = Token(p.chain_id, p.token0_symbol, p.token0_address, p.token0_decimals)
                    break
                elif p.token1_address == address:
                    token = Token(p.chain_id, p.token1_symbol, p.token1_address, p.token1_decimals)
                    break
            self.token_cache[address] = token
        return self.token_cache[address]

    def get_all_tokens(self) -> Set[Token]:
        """Get all base and quote tokens in trading pairs.

        .. warning ::

            This method is useful for only test/limited pair count universes.
            It is very slow and mainly purported for debugging and diagnostics.

        """
        tokens = set()
        for pair_id in self.pair_map.keys():
            p = self.get_pair_by_id(pair_id)
            tokens.add(self.get_token(p.base_token_address))
            tokens.add(self.get_token(p.quote_token_address))
        return tokens

    def get_single(self) -> DEXPair:
        """For strategies that trade only a single trading pair, get the only pair in the universe.

        :raise AssertionError:
            If our pair universe does not have an exact single pair.

            If the target pair could not be decoded.
        """
        if self.single_pair_cache:
            return self.single_pair_cache 

        pair_count = len(self.pair_map)
        assert pair_count == 1, f"Not a single trading pair universe, we have {pair_count} pairs"
        data = next(iter(self.pair_map.values()))

        # See https://github.com/tradingstrategy-ai/trading-strategy/issues/104
        obj =_convert_to_dex_pair(data)
        self.single_pair_cache = obj
        return self.single_pair_cache

    def get_single_quote_token(self) -> Token:
        """Gets the only trading pair quote token for this trading universe.

        :return:
            Quote token for all trading pairs.

        :raise AssertionError:
            If we have trading pairs with different quotes.

            E.g. both ``-ETH`` and ``-USDC`` pairs.
        """

        # Create (chain id, quote token address) set
        quotes = set()
        for p in self.iterate_pairs():
            quotes.add((p.chain_id, p.quote_token_address))

        tokens = [self.get_token(q[1]) for q in quotes]
        assert len(tokens) == 1, f"We have multiple qutoe tokens: {tokens}"
        return tokens[0]

    def get_by_symbols(self, base_token_symbol: str, quote_token_symbol: str) -> Optional[DEXPair]:
        """For strategies that trade only a few trading pairs, get the only pair in the universe.

        .. warning ::

            Currently, this method is only safe for prefiltered universe. There are no safety checks if
            the returned trading pair is legit. In the case of multiple matching pairs,
            a random pair is returned.g

        :raise PairNotFoundError: If we do not have a pair with the given symbols

        """
        for pair_id in self.pair_map.keys():
            pair = self.get_pair_by_id(pair_id)
            if pair.base_token_symbol == base_token_symbol and pair.quote_token_symbol == quote_token_symbol:
                return pair
        
        raise PairNotFoundError(base_token=base_token_symbol, quote_token=quote_token_symbol)
    
    def get_by_symbols_safe(self, base_token_symbol: str, quote_token_symbol: str) -> Optional[DEXPair]:
        """Get a trading pair by its ticker symbols. In the case of multiple matching pairs, an exception is raised.

        :raise DuplicatePair: If multiple pairs are found for the given symbols

        :raise PairNotFoundError: If we do not have a pair with the given symbols

        :return DEXPair: The trading pair
        """
        pair_placeholder = []
        for pair_id in self.pair_map.keys():
            pair = self.get_pair_by_id(pair_id)
            if pair.base_token_symbol == base_token_symbol and pair.quote_token_symbol == quote_token_symbol:
                pair_placeholder.append(pair)

        if len(pair_placeholder) > 1:
            raise DuplicatePair(f"Multiple pairs found for id {pair_id}")
        
        if len(pair_placeholder) == 1:
            return pair_placeholder[0]
        
        raise PairNotFoundError(base_token=base_token_symbol, quote_token=quote_token_symbol)

    def get_one_pair_from_pandas_universe(
            self,
            exchange_id: PrimaryKey | None,
            base_token: str,
            quote_token: str,
            fee_tier: Optional[Percent] = None,
            pick_by_highest_vol=False) -> Optional[DEXPair]:
        """Get a trading pair by its ticker symbols.

        Note that this method works only very simple universes, as any given pair
        is poised to have multiple tokens and multiple trading pairs on different exchanges.

        Example:

        .. code-block:: python

            # Get PancakeSwap exchange,
            # for the full exchange list see https://tradingstrategy.ai/trading-view/exchanges
            pancake = exchange_universe.get_by_chain_and_slug(ChainId.bsc, "pancakeswap-v2")

            # Because there can be multiple trading pairs with same tickers,
            # we pick the genuine among the scams based on its trading volume
            wbnb_busd_pair = pair_universe.get_one_pair_from_pandas_universe(
                pancake.exchange_id,
                "WBNB",
                "BUSD",
                pick_by_highest_vol=True,
                )

            print("WBNB address is", wbnb_busd_pair.base_token_address)
            print("BUSD address is", wbnb_busd_pair.quote_token_address)
            print("WBNB-BUSD pair contract address is", wbnb_busd_pair.address)

        :param exchange_id:
            The exchange internal id which we are looking up.

            Set ``None`` to look all exchanges.

        :param fee_tier:
            Uniswap v3 and likes provide the same ticker  in multiple fee tiers.

            You need to use `fee_tier` parameter to separate the Uniswap pools.
            Fee tier is not needed for Uniswap v2 like exchanges as all of their trading pairs have the same fee structure.

            The fee tier is 0...1 e.g. 0.0030 for 3 BPS or 0.3% fee tier.

            If fee tier is not provided, then the lowest fee tier pair is returned.
            However the lowest fee tier might not have the best liquidity or volume.

        :param pick_by_highest_vol:
            If multiple trading pairs with the same symbols are found, pick one with the highest volume. This is because often malicious trading pairs are create to attract novice users.

        :raise DuplicatePair: If the universe contains more than single entry for the pair.
        :raise PairNotFoundError: If the pair is not found in the universe.

        :return: DEXPairs with the given symbols
        """

        if fee_tier is not None:
            assert (fee_tier >= 0) and (fee_tier <= 1), f"Received bad fee tier: {base_token}-{quote_token}: {fee_tier}"

        df = self.df

        conditions = (df["base_token_symbol"] == base_token) & (df["quote_token_symbol"] == quote_token)

        if exchange_id is not None:
            conditions = conditions & (df["exchange_id"] == exchange_id)

        if fee_tier is not None:
            fee_bps = int(fee_tier * 10000)  # Convert to BPS
            conditions = conditions & (df["fee"] == fee_bps)

        pairs: pd.DataFrame = df.loc[conditions]

        if len(pairs) > 1:
            if not pick_by_highest_vol:
                for p in pairs.to_dict(orient="records"):
                    logger.error("Conflicting pair: %s", p)
                raise DuplicatePair(f"Found {len(pairs)} trading pairs for {base_token}-{quote_token} when 1 was expected")

            # Sort by trade volume and pick the highest one
            pairs = pairs.sort_values(by=["fee", "buy_volume_all_time"], ascending=[True, False])
            data = next(iter(pairs.to_dict("index").values()))
            return _convert_to_dex_pair(data)

        if len(pairs) == 1:
            data = next(iter(pairs.to_dict("index").values()))
            return _convert_to_dex_pair(data)

        raise PairNotFoundError(base_token=base_token, quote_token=quote_token, fee_tier=fee_tier, exchange_id=exchange_id)

    def get_pair(self,
                 chain_id: ChainId,
                 exchange_slug: str,
                 base_token: str,
                 quote_token: str,
                 fee_tier: Optional[float] = None,
                 exchange_universe: Optional[ExchangeUniverse]=None
                 ) -> DEXPair:
        """Get a pair by its description.

        The simplest way to access pairs in the pair universe.

        To use this method, we must include `exchange_universe` in the :py:meth:`__init__`
        as otherwise we do not have required look up tables.

        :return:
            The trading pair on the exchange.

            Highest volume trading pair if multiple matches.

        :raise PairNotFoundError:
            In the case input data cannot be resolved.
        """

        assert isinstance(chain_id, ChainId)
        assert type(exchange_slug) == str
        assert type(base_token) == str
        assert type(quote_token) == str


        assert self.exchange_universe is not None or exchange_universe is not None, "You need to provide exchange_universe argument to use this method"

        eu = exchange_universe or self.exchange_universe

        if fee_tier:
            desc = (chain_id, exchange_slug, base_token, quote_token, fee_tier)
        else:
            desc = (chain_id, exchange_slug, base_token, quote_token,)

        return self.get_pair_by_human_description(eu, desc)

    def get_pair_by_human_description(self,
                                      desc: HumanReadableTradingPairDescription | ExchangeUniverse,
                                      exchange_universe: ExchangeUniverse | HumanReadableTradingPairDescription = None,
                                      ) -> DEXPair:
        """Get pair by its human readable description.

        Look up a trading pair by chain, exchange, base, quote token tuple.

        See :py:data:`HumanReadableTradingPairDescription` for more information.

        .. note ::

            API signature change and the order of parameters reversed in TS version 0.19

        Example:

        .. code-block:: python

            # Get BNB-BUSD pair on PancakeSwap v2
            desc = (ChainId.bsc, "pancakeswap-v2", "WBNB", "BUSD")
            bnb_busd = pair_universe.get_pair_by_human_description(desc)
            assert bnb_busd.base_token_symbol == "WBNB"
            assert bnb_busd.quote_token_symbol == "BUSD"
            assert bnb_busd.buy_volume_30d > 1_000_000

        Another example:

        .. code-block:: python

            pair_human_descriptions = (
                (ChainId.ethereum, "uniswap-v2", "WETH", "USDC"),  # ETH
                (ChainId.ethereum, "uniswap-v2", "EUL", "WETH", 0.0030),  # Euler 30 bps fee
                (ChainId.ethereum, "uniswap-v3", "EUL", "WETH", 0.0100),  # Euler 100 bps fee
                (ChainId.ethereum, "uniswap-v2", "MKR", "WETH"),  # MakerDAO
                (ChainId.ethereum, "uniswap-v2", "HEX", "WETH"),  # MakerDAO
                (ChainId.ethereum, "uniswap-v2", "FNK", "USDT"),  # Finiko
                (ChainId.ethereum, "sushi", "AAVE", "WETH"),  # AAVE
                (ChainId.ethereum, "sushi", "COMP", "WETH"),  # Compound
                (ChainId.ethereum, "sushi", "WETH", "WBTC"),  # BTC
                (ChainId.ethereum, "sushi", "ILV", "WETH"),  # Illivium
                (ChainId.ethereum, "sushi", "DELTA", "WETH"),  # Delta
                (ChainId.ethereum, "sushi", "UWU", "WETH"),  # UwU lend
                (ChainId.ethereum, "uniswap-v2", "UNI", "WETH"),  # UNI
                (ChainId.ethereum, "uniswap-v2", "CRV", "WETH"),  # Curve
                (ChainId.ethereum, "sushi", "SUSHI", "WETH"),  # Sushi
                (ChainId.bsc, "pancakeswap-v2", "WBNB", "BUSD"),  # BNB
                (ChainId.bsc, "pancakeswap-v2", "Cake", "BUSD"),  # Cake
                (ChainId.bsc, "pancakeswap-v2", "MBOX", "BUSD"),  # Mobox
                (ChainId.bsc, "pancakeswap-v2", "RDNT", "WBNB"),  # Radiant
                (ChainId.polygon, "quickswap", "WMATIC", "USDC"),  # Matic
                (ChainId.polygon, "quickswap", "QI", "WMATIC"),  # QiDao
                (ChainId.polygon, "sushi", "STG", "USDC"),  # Stargate
                (ChainId.avalanche, "trader-joe", "WAVAX", "USDC"),  # Avax
                (ChainId.avalanche, "trader-joe", "JOE", "WAVAX"),  # TraderJoe
                (ChainId.avalanche, "trader-joe", "GMX", "WAVAX"),  # GMX
                (ChainId.arbitrum, "camelot", "ARB", "WETH"),  # ARB
                # (ChainId.arbitrum, "sushi", "MAGIC", "WETH"),  # Magic
            )

            client = persistent_test_client
            exchange_universe = client.fetch_exchange_universe()
            pairs_df = client.fetch_pair_universe().to_pandas()
            pair_universe = PandasPairUniverse(pairs_df, exchange_universe=exchange_universe)

            pairs: List[DEXPair]
            pairs = [pair_universe.get_pair_by_human_description(exchange_universe, d) for d in pair_human_descriptions]

            assert len(pairs) == 26
            assert pairs[0].exchange_slug == "uniswap-v2"
            assert pairs[0].get_ticker() == "WETH-USDC"

            assert pairs[1].exchange_slug == "uniswap-v2"
            assert pairs[1].get_ticker() == "EUL-WETH"

        :param desc:
            Trading pair description as tuple (blockchain, dex, base, quote fee)

        :param exchange_universe:
            The current database used to decode exchanges.

            If not given use the `exchange_universe` given in the constructor.
            Either argument here or argument in the constructor must be given.

        :return:
            The trading pair on the exchange.

            Highest volume trading pair if multiple matches.

        :raise PairNotFoundError:
            In the case input data cannot be resolved.
        """

        # Check legacy parameter order
        if isinstance(desc, ExchangeUniverse):
            desc, exchange_universe = exchange_universe, desc

        if exchange_universe is None:
            exchange_universe = self.exchange_universe

        assert exchange_universe is not None, "get_pair_by_human_description() needs exchange_universe passed as constructor or function argument in order to do pair lookups"

        if len(desc) >= 5:
            chain_id, exchange_slug, base_token, quote_token, fee_tier = desc
        else:
            chain_id, exchange_slug, base_token, quote_token = desc
            fee_tier = None

        common_explanation = "Use pair description format (chain, exchange, base, quote) or (chain, exchange, base, quote, fee)"

        assert isinstance(chain_id, ChainId), f"Not ChainId: {chain_id}\n{common_explanation}"
        assert type(exchange_slug) in (str, NoneType), f"Not exchange slug: {exchange_slug}\n{common_explanation}"
        assert type(base_token) == str, f"Base token symbol not a string: {base_token}\n{common_explanation}"
        assert type(quote_token) == str, f"Quote token symbol not a string: {quote_token}\n{common_explanation}"

        if fee_tier is not None:
            assert (fee_tier >= 0) and (fee_tier <= 1), f"Received bad fee tier: {chain_id} {exchange_slug} {base_token} {quote_token}: {fee_tier}"

        if exchange_slug:
            exchange = exchange_universe.get_by_chain_and_slug(chain_id, exchange_slug)
            if exchange is None:
                # Try to produce very helpful error message
                if exchange_universe.get_exchange_count() == 1:
                    our_exchange_slug = exchange_universe.get_single().exchange_slug
                    exchange_message = f"The slug of the only exchange we have is {our_exchange_slug}."
                else:
                    exchange_message = ""

                raise ExchangeNotFoundError(chain_id_name=chain_id.name, exchange_slug=exchange_slug, optional_extra_message=exchange_message)
        else:
            exchange = None

        pair = self.get_one_pair_from_pandas_universe(
            exchange.exchange_id if exchange_slug else None,
            base_token,
            quote_token,
            fee_tier=fee_tier,
            pick_by_highest_vol=True,
        )

        # this check techinically unnecessary 
        # since get_one_pair_from_pandas_universe will raise
        # but just to be sure
        if pair is None:
            raise PairNotFoundError(base_token=base_token, quote_token=quote_token, fee_tier=fee_tier, exchange_slug=exchange_slug)

        return pair

    def get_exchange_for_pair(self, pair: DEXPair) -> Exchange:
        """Get the exchange data on which a pair is trading.

        :param pair:
            Trading pair

        :return:
            Exchange instance.

            Should always return a value as traind pairs cannot
            exist without an exchange.
        """
        assert self.exchange_universe, "PandasPairUniverse.exchange_universe must be set in order to use this function"
        return self.exchange_universe.get_by_id(pair.exchange_id)

    def create_parquet_load_filter(self, count_limit=10_000) -> List[Tuple]:
        """Returns a Parquet loading filter that contains pairs in this universe.

        When candle or liquidity file is read to the memory,
        only read pairs that are within this pair universe.
        This severely reduces the memory usage and speed ups loading.

        See :py:func:`tradingstrategy.reader.read_parquet`.

        :param count_limit:
            Sanity check assert limit how many pairs we can cram into the filter.

        :return:
            Filter to be passed to read_table
        """

        count = self.get_count()
        assert count < count_limit, f"Too many pairs to create a filter. Pair count is {count}"

        # https://arrow.apache.org/docs/python/generated/pyarrow.parquet.read_table.html
        return [("pair_id", "in", self.get_all_pair_ids())]

    @staticmethod
    def create_single_pair_universe(
            df: pd.DataFrame,
            exchange: Exchange,
            base_token_symbol: str,
            quote_token_symbol: str,
            pick_by_highest_vol=True,
            fee_tier: Optional[float]=None,
    ) -> "PandasPairUniverse":
        """Create a trading pair universe that contains only a single trading pair.

        .. warning::

            Deprecated

        This is useful for trading strategies that to technical analysis trading
        on a single trading pair like BTC-USD.

        :param df:
            Unfiltered DataFrame for all pairs

        :param exchange:
            Exchange instance on the pair is trading

        :param base_token_symbol:
            Base token symbol of the trading pair

        :param quote_token_symbol:
            Quote token symbol of the trading pair

        :param pick_by_highest_vol:
            In the case of multiple match per token symbol,
            or scam tokens,
            pick one with the highest trade volume

        :param fee_tier:
            Pick a pair for a specific fee tier.

            Uniswap v3 has

        :raise DuplicatePair:
            Multiple pairs matching the criteria

        :raise PairNotFoundError:
            No pairs matching the criteria
        """

        warnings.warn('This method is deprecated. Use PandasPairUniverse.create_pair_universe() instead', DeprecationWarning, stacklevel=2)

        return PandasPairUniverse.create_limited_pair_universe(
            df,
            exchange,
            [(base_token_symbol, quote_token_symbol)],
            pick_by_highest_vol,
        )

    @staticmethod
    def create_limited_pair_universe(
            df: pd.DataFrame,
            exchange: Exchange,
            pairs: List[Tuple[str, str]],
            pick_by_highest_vol=True) -> "PandasPairUniverse":
        """Create a trading pair universe that contains only few trading pairs.

        .. warning::

            Deprecated

        This is useful for trading strategies that to technical analysis trading
        on a few trading pairs, or single pair three-way trades like Cake-WBNB-BUSD.

        :param df:
            Unfiltered DataFrame for all pairs

        :param exchange:
            Exchange instance on the pair is trading

        :param pairs:
            List of trading pairs as ticket tuples. E.g. `[ ("WBNB, "BUSD"), ("Cake", "WBNB") ]`

        :param pick_by_highest_vol:
            In the case of multiple match per token symbol,
            or scam tokens,
            pick one with the highest trade volume

        :raise DuplicatePair: Multiple pairs matching the criteria
        :raise PairNotFoundError: No pairs matching the criteria
        """

        warnings.warn('This method is deprecated. Use PandasPairUniverse.create_pair_universe() instead', DeprecationWarning, stacklevel=2)

        assert exchange is not None, "Got None as Exchange - exchange not found?"

        # https://pandas.pydata.org/docs/user_guide/merging.html
        frames = []

        for base_token_symbol, quote_token_symbol in pairs:

            filtered_df: pd.DataFrame= df.loc[
                (df["exchange_id"] == exchange.exchange_id) &
                (df["base_token_symbol"] == base_token_symbol) &
                (df["quote_token_symbol"] == quote_token_symbol)]

            if len(filtered_df) > 1:
                if not pick_by_highest_vol:
                    duplicates = 0
                    for p in filtered_df.to_dict(orient="records"):
                        logger.error("Conflicting pair: %s", p)
                        duplicates += 1
                    raise DuplicatePair(f"Found {duplicates} trading pairs for {base_token_symbol}-{quote_token_symbol} when 1 was expected")

                # Sort by trade volume and pick the highest one
                sorted = filtered_df.sort_values(by="buy_volume_all_time", ascending=False)
                duplicates_removed_df = sorted.drop_duplicates(subset="base_token_symbol")
                frames.append(duplicates_removed_df)

            elif len(filtered_df) == 1:
                frames.append(filtered_df)

            else:
                raise PairNotFoundError(base_token=base_token_symbol, quote_token=quote_token_symbol, exchange_slug=exchange.exchange_slug)

        if exchange:
            exchange_universe = ExchangeUniverse.from_collection([exchange])
        else:
            exchange_universe = None

        return PandasPairUniverse(pd.concat(frames), exchange_universe=exchange_universe)

    @staticmethod
    def create_pair_universe(
        df: pd.DataFrame,
        pairs: Collection[HumanReadableTradingPairDescription],
    ) -> "PandasPairUniverse":
        """Create a PandasPairUniverse instance based on loaded raw pairs data.

        A shortcut method to create a pair universe for a single or few trading pairs,
        from DataFrame of all possible trading pairs.

        Example for a single pair:

        .. code-block:: python

            pairs_df = client.fetch_pair_universe().to_pandas()
            pair_universe = PandasPairUniverse.create_pair_universe(
                    pairs_df,
                    [(ChainId.polygon, "uniswap-v3", "WMATIC", "USDC", 0.0005)],
                )
            assert pair_universe.get_count() == 1
            pair = pair_universe.get_single()
            assert pair.base_token_symbol == "WMATIC"
            assert pair.quote_token_symbol == "USDC"
            assert pair.fee_tier == 0.0005  # BPS

        Example for multiple trading pairs.:

        .. code-block:: python

            pairs_df = client.fetch_pair_universe().to_pandas()

            # Create a trading pair universe for a single trading pair
            #
            # WMATIC-USD on Uniswap v3 on Polygon, 5 BPS fee tier and 30 BPS fee tier
            #
            pair_universe = PandasPairUniverse.create_pair_universe(
                    pairs_df,
                    [
                        (ChainId.polygon, "uniswap-v3", "WMATIC", "USDC", 0.0005),
                        (ChainId.polygon, "uniswap-v3", "WMATIC", "USDC", 0.0030)
                    ],
                )
            assert pair_universe.get_count() == 2

        :param df:
            Pandas DataFrame of all pair data.

            See :py:meth:`tradingstrategy.client.Client.fetch_pair_universe` for more information.

        :return:
            A trading pair universe that contains only the listed trading pairs.
        """
        resolved_pairs_df = resolve_pairs_based_on_ticker(df, pairs=pairs)
        return PandasPairUniverse(resolved_pairs_df)


class LegacyPairUniverse:
    """The queries universe, as returned by the server.

    .. note ::

        TODO: Legacy prototype implementation and will be deprecated.

    Converts raw pair dataset to easier to use `DEXPair`
    in-memory index.

    You likely want to use :py:class:`PandasPairUniverse`,
    as its offers much more functionality than this implemetation.
    """

    #: Internal id -> DEXPair mapping
    pairs: Dict[int, DEXPair]

    def __init__(self, pairs: Dict[int, DEXPair]):
        self.pairs = pairs

    @classmethod
    def create_from_pyarrow_table(cls, table: pa.Table) -> "LegacyPairUniverse":
        """Convert columnar presentation to a Python in-memory objects.

        Some data manipulation is easier with objects instead of columns.

        .. note ::

            This seems to quite slow operation.
            It is recommend you avoid this if you do not need row-like data.
        """
        pairs: Dict[int, DEXPair] = {}
        for batch in table.to_batches(max_chunksize=5000):
            d = batch.to_pydict()
            for row in iterate_columnar_dicts(d):
                pairs[row["pair_id"]] = DEXPair.from_dict(row)

        return LegacyPairUniverse(pairs=pairs)

    @classmethod
    def create_from_pyarrow_table_with_filters(cls, table: pa.Table, chain_id_filter: Optional[ChainId] = None) -> "LegacyPairUniverse":
        """Convert columnar presentation to a Python in-memory objects.

        Filter the pairs based on given filter arguments.
        """

        if chain_id_filter:
            # https://stackoverflow.com/a/64579502/315168
            chain_id_index = table.column('chain_id')
            row_mask = pc.equal(chain_id_index, pa.scalar(chain_id_filter.value, chain_id_index.cause))
            selected_table = table.filter(row_mask)

        return LegacyPairUniverse.create_from_pyarrow_table(selected_table)

    def get_pair_by_id(self, pair_id: int) -> Optional[DEXPair]:
        """Resolve pair by its id.

        Only useful for debugging. Does a slow look
        """
        return self.pairs[pair_id]

    def get_pair_by_ticker(self, base_token: str, quote_token: str) -> Optional[DEXPair]:
        """Get a trading pair by its ticker symbols.

        Note that this method works only very simple universes, as any given pair
        is poised to have multiple tokens and multiple trading pairs on different exchanges.

        :raise DuplicatePair: If the universe contains more than single entry for the pair.
        :raise PairNotFoundError: If the pair is not found.

        :return: None if there is no match
        """
        pairs = [p for p in self.pairs.values() if p.base_token_symbol == base_token and p.quote_token_symbol == quote_token]

        if len(pairs) > 1:
            raise DuplicatePair(f"Multiple trading pairs found {base_token}-{quote_token}")

        if pairs:
            return pairs[0]

        raise PairNotFoundError(base_token=base_token, quote_token=quote_token)

    def get_pair_by_ticker_by_exchange(self, exchange_id: int, base_token: str, quote_token: str) -> Optional[DEXPair]:
        """Get a trading pair by its ticker symbols.

        Note that this method works only very simple universes, as any given pair
        is poised to have multiple tokens and multiple trading pairs on different exchanges.

        :param exchange_id: E.g. `1` for uniswap_v2

        :raise DuplicatePair:
            If the universe contains more than single entry for the pair.
            Because we are looking by a token symbol there might be fake tokens with the same symbol.

        :return: None if there is no match
        """

        # Don't let ints slip through as they are unsupported
        assert type(exchange_id) == int

        pairs = [p for p in self.pairs.values()
                 if p.base_token_symbol == base_token
                 and p.quote_token_symbol == quote_token
                 and p.exchange_id == exchange_id]

        if len(pairs) > 1:
            raise DuplicatePair(f"Multiple trading pairs found {base_token}-{quote_token} on exchange {exchange_id}")

        if pairs:
            return pairs[0]

        raise PairNotFoundError(base_token=base_token, quote_token=quote_token)

    def get_all_pairs_on_exchange(self, exchange_id: int) -> Iterable[DEXPair]:
        """Get all trading pair on a decentralsied exchange.

        Use `ExchangeUniverse.get_by_chain_and_slug` to resolve the `exchange_id` first.
        :param chain_id: E.g. `ChainId.ethereum`

        :param exchange_id: E.g. `1` for uniswap_v2

        :raise DuplicatePair:
            If the universe contains more than single entry for the pair.
            Because we are looking by a token symbol there might be fake tokens with the same symbol.

        :return: None if there is no match
        """

        # Don't let ints slip through as they are unsupported
        assert type(exchange_id) == int

        for p in self.pairs.values():
            if p.exchange_id == exchange_id:
                yield p

    def get_active_pairs(self) -> Iterable["DEXPair"]:
        """Filter for pairs that have see a trade for the last 30 days"""
        return filter(lambda p: not p.flag_inactive, self.pairs.values())

    def get_inactive_pairs(self) -> Iterable["DEXPair"]:
        """Filter for pairs that have not see a trade for the last 30 days"""
        return filter(lambda p: p.flag_inactive, self.pairs.values())


def filter_for_exchanges(pairs: pd.DataFrame, exchanges: Collection[Exchange]) -> pd.DataFrame:
    """Filter dataset so that it only contains data for the trading pairs from a certain exchange.

    Useful as a preprocess step for creating :py:class:`tradingstrategy.candle.GroupedCandleUniverse`
    or :py:class:`tradingstrategy.liquidity.GroupedLiquidityUniverse`.
    """
    exchange_ids = [e.exchange_id for e in exchanges]
    our_pairs: pd.DataFrame = pairs.loc[
        (pairs['exchange_id'].isin(exchange_ids))
    ]
    return our_pairs


def filter_for_trading_fee(pairs: pd.DataFrame, fee: Percent) -> pd.DataFrame:
    """Select only pairs with a specific trading fee.

    Filter pairs based on :py:term:`AMM` :py:term:`swap` fee.

    :param fee:
        Fee as the floating point.

        For example ``0.0005`` for :term:`Uniswap` 5 BPS fee tier.
    """

    assert 0 < fee < 1, f"Got fee: {fee}"

    int_fee = int(fee * 10_000)

    our_pairs: pd.DataFrame = pairs.loc[
        (pairs['fee'] == int_fee)
    ]
    return our_pairs


def filter_for_base_tokens(
    pairs: pd.DataFrame,
    base_token_addresses: List[str] | Set[str]
) -> pd.DataFrame:
    """Filter dataset so that it only contains data for the trading pairs that have a certain base token.

    Useful as a preprocess step for creating :py:class:`tradingstrategy.lending.LendingUniverse`

    Example:

    .. code-block:: python

        client = persistent_test_client

        exchange_universe = client.fetch_exchange_universe()

        quote_tokens = {
            "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",  # USDC polygon
            "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",  # USDT polygon
        }

        pairs_df = client.fetch_pair_universe().to_pandas()

        # Find out all volatile pairs traded against USDC and USDT on Polygon
        pairs_df = filter_for_chain(pairs_df, ChainId.polygon)
        pairs_df = filter_for_stablecoins(pairs_df, StablecoinFilteringMode.only_volatile_pairs)
        pairs_df = filter_for_quote_tokens(pairs_df, quote_tokens)

        # Create lending universe and trading universe with the cross section of
        # - Available assets in the lending protocols
        # - Asset we can trade
        lending_reserves = client.fetch_lending_reserve_universe()
        pairs_df = filter_for_base_tokens(pairs_df, lending_reserves.get_asset_addresses())

        pair_universe = PandasPairUniverse(pairs_df, exchange_universe=exchange_universe)

        # Lending reserves have around ~320 individual trading pairs on Polygon across different DEXes
        assert 1 < pair_universe.get_count() < 1_000

        eth_usdc = pair_universe.get_pair_by_human_description((ChainId.polygon, "uniswap-v3", "WETH", "USDC"))

    :param quote_token_addresses:
        List of Ethereum addresses of the tokens.

        Lowercased, non-checksummed.

    :return:
        DataFrame with trading pairs filtered to match quote token condition
    """
    assert type(base_token_addresses) in (list, set), f"Received: {type(base_token_addresses)}: {base_token_addresses}"

    for addr in base_token_addresses:
        assert addr == addr.lower(), f"Address was not lowercased {addr}"

    our_pairs: pd.DataFrame = pairs.loc[
        (pairs['token0_address'].isin(base_token_addresses) & (pairs['token0_symbol'] == pairs['base_token_symbol'])) |
        (pairs['token1_address'].isin(base_token_addresses) & (pairs['token1_symbol'] == pairs['base_token_symbol']))
    ]

    return our_pairs


def filter_for_quote_tokens(
        pairs: pd.DataFrame,
        quote_token_addresses: List[str] | Set[str]
) -> pd.DataFrame:
    """Filter dataset so that it only contains data for the trading pairs that have a certain quote tokens.

    Useful as a preprocess step for creating :py:class:`tradingstrategy.candle.GroupedCandleUniverse`
    or :py:class:`tradingstrategy.liquidity.GroupedLiquidityUniverse`.

    You might, for example, want to construct a trading universe where you have only BUSD pairs.

    Example:

    .. code-block:: python

        exchange_universe = client.fetch_exchange_universe()

        quote_tokens = {
            "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",  # USDC polygon
            "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",  # USDT polygon
        }

        pairs_df = client.fetch_pair_universe().to_pandas()

        # Find out all volatile pairs traded against USDC and USDT on Polygon
        pairs_df = filter_for_chain(pairs_df, ChainId.polygon)
        pairs_df = filter_for_stablecoins(pairs_df, StablecoinFilteringMode.only_volatile_pairs)
        pairs_df = filter_for_quote_tokens(pairs_df, quote_tokens)

        pairs_df = filter_for_quote_tokens(pairs_df, lending_reserves.get_asset_addresses())
        pair_universe = PandasPairUniverse(pairs_df, exchange_universe=exchange_universe)

    :param quote_token_addresses:
        List of Ethereum addresses of the tokens.

        Lowercased, non-checksummed.

    :return:
        DataFrame with trading pairs filtered to match quote token condition
    """
    assert type(quote_token_addresses) in (list, set), f"Received: {type(quote_token_addresses)}: {quote_token_addresses}"

    for addr in quote_token_addresses:
        assert addr == addr.lower(), f"Address was not lowercased {addr}"

    our_pairs: pd.DataFrame = pairs.loc[
        (pairs['token0_address'].isin(quote_token_addresses) & (pairs['token0_symbol'] == pairs['quote_token_symbol'])) |
        (pairs['token1_address'].isin(quote_token_addresses) & (pairs['token1_symbol'] == pairs['quote_token_symbol']))
    ]

    return our_pairs


class StablecoinFilteringMode(enum.Enum):
    """How to filter pairs in stablecoin filtering.

    See :py:func:`filter_for_stablecoins`.
    """
    only_stablecoin_pairs = "only_stablecoin_pairs"
    only_volatile_pairs = "only_volatile_pairs"
    all_pairs = "all_pairs"


def filter_for_stablecoins(pairs: pd.DataFrame, mode: StablecoinFilteringMode) -> pd.DataFrame:
    """Filter dataset so that it only contains data for the trading pairs that are either stablecoin pairs or not.

    Trading logic might not be able to deal with or does not want to deal with stable -> stable pairs.
    Trading stablecoin to another does not make sense, unless you are doing high volume arbitration strategies.

    Uses internal stablecoin list from :py:mod:`tradingstrategy.stablecoin`.

    - For code example see :py:func:`filter_for_quote_tokens`
    - See also :py:class:`StablecoinFilteringMode`
    """
    assert isinstance(mode, StablecoinFilteringMode)

    if mode == StablecoinFilteringMode.all_pairs:
        return pairs

    if mode == StablecoinFilteringMode.only_stablecoin_pairs:
        our_pairs: pd.DataFrame = pairs.loc[
            (pairs['token0_symbol'].isin(ALL_STABLECOIN_LIKE) & pairs['token1_symbol'].isin(ALL_STABLECOIN_LIKE))
        ]
    else:
        # https://stackoverflow.com/a/35939586/315168
        our_pairs: pd.DataFrame = pairs.loc[
            ~(pairs['token0_symbol'].isin(ALL_STABLECOIN_LIKE) & pairs['token1_symbol'].isin(ALL_STABLECOIN_LIKE))
        ]
    return our_pairs


def filter_for_chain(
    pairs: pd.DataFrame,
    chain_id: ChainId,
):
    """Extract trading pairs for specific blockchain.

    - For code example see :py:func:`filter_for_quote_tokens`
    """
    assert isinstance(chain_id, ChainId)
    return pairs.loc[pairs["chain_id"] == chain_id.value]


def filter_for_exchange(
    pairs: pd.DataFrame,
    exchange_slug: Slug | Set[Slug] | Tuple[Slug] | List[Slug],
):
    """Extract trading pairs for specific exchange(s).

    Example:

    .. code-block:: python

        # Pick only pairs traded on Uniswap v3
        df = filter_for_exchange(df, "uniswap-v3")

    With two exchanges:

        # Pick only pairs traded on Uniswap v3 or Quickswap
        df = filter_for_exchange(df, {"uniswap-v3", "quickswap"})

    """
    if type(exchange_slug) == str:
        return pairs.loc[pairs["exchange_slug"] == exchange_slug]
    elif type(exchange_slug) in (tuple, set, list):
        return pairs.loc[pairs["exchange_slug"].isin(exchange_slug)]
    else:
        raise AssertionError(f"Unsupported exchange slug filter: {exchange_slug.__class__}")


def resolve_pairs_based_on_ticker(
    df: pd.DataFrame,
    chain_id: Optional[ChainId] = None,
    exchange_slug: Optional[str] = None,
    pairs: set[tuple[ChainId, str, str, str] | \
               tuple[ChainId, str, str, str, BasisPoint]] | \
                Collection[HumanReadableTradingPairDescription] = None,
    sorting_criteria_by: Tuple = ("fee", "buy_volume_all_time"),
    sorting_criteria_ascending: Tuple = (True, False),
) -> pd.DataFrame:
    """Resolve symbolic trading pairs to their internal integer primary key ids.

    Uses pair database described :py:class:`DEXPair` Pandas dataframe
    to resolve pairs to their integer ids on a single exchange.

    .. warning ::

        For popular trading pairs, there will be multiple scam pairs
        with the same ticker name. In this case, one with the highest all-time
        buy volume is chosen.

    .. note ::

        Pair ids are not stable and may change long term.
        Always resolve pair ids before a run.

    Example:

    .. code-block: python

        client = persistent_test_client
        pairs_df = client.fetch_pair_universe().to_pandas()

        pairs = {
            (ChainId.ethereum, "uniswap-v3", "WETH", "USDC", 0.0005),
            (ChainId.ethereum, "uniswap-v3", "DAI", "USDC"),
        }

        filtered_pairs_df = resolve_pairs_based_on_ticker(
            pairs_df,
            pairs=pairs,
        )

        assert len(filtered_pairs_df) == 2

    Alternative Example:

    .. code-block: python

        pairs_df = client.fetch_pair_universe().to_pandas()

        tickers = {
            ("WBNB", "BUSD"),
            ("Cake", "WBNB"),
        }

        # ticker -> pd.Series row map for pairs
        filtered_pairs_df = resolve_pairs_based_on_ticker(
            pairs_df,
            ChainId.bsc,
            "pancakeswap-v2",
            tickers
        )

        assert len(filtered_pairs_df) == 2
        wbnb_busd = filtered_pairs_df.loc[
            (filtered_pairs_df["base_token_symbol"] == "WBNB") &
            (filtered_pairs_df["quote_token_symbol"] == "BUSD")
        ].iloc[0]
        assert wbnb_busd["buy_volume_30d"] > 0

    :param df:
        DataFrame containing DEXPairs

    :param chain_id:
        Blockchain the exchange is on.

        Set `None` if given part of `HumanReadableTradingPairDescription`.

    :param exchange_slug:
        Symbolic exchange name

        Set `None` if given part of `HumanReadableTradingPairDescription`.

    :param pairs:
        List of trading pairs as (base token, quote token) tuples.
        Note that giving trading pair tokens in wrong order
        causes pairs not to be found.
        If any ticker does not match it is not included in the result set.

        See :py:data:`tradingstrategy.pair.HumanReadableTradingPairDescription`.

    :param sorting_criteria_by:
        Resulting DataFrame sorting

    :param sorting_criteria_ascending:
        Resulting DataFrame sorting

    :return:
        DataFrame with filtered pairs.
    """

    assert pairs, "No pair_tickers given"

    match_fee = False

    # Create list of conditions to filter out dataframe,
    # one condition per pair
    conditions = []
    for pair_description in pairs:

        if len(pair_description) in (4, 5):
            # New API
            pair_chain, pair_exchange, base, quote, *fee = pair_description

            assert isinstance(pair_chain, ChainId), f"Expected ChainId, got {pair_chain}. Description is {pair_description}."
            assert type(pair_exchange) == str
            assert type(base) == str
            assert type(quote) == str

            # Convert to BPS
            if len(fee) > 0:
                assert len(fee) == 1
                fee_value = fee[0]
                assert type(fee_value) == float, f"Expected fee 0...1: {type(fee_value)}: {fee_value}"
                assert fee_value >= 0 and fee_value <= 1
                fee = [int(fee_value * 10000)]

        else:
            pair_chain = chain_id
            pair_exchange = exchange_slug
            assert chain_id, "chain_id missing"
            assert exchange_slug, "exchange_slug missing"
            base, quote, *fee = pair_description

        condition = (
            (df["base_token_symbol"].str.lower() == base.lower())
            & (df["quote_token_symbol"].str.lower() == quote.lower())
            & (df["exchange_slug"].str.lower() == pair_exchange.lower())
            & (df["chain_id"] == pair_chain.value)
        )

        # also filter by pair fee if pair ticker specifies it
        if len(fee) > 0:
            condition &= (df["fee"] == fee[0])

        conditions.append(condition)

    # OR call conditions together
    # https://stackoverflow.com/a/57468610/315168
    df_matches = df.loc[np.logical_or.reduce(conditions)]

    # Sort by the buy volume as a preparation
    # for the scam filter
    df_matches = df_matches.sort_values(by=list(sorting_criteria_by), ascending=list(sorting_criteria_ascending))

    result_pair_ids = set()

    # Scam filter.
    # Pick the tokens by the highest buy volume to the result map.
    for pair_description in pairs:

        match_fee = None
        if len(pair_description) > 3:
            pair_chain, pair_exchange, base, quote, *fee = pair_description
            if len(fee) >= 1:
                match_fee = fee[0]
        else:
            # Legacy
            base, quote, *_ = pair_description

        if match_fee:
            for _, row in df_matches.iterrows():
                if (
                    row["base_token_symbol"].lower() == base.lower()
                    and row["quote_token_symbol"].lower() == quote.lower()
                    and row["fee"] == match_fee * 10000
                ):
                    result_pair_ids.add(row["pair_id"])
                    break

        else:

            for _, row in df_matches.iterrows():
                if (
                    row["base_token_symbol"].lower() == base.lower()
                    and row["quote_token_symbol"].lower() == quote.lower()
                ):
                    result_pair_ids.add(row["pair_id"])
                    break

    result_df = df.loc[df["pair_id"].isin(result_pair_ids)]

    return result_df


def generate_address_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add base_token_address, quote_token_address columns.

    These are not part of the dataset, as they can be derived from other colums.

    :param df:
        Dataframe from :py:meth:`tradingstrategy.client.Client.fetch_pair_universe`.

    :return:
        New DataFrame with `base_token_address` and `quote_token_address` columns.

    """

    def expander(row: pd.Series) -> dict:
        quote_token_symbol = row["quote_token_symbol"]
        if row["token0_symbol"] == quote_token_symbol:
            return {
                "quote_token_address": row["token0_address"],
                "base_token_address": row["token1_address"],
            }
        else:
            return {
                "quote_token_address": row["token1_address"],
                "base_token_address": row["token0_address"],
            }

    applied_df = df.apply(expander, axis='columns', result_type='expand')
    df = pd.concat([df, applied_df], axis='columns')
    return df


def _preprocess_loaded_pair_data(data: dict) -> dict:
    """Fix any data loading and transfomration issues we might have with the data.

    Hot fix for https://github.com/tradingstrategy-ai/trading-strategy/issues/104
    """

    assert isinstance(data, dict)

    def _fix_val(v):

        if v is pd.NA:
            # FastParquet fills missing data with pandas._libs.missing.NAType
            return None

        try:
            if isnan(v):
                return None
        except:
            pass
        return v

    result = {}
    for k, v in data.items():
        result[k] = _fix_val(v)

    return result


def _convert_to_dex_pair(data: dict) -> DEXPair:
    """Convert trading pai0r data from dict to object.

    - Correctly handle serialisation quirks

    - Give user friendly error reports
    """
    data = _preprocess_loaded_pair_data(data)
    try:
        obj = DEXPair.from_dict(data)
    except Exception as e:
        pretty = pprint.pformat(data)
        raise DataDecodeFailed(f"Could not decode trading pair data:\n{pretty}") from e
    return obj