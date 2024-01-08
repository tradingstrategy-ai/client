from typing import Tuple
from tradingstrategy.chain import ChainId
from tradingstrategy.exchange import ExchangeType
from tradingstrategy.utils.format import string_to_eth_address

BINANCE_CHAIN_ID = ChainId.unknown
BINANCE_CHAIN_SLUG = ChainId(BINANCE_CHAIN_ID)
BINANCE_EXCHANGE_ADDRESS = string_to_eth_address("binance")
BINANCE_EXCHANGE_SLUG = "binance"
BINANCE_EXCHANGE_ID = 129875571
BINANCE_EXCHANGE_TYPE = ExchangeType.uniswap_v2
BINANCE_FEE = 0.0005  # TODO: get correct fee

BINANCE_SUPPORTED_QUOTE_TOKENS = ("USDT", "BUSD", "USDC")

DAYS_IN_YEAR = 365


def split_binance_symbol(symbol) -> Tuple[str, str]:
    """Split a binance symbol into a base and quote token.

    :param s: E.g. `ETHUSDT`
    :return: (base_token, quote_token)
    """
    for currency in BINANCE_SUPPORTED_QUOTE_TOKENS:
        if symbol.endswith(currency):
            main_part = symbol[: -len(currency)]
            currency_part = symbol[-len(currency) :]
            return main_part, currency_part
    raise ValueError(
        f"Unknown currency {symbol}. Currency needs to end with one of the supported quote tokens: {BINANCE_SUPPORTED_QUOTE_TOKENS}"
    )
