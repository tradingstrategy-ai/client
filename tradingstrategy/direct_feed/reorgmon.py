"""Chain reorganisation handling during the real-time OHLCV candle production."""

import datetime
from abc import abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple
import logging

from web3 import Web3

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BlockRecord:
    block_number: int
    block_hash: str
    timestamp: int

    def __post_init__(self):
        assert type(self.block_number) == int
        assert type(self.block_hash) == str
        assert type(self.timestamp) == int


@dataclass(slots=True, frozen=True)
class ChainReorganisationResolution:
    last_block_number: int
    latest_good_block: Optional[int]


class ChainReorganisationDetected(Exception):
    block_number: int
    original_hash: str
    new_hash: str

    def __init__(self, block_number: int, original_hash: str, new_hash: str):
        self.block_number = block_number
        self.original_hash = original_hash
        self.new_hash = new_hash

        super().__init__(f"Block reorg detected at #{block_number:,}. Original hash: {original_hash}. New hash: {new_hash}")


class ReorganisationResolutionFailure(Exception):
    """Chould not figure out chain reorgs after mutliple attempt.

    Node in a bad state?
    """



class ReorganisationMonitor:
    """Watch blockchain for reorgs.

    - Check block headers for chain reorganisations

    - Also manages the service for block timestamp lookups
    """

    def __init__(self, check_depth=200, max_reorg_resolution_attempts=10):
        self.block_map: Dict[int, BlockRecord] = {}
        self.last_block_read: int = 0
        self.check_depth = check_depth
        self.max_cycle_tries = max_reorg_resolution_attempts

    def get_last_block_read(self):
        return self.last_block_read

    def load_initial_data(self, block_count: int) -> Tuple[int, int]:
        """Get the inital block buffer filled up.

        :return:
            The initial block range to start to work with
        """
        end_block = self.get_last_block_live()
        start_block = max(end_block - block_count, 1)
        return start_block, end_block

    def add_block(self, record: BlockRecord):
        """Add new block to header tracking.

        Blocks must be added in order.
        """
        block_number = record.block_number
        assert block_number not in self.block_map, f"Block already added: {block_number}"
        self.block_map[block_number] = record

        assert self.last_block_read == block_number - 1, f"Blocks must be added in order. Last: {self.last_block_read}, got: {record}"
        self.last_block_read = block_number

    def check_block_reorg(self, block_number: int, block_hash: str):
        original_block = self.block_map.get(block_number)

        if original_block.block_hash != block_hash:
            raise ChainReorganisationDetected(block_number, original_block.block_hash, block_hash)

    def truncate(self, latest_good_block: int):
        """Delete data after a block number because chain reorg happened."""
        assert self.last_block_read
        for block_to_delete in range(latest_good_block + 1, self.last_block_read):
            del self.block_map[block_to_delete]
        self.last_block_read = latest_good_block

    def figure_reorganisation_and_new_blocks(self):
        """Compare the local block database against the live data from chain.

        Spot the differences in (block number, block header) tuples
        and determine a chain reorg.
        """
        chain_last_block = self.get_last_block_live()
        check_start_at = max(self.last_block_read - self.check_depth, 1)
        for block in self.get_block_data(check_start_at, chain_last_block):
            self.check_block_reorg(block.block_number, block.block_hash)
            if block.block_number not in self.block_map:
                self.add_block(block)

    def get_block_timestamp(self, block_number: int) -> int:
        """Return UNIX UTC timestamp of a block."""
        return self.block_map[block_number].timestamp

    def update_chain(self) -> ChainReorganisationResolution:
        """

        :return:
            Last block
        """

        tries_left = self.max_cycle_tries
        max_purge = None

        while tries_left > 0:
            try:
                self.figure_reorganisation_and_new_blocks()
                return ChainReorganisationResolution(self.last_block_read, max_purge)
            except ChainReorganisationDetected as e:
                logger.info("Chain reorganisation detected: %s", e)

                latest_good_block = e.block_number - 1

                if max_purge:
                    max_purge = min(latest_good_block, max_purge)
                else:
                    max_purge = e.block_number

                self.truncate(latest_good_block)
                tries_left -= 1

        raise ReorganisationResolutionFailure(f"Gave up chain reorg resolution. Last block: {self.last_block_read}, attempts {self.max_cycle_tries}")

    @abstractmethod
    def get_block_data(self, start_block, end_block) -> Iterable[BlockRecord]:
        """Read the new block headers.

        :param start_block:
            The first block where to read (inclusive)

        :param end_block:
            The block where to read (inclusive)
        """

    @abstractmethod
    def get_last_block_live(self) -> int:
        """Get last block number"""


class JSONRPCReorganisationMonitor(ReorganisationMonitor):
    """Watch blockchain for reorgs using eth_getBlockByNumber JSON-RPC API."""

    def __init__(self, web3: Web3, check_depth=200, max_reorg_resolution_attempts=10):
        super().__init__(check_depth=check_depth, max_reorg_resolution_attempts=max_reorg_resolution_attempts)
        self.web3 = web3

    def get_last_block_live(self):
        return self.web3.eth.block_number

    def get_block_data(self, start_block, end_block) -> Iterable[BlockRecord]:
        logger.debug("Extracting timestamps for logs %d - %d", start_block, end_block)
        web3 = self.web3

        # Collect block timestamps from the headers
        for block_num in range(start_block, end_block + 1):
            raw_result = web3.manager.request_blocking("eth_getBlockByNumber", (hex(block_num), False))
            data_block_number = raw_result["number"]
            block_hash = raw_result["hash"]
            assert type(data_block_number) == str, "Some automatic data conversion occured from JSON-RPC data. Make sure that you have cleared middleware onion for web3"
            assert int(raw_result["number"], 16) == block_num

            timestamp = int(raw_result["timestamp"], 16)

            yield BlockRecord(block_num, block_hash, timestamp)


class SyntheticReorganisationMonitor(ReorganisationMonitor):
    """A dummy reorganisation monitor for unit testing.

    Simulate block feed.
    """

    def __init__(self, block_number: int = 1):
        super().__init__()
        self.block_number = block_number

    def produce_blocks(self, block_count=1):
        """Populate the fake blocks in mock chain."""
        for x in range(block_count):
            num = self.block_number
            self.block_number += 1
            record = BlockRecord(num, hex(num), num)
            self.add_block(record)

    def get_last_block_live(self):
        return self.block_number - 1

    def get_block_data(self, start_block, end_block) -> Iterable[BlockRecord]:

        assert start_block > 0, "Cannot ask data for zero block"
        assert end_block <= self.get_last_block_live(), "Cannot ask data for blocks that are not produced yet"

        for i in range(start_block, end_block + 1):
            yield self.block_map[i]


