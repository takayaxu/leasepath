"""Block-granular research cache; logical block keys include the full prefix.

Pins belong to owners. Expiry removes only protection, never resident data.
The optional future_rank hook is used solely by a labelled offline diagnostic.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math


def block_keys(tokens, compatibility_id, block_size):
    if block_size <= 0 or len(tokens) % block_size:
        raise ValueError('v0.1 requires positive block size and aligned token lengths')
    return tuple(hashlib.sha256(json.dumps([compatibility_id, tokens[:end]], separators=(',', ':')).encode()).hexdigest()
                 for end in range(block_size, len(tokens)+1, block_size))


@dataclass
class Block:
    slot: int
    generation: int
    last_touch: int
    pins: dict = field(default_factory=dict)
    active: set = field(default_factory=set)


class Cache:
    def __init__(self, capacity_blocks, retention_blocks, block_bytes):
        if not 0 <= retention_blocks < capacity_blocks or block_bytes <= 0:
            raise ValueError('require 0 <= retention < capacity and positive block bytes')
        self.capacity = capacity_blocks
        self.retention_budget = retention_blocks
        self.block_bytes = block_bytes
        self.blocks = {}
        self.generations = [0] * capacity_blocks
        self.clock = 0
        self.now = 0.0
        self.protected_byte_seconds = 0.0
        self.evictions = 0
        self.admitted = 0
        self.rejected = 0

    def advance(self, now):
        if not math.isfinite(now) or now < self.now:
            raise ValueError('cache time must be finite and monotonic')
        for block in self.blocks.values():
            if block.pins:
                self.protected_byte_seconds += max(0, min(now, max(block.pins.values()))-self.now)*self.block_bytes
            block.pins = {owner: expiry for owner, expiry in block.pins.items() if expiry > now}
        self.now = now

    def unpin(self, owner):
        for block in self.blocks.values():
            block.pins.pop(owner, None)

    def prefix_hits(self, keys):
        count = 0
        for key in keys:
            if key not in self.blocks:
                break
            count += 1
        return count

    def can_start(self, keys):
        if len(keys) > self.capacity:
            raise ValueError('request exceeds total KV pool')
        wanted = set(keys)
        missing = sum(key not in self.blocks for key in wanted)
        free = self.capacity-len(self.blocks)
        evictable = sum(key not in wanted and not b.pins and not b.active for key, b in self.blocks.items())
        return missing <= free+evictable

    def next_expiry(self):
        return min((expiry for b in self.blocks.values() for expiry in b.pins.values()), default=math.inf)

    def start(self, owner, keys, future_rank=None):
        if not self.can_start(keys):
            return None
        hits = self.prefix_hits(keys)
        wanted = set(keys)
        # Acquire active references before dropping this owner's idle pins.
        for key in keys:
            if key in self.blocks:
                self.blocks[key].active.add(owner)
        self.unpin(owner)
        for key in keys:
            self.clock += 1
            if key not in self.blocks:
                if len(self.blocks) == self.capacity:
                    candidates = [k for k, b in self.blocks.items() if k not in wanted and not b.pins and not b.active]
                    if future_rank is None:
                        victim = min(candidates, key=lambda k: (self.blocks[k].last_touch, k))
                    else:
                        victim = max(candidates, key=lambda k: (future_rank(k), -self.blocks[k].last_touch, k))
                    del self.blocks[victim]
                    self.evictions += 1
                used = {b.slot for b in self.blocks.values()}
                slot = next(i for i in range(self.capacity) if i not in used)
                self.generations[slot] += 1
                self.blocks[key] = Block(slot, self.generations[slot], self.clock)
            self.blocks[key].last_touch = self.clock
            self.blocks[key].active.add(owner)
        return hits

    def finish(self, owner, keys, ttl):
        if not math.isfinite(ttl) or ttl < 0:
            raise ValueError('TTL must be finite and nonnegative')
        for key in keys:
            self.blocks[key].active.discard(owner)
        if ttl == 0:
            return
        protected = {k for k, b in self.blocks.items() if b.pins}
        if len(protected | set(keys)) > self.retention_budget:
            self.rejected += 1
            return
        for key in keys:
            self.blocks[key].pins[owner] = self.now+ttl
        self.admitted += 1

    def physical_ids(self, keys):
        return [('synthetic-epoch', 0, self.blocks[k].slot, self.blocks[k].generation) for k in keys if k in self.blocks]
