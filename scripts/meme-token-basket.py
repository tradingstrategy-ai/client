"""Create a basket of meme tokens on Ethereum mainnet.

- Use Coingecko labelling

- Cross-reference to Trading Strategy data

- Build a basket of available categorised (meme) tokens on Ethereum mainnet
"""

import logging
import os
import sys

from tradingstrategy.alternative_data.coingecko import CoingeckoUniverse, categorise_pairs
from tradingstrategy.chain import ChainId
from tradingstrategy.client import Client
from tradingstrategy.pair import PandasPairUniverse
from tradingstrategy.utils.token_filter import deduplicate_pairs_by_volume


def main():

    logging.basicConfig(handlers=[logging.StreamHandler(sys.stdout)], level=logging.INFO)
    logger = logging.getLogger(__name__)

    chain_id = ChainId.ethereum
    category = "Meme"
    client = Client.create_live_client(api_key=os.environ['TRADING_STRATEGY_API_KEY'])

    coingecko_universe = CoingeckoUniverse.load()
    logger.info("Coingecko universe is %s", coingecko_universe)

    pairs_df = client.fetch_pair_universe().to_pandas()
    category_df = categorise_pairs(coingecko_universe, pairs_df)

    # Get all trading pairs that are memecoin, across all coingecko data
    mask = category_df["category"] == category
    meme_pair_ids = category_df[mask]["pair_id"]

    logger.info("Memecoin pairs across all chain %s", len(meme_pair_ids))

    # From these pair ids, see what trading pairs we have on Ethereum mainnet
    pairs_df = pairs_df[pairs_df["pair_id"].isin(meme_pair_ids) & (pairs_df["chain_id"] == chain_id.value)]
    logger.info("Ethereum filtered memecoins %s", len(pairs_df))

    pairs_universe = PandasPairUniverse(pairs_df)

    logger.info("Example pairs:")
    for pair in list(pairs_universe.iterate_pairs())[0:10]:
        logger.info("   Pair: %s", pair)
    # SHIB - WETH (0x811beed0119b4afce20d2583eb608c6f7af1954f) at exchange 1 on ethereum>
    # SHIB - USDT (0x773dd321873fe70553acc295b1b49a104d968cc8) at exchange 1 on ethereum>
    # LEASH - WETH (0x874376be8231dad99aabf9ef0767b3cc054c60ee) at exchange 1 on ethereum>
    # LEASH - DAI (0x761d5dca312484036de12ba22b660a2e5b1aa211) at exchange 1 on ethereum>

    # Deduplicate trading pairs

    # - Choose the best pair with the best volume
    pairs_df = deduplicate_pairs_by_volume(pairs_df)
    pairs_universe = PandasPairUniverse(pairs_df)

    logger.info("Example of deduplicated pairs:")
    for pair in list(pairs_universe.iterate_pairs())[0:10]:
        logger.info("   Pair: %s", pair)
    # INFO:__main__:   Pair: <Pair #37836 SHIB - WETH (0x24d3dd4a62e29770cf98810b09f89d3a90279e7a) at exchange 22 on ethereum>
    # INFO:__main__:Example of deduplicated pairs:
    # INFO:__main__:   Pair: <Pair #3018988 PEPE - WETH (0x11950d141ecb863f01007add7d1a342041227b58) at exchange 3681 on ethereum>
    # INFO:__main__:   Pair: <Pair #3047249 TURBO - WETH (0x8107fca5494375fc743a9fc4d4844353a1af3d94) at exchange 3681 on ethereum>
    # INFO:__main__:   Pair: <Pair #3842242 Neiro - WETH (0x15153da0e9e13cfc167b3d417d3721bf545479bb) at exchange 3681 on ethereum>
    # INFO:__main__:   Pair: <Pair #3376429 MEME - WETH (0x70cf99553471fe6c0d513ebfac8acc55ba02ab7b) at exchange 3681 on ethereum>

    logger.info(
        "Total %d pairs to trade on %s for category %s",
        len(pairs_df),
        chain_id.name,
        category,
    )

    print("All ok")


if __name__ == "__main__":
    main()