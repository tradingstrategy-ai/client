"""Test /top endpoint."""
import datetime

from tradingstrategy.chain import ChainId
from tradingstrategy.client import Client
from tradingstrategy.top import TopPairsReply, TopPairMethod


def test_load_top_by_exchanges(persistent_test_client: Client):
    """Load 10 top pairs by liquidity from /top endpoint.

    - Integration test

    - Get whatever pairs we have today
    """

    client = persistent_test_client

    top_reply = client.fetch_top_pairs(
        chain_ids={ChainId.ethereum},
        exchange_slugs={"uniswap-v2", "uniswap-v3"},
        limit=10,
    )

    assert isinstance(top_reply, TopPairsReply)
    assert len(top_reply.included) == 10
    assert len(top_reply.excluded) > 0  # There is always something to be excluded

    # Because this is a dynamic reply,
    # we just check accessor methods work
    for pair in top_reply.included:
        assert pair.get_persistent_string_id() is not None
        assert isinstance(pair.volume_updated_at, datetime.datetime)
        assert isinstance(pair.tvl_updated_at, datetime.datetime)
        assert isinstance(pair.queried_at, datetime.datetime)
        assert pair.volume_24h_usd > 0, f"Top pair issue on {pair}"
        assert pair.tvl_latest_usd > 0, f"Top pair issue on {pair}"
        if pair.base_token != "WETH":
            assert pair.token_sniffer_score, f"Top pair issue on {pair}"
            assert pair.token_sniffer_score > 0, f"Top pair issue on {pair}"


def test_load_top_by_tokens(persistent_test_client: Client):
    """Load top trading pairs by token addresses from /top endpoint.

    - Integration test

    - Inspect TokenSniffer reply data for well known tokens
    """

    client = persistent_test_client

    # AAVE 0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9
    # COMP 0xc00e94Cb662C3520282E6f5717214004A7f26888
    top_reply = client.fetch_top_pairs(
        chain_ids={ChainId.ethereum},
        addresses={"0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", "0xc00e94Cb662C3520282E6f5717214004A7f26888"},
        method=TopPairMethod.by_token_addresses,
    )

    assert isinstance(top_reply, TopPairsReply)
    assert len(top_reply.included) == 3
    assert len(top_reply.excluded) == 0  # There is always something to be excluded

    weth_usdc = top_reply[0]
    assert weth_usdc.get_buy_tax() == 0
    assert weth_usdc.get_sell_tax() == 0

    # Because this is a dynamic reply,
    # we just check accessor methods work
    for pair in top_reply.included:
        assert pair.get_persistent_string_id() is not None
        assert isinstance(pair.volume_updated_at, datetime.datetime)
        assert isinstance(pair.tvl_updated_at, datetime.datetime)
        assert isinstance(pair.queried_at, datetime.datetime)
        assert pair.volume_24h_usd > 0, f"Top pair issue on {pair}"
        assert pair.tvl_latest_usd > 0, f"Top pair issue on {pair}"
        if pair.base_token != "WETH":
            assert pair.token_sniffer_score, f"Top pair issue on {pair}"
            assert pair.token_sniffer_score > 0, f"Top pair issue on {pair}"


def test_token_tax(persistent_test_client: Client):
    """Check the token tax of a token."""

    client = persistent_test_client

    # Example tokens with tax
    # FRIEND.TECH 0x71fc7cf3e26ce5933fa1952590ca6014a5938138
    # $PAAL 0x14feE680690900BA0ccCfC76AD70Fd1b95D10e16
    # TRUMP 0x576e2BeD8F7b46D34016198911Cdf9886f78bea7
    top_reply = client.fetch_top_pairs(
        chain_ids={ChainId.ethereum},
        addresses={"0x71fc7cf3e26ce5933fa1952590ca6014a5938138", "0x14feE680690900BA0ccCfC76AD70Fd1b95D10e16", "0x576e2BeD8F7b46D34016198911Cdf9886f78bea7"},
        method=TopPairMethod.by_token_addresses,
    )

    assert isinstance(top_reply, TopPairsReply)
    assert len(top_reply.included) == 1
    assert len(top_reply.excluded) == 0
    friend_weth = top_reply.included[0]
    assert friend_weth.base_token == "FRIEND.TECH"
    assert friend_weth.quote_token == "WETH"
    assert 0 < friend_weth.get_buy_tax() < 1
    assert 0 < friend_weth.get_sell_tax() < 1


