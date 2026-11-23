"""Explicit adapters: these do not claim to match unseen Build 030 APIs."""
from .controller import CRAMPUMemoryController, AuthorizationError

class SuitMemoryAdapter:
    def __init__(self, controller: CRAMPUMemoryController, capability_check):
        if not callable(capability_check): raise TypeError('capability_check must be callable')
        self.controller = controller
        self.capability_check = capability_check

    def _check(self, agent_id, action, block_id):
        if not self.capability_check(agent_id, action, block_id):
            raise AuthorizationError('SoSo Suit denied capability')

    def remember(self, agent_id, block_id, payload):
        self._check(agent_id, 'memory.write', block_id)
        self.controller.write_hot(agent_id, block_id, payload)
        txid = self.controller.stage_to_warm(agent_id, block_id)
        return self.controller.commit_to_cool(agent_id, txid)

    def recall(self, agent_id, block_id):
        self._check(agent_id, 'memory.read', block_id)
        return self.controller.load_from_cool(block_id, agent_id)

class Brain49MemoryAdapter:
    def __init__(self, suit_adapter: SuitMemoryAdapter): self.suit = suit_adapter
    def big49_id(self, index):
        if type(index) is not int or not 0 <= index < 1600: raise ValueError('BIG49 index 0..1599')
        return index
    def small49_id(self, index):
        if type(index) is not int or not 0 <= index < 608: raise ValueError('SMALL49 index 0..607')
        return 1600 + index
    def write(self, agent_id, family, index, payload):
        block_id = self.big49_id(index) if family == 'BIG49' else self.small49_id(index) if family == 'SMALL49' else None
        if block_id is None: raise ValueError('unknown family')
        return self.suit.remember(agent_id, block_id, payload)
    def read(self, agent_id, family, index):
        block_id = self.big49_id(index) if family == 'BIG49' else self.small49_id(index) if family == 'SMALL49' else None
        if block_id is None: raise ValueError('unknown family')
        return self.suit.recall(agent_id, block_id)
