from __future__ import annotations
from collections.abc import Callable  # for 3.7 <= Python version < 3.10
from typing import TYPE_CHECKING

from kimmdy.topology.topology import (
    Topology,
)  # fixes circular import issues for type hints

if TYPE_CHECKING:
    from kimmdy.runmanager import RunManager
    from kimmdy.config import Config
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from kimmdy.tasks import TaskFiles
import logging
from pathlib import Path
import dill


class ConversionType(Enum):
    BREAK = auto()
    BIND = auto()
    MOVE = auto()

    def __repr__(self):
        return f"{self.name}"


@dataclass
class Conversion:
    """A Conversion.
    encompasses a single transformation, breaking of adding one bond.

    Parameters
    ----------
    type : ConversionType.BREAK or .BIND
    atom_idx : tuple(from, to)
    """

    type: ConversionType
    atom_idx: tuple[str, str]

    def __repr__(self):
        return f"{self.type}: [{' '.join(self.atom_idx)}]"



ConversionRecipe = list[Conversion]
"""A ConversionRecipe.

is a list of Conversions to encompass one reaction outcome.
In the case of breaking a bond it is simply a list of length one,
but for e.g. moving an atom from one binding partner to another
it is a list with one BREAK and one BIND operation.
"""


@dataclass
class ReactionOutcome:
    """A ReactionOutcome
    encompasses a ConversionRecipe, its (constant) rate, as well as
    time-dependent rates r_ts with an associated timestep ts. 
    """

    recipe: ConversionRecipe
    rate: float 
    r_ts: list[float]
    ts: list[int]

@dataclass
class ReactionResult:
    """A ReactionResult
    encompasses a list of ReactionOutcomes.
    """

    outcomes: list[ReactionOutcome]

    def to_csv(self, path: Path):
        """Write a ReactionResult as defined in the reaction module to a csv file"""
        with open(path,"w") as f:    
            f.write(',rate,recipe,r_ts,ts\n')        
            f.write('\n'.join([f"{i},{RO.rate},\"{RO.recipe}\",\"[{','.join(map(str,RO.r_ts))}]\",\"[{','.join(map(str,RO.ts))}]\"" for i,RO in enumerate(self.outcomes)]))
    
    def to_dill(self, path: Path):
        with open(path,"wb") as f:
            dill.dump(self, f)

    @classmethod
    def from_dill(_, path: Path):
        with open(path,"rb") as f:
            return dill.load(f)


class Reaction(ABC):
    """Reaction base class
    hast a type_scheme, which is a dict of types of possible entries in config.
    Used to read and check the input config.
    To not use this feature return empty dict.

    Example:
    ```python
    {"homolysis": {"edis": Path, "bonds": Path}}
    ```
    """

    type_scheme = dict()

    def __init__(self, name, runmng: RunManager):
        self.name = name
        self.runmng = runmng
        # sub config, settings of this specific reaction:
        # TODO: does it even need it's own config explicitlye if if can always access the full config anyways?
        self.config: Config = self.runmng.config.reactions.attr(self.name)

        logging.debug(f"Reaction {self.name} instatiated.")

    @abstractmethod
    def get_reaction_result(self, files: TaskFiles) -> ReactionResult:
        pass
