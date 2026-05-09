from abc import ABC, abstractmethod

class BaseLaw(ABC):
    """
    Base modular class for all Laws in the game.
    Allows easy expansion and evaluation of law impacts.
    """
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    def evaluate_stability_impact(self, kingdom) -> int:
        """Returns how this law modifies base stability. Default is 0."""
        return 0

class SuccessionLaw(BaseLaw):
    pass

class AuthorityLaw(BaseLaw):
    pass

# Implementations
class ConfederatedPartition(SuccessionLaw):
    name = "Partição Confederada"
    description = "As terras são divididas entre os filhos. Causa perda de estabilidade na morte, mas vassalos adoram."

class AbsoluteAuthority(AuthorityLaw):
    name = "Autoridade Absoluta"
    description = "O Soberano dita tudo. Reduz drasticamente a lealdade do conselho, mas aumenta o exército/ouro."

    def evaluate_stability_impact(self, kingdom) -> int:
        return -15

class VassalAutonomy(AuthorityLaw):
    name = "Autonomia dos Vassalos"
    description = "Os vassalos têm alto grau de independência. Mantém todos felizes, mas os recursos fluem lentamente."

    def evaluate_stability_impact(self, kingdom) -> int:
        return +10

LAW_REGISTRY = {
    "Partição Confederada": ConfederatedPartition(),
    "Autoridade Absoluta": AbsoluteAuthority(),
    "Autonomia dos Vassalos": VassalAutonomy()
}

def get_law_object(law_name: str) -> BaseLaw:
    return LAW_REGISTRY.get(law_name, None)
