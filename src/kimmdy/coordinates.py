import logging
from typing import Optional, Union
from copy import deepcopy
from kimmdy.topology.topology import MoleculeType, Topology
from kimmdy.topology.ff import FF
from kimmdy.topology.atomic import (
    Bond,
    Angle,
    Dihedral,
    DihedralType,
    MultipleDihedrals,
    Interaction,
    InteractionType,
    InteractionTypes,
)
from kimmdy.topology.utils import (
    match_atomic_item_to_atomic_type,
    get_protein_section,
    set_protein_section,
)

logger = logging.getLogger(__name__)


def is_parameterized(entry: Interaction):
    """Parameterized topology entries have c0 and c1 attributes != None"""
    return entry.c0 != None and entry.c1 != None


def get_explicit_MultipleDihedrals(
    dihedral_key: tuple[str, str, str, str],
    mol: MoleculeType,
    dihedrals_in: Optional[MultipleDihedrals],
    ff: FF,
    periodicity_max: int = 6
) -> Union[MultipleDihedrals, None]:
    """Takes a valid dihedral key and returns explicit
    dihedral parameters for a given topology
    """
    if not dihedrals_in:
        return None

    if not "" in dihedrals_in.dihedrals.keys():
        # empty string means implicit parameters
        return dihedrals_in

    type_key = [mol.atoms[x].type for x in dihedral_key]

    multiple_dihedrals = MultipleDihedrals(*dihedral_key, "9", {})
    for periodicity in range(1, periodicity_max + 1):
        match_obj = match_atomic_item_to_atomic_type(
            type_key, ff.proper_dihedraltypes, str(periodicity)
        )
        if match_obj:
            assert isinstance(match_obj, DihedralType)
            l = [*dihedral_key, "9", match_obj.c0, match_obj.c1, match_obj.periodicity]
            multiple_dihedrals.dihedrals[str(periodicity)] = Dihedral.from_top_line(l)

    if not multiple_dihedrals.dihedrals:
        return None

    return multiple_dihedrals


def get_explicit_or_type(
    key: tuple[str, ...],
    interaction: Union[Interaction, None],
    interaction_types: InteractionTypes,
    mol: MoleculeType,
    periodicity: str = "",
) -> Union[Interaction, InteractionType, None]:
    """Takes an Interaction and associated key, InteractionTypes, Topology
    and Periodicity (for dihedrals) and returns an object with the parameters of this Interaction
    """
    if not interaction:
        return None

    if is_parameterized(interaction):
        return interaction

    type_key = [mol.atoms[x].type for x in key]
    match_obj = match_atomic_item_to_atomic_type(
        type_key, interaction_types, periodicity
    )

    if match_obj:
        assert isinstance(match_obj, InteractionType)
        return match_obj
    else:
        raise ValueError(
            f"Could not find explicit parameters for {[type_key,interaction]} in line or in force field {ff} attached to {mol}."
        )


def merge_dihedrals(
    dihedral_key: tuple[str, str, str, str],
    interactionA: Union[Dihedral, None],
    interactionB: Union[Dihedral, None],
    interaction_typesA: dict[tuple[str, ...], DihedralType],
    interaction_typesB: dict[tuple[str, ...], DihedralType],
    molA: MoleculeType,
    molB: MoleculeType,
    funct: str,
    periodicity: str,
) -> Dihedral:
    """Merge one to two Dihedrals or -Types into a Dihedral in free-energy syntax"""
    # convert implicit standard ff parameters to explicit, if necessary
    if interactionA:
        parameterizedA = get_explicit_or_type(
            dihedral_key,
            interactionA,
            interaction_typesA,
            molA,
            periodicity,
        )
    else:
        parameterizedA = None

    if interactionB:
        parameterizedB = get_explicit_or_type(
            dihedral_key,
            interactionB,
            interaction_typesB,
            molB,
            periodicity,
        )
    else:
        parameterizedB = None

    # construct parameterized Dihedral
    if parameterizedA and parameterizedB:
        # same
        dihedralmerge = Dihedral(
            *dihedral_key,
            funct=funct,
            c0=parameterizedA.c0,
            c1=parameterizedA.c1,
            periodicity=parameterizedA.periodicity,
            c3=parameterizedB.c0,
            c4=parameterizedB.c1,
            c5=parameterizedB.periodicity,
        )
    elif parameterizedA:
        # breaking
        dihedralmerge = Dihedral(
            *dihedral_key,
            funct=funct,
            c0=parameterizedA.c0,
            c1=parameterizedA.c1,
            periodicity=parameterizedA.periodicity,
            c3=parameterizedA.c0,
            c4="0.00",
            c5=parameterizedA.periodicity,
        )
    elif parameterizedB:
        # binding
        dihedralmerge = Dihedral(
            *dihedral_key,
            funct="9",
            c0=parameterizedB.c0,
            c1="0.00",
            periodicity=parameterizedB.periodicity,
            c3=parameterizedB.c0,
            c4=parameterizedB.c1,
            c5=parameterizedB.periodicity,
        )
    else:
        raise ValueError(
            f"Tried to merge two dihedrals of {dihedral_key} but no parameterized dihedrals found!"
        )
    return dihedralmerge



def merge_top_moleculetypes_parameter_growth(
    molA: MoleculeType, molB: MoleculeType, ff: FF, focus_nr: Union[list[str], None] = None
) -> MoleculeType:
    """Takes two Topologies and joins them for a smooth free-energy like parameter transition simulation"""
    hyperparameters = {
        "morse_well_depth": "400.0",
        "morse_steepness": "10.0",
        "morse_dist_factor": 4,
    }  # well_depth D [kJ/mol], steepness [nm-1], dist_factor for bond length

    # TODO:
    # think about how to bring focus_nr into this

    ## atoms
    for nr in molA.atoms.keys():
        atomA = molA.atoms[nr]
        atomB = molB.atoms[nr]
        if atomA != atomB:
            if atomA.charge != atomB.charge:
                atomB.typeB = deepcopy(atomB.type)
                atomB.type = deepcopy(atomA.type)
                atomB.chargeB = deepcopy(atomB.charge)
                atomB.charge = deepcopy(atomA.charge)
                atomB.massB = deepcopy(atomB.mass)
                atomB.mass = deepcopy(atomA.mass)
            else:
                logger.debug(
                    f"Atom {nr} with A:{atomA} and B:{atomB} changed during changemanager step but not the charges!"
                )

    ## bonds
    keysA = set(molA.bonds.keys())
    keysB = set(molB.bonds.keys())
    keys = keysA | keysB

    for key in keys:
        interactionA = molA.bonds.get(key)
        interactionB = molB.bonds.get(key)

        if interactionA != interactionB:
            parameterizedA = get_explicit_or_type(
                key, interactionA, ff.bondtypes, molA
            )
            parameterizedB = get_explicit_or_type(
                key, interactionB, ff.bondtypes, molB
            )
            if parameterizedA and parameterizedB:
                molB.bonds[key] = Bond(
                    *key,
                    funct=parameterizedB.funct,
                    c0=parameterizedA.c0,
                    c1=parameterizedA.c1,
                    c2=parameterizedB.c0,
                    c3=parameterizedB.c1,
                )
            elif parameterizedA:
                molB.bonds[key] = Bond(
                    *key,
                    funct="3",
                    c0=parameterizedA.c0,
                    c1=hyperparameters["morse_well_depth"],
                    c2=hyperparameters["morse_steepness"],
                    c3=f"{float(parameterizedA.c0)*hyperparameters['morse_dist_factor']:7.5f}",
                    c4="0.00",
                    c5=hyperparameters["morse_steepness"],
                )

                # update bound_to
                atompair = [molB.atoms[key[0]], molB.atoms[key[1]]]
                atompair[0].bound_to_nrs.append(atompair[1].nr)
                atompair[1].bound_to_nrs.append(atompair[0].nr)

            elif parameterizedB:
                molB.bonds[key] = Bond(
                    *key,
                    funct="3",
                    c0=f"{float(parameterizedB.c0)*hyperparameters['morse_dist_factor']:7.5f}",
                    c1="0.00",
                    c2=hyperparameters["morse_steepness"],
                    c3=parameterizedB.c0,
                    c4=hyperparameters["morse_well_depth"],
                    c5=hyperparameters["morse_steepness"],
                )

    ## pairs and exclusions
    exclusions_content = molB.atomics.get("exclusions", [])
        # maybe hook this up to empty_sections if it gets accessible
    for key in keysA - keysB:
        molB.pairs.pop(key, None)
        exclusions_content.append(list(key))

    molB.atomics["exclusions"] = exclusions_content

    ## angles
    keys = set(molA.angles.keys()) | set(molB.angles.keys())
    for key in keys:
        interactionA = molA.angles.get(key)
        interactionB = molB.angles.get(key)

        if interactionA != interactionB:
            parameterizedA = get_explicit_or_type(
                key, interactionA, ff.angletypes, molA
            )
            parameterizedB = get_explicit_or_type(
                key, interactionB, ff.angletypes, molB
            )
            if parameterizedA and parameterizedB:
                molB.angles[key] = Angle(
                    *key,
                    funct=parameterizedB.funct,
                    c0=parameterizedA.c0,
                    c1=parameterizedA.c1,
                    c2=parameterizedB.c0,
                    c3=parameterizedB.c1,
                )
            elif parameterizedA:
                molB.angles[key] = Angle(
                    *key,
                    funct="1",
                    c0=parameterizedA.c0,
                    c1=parameterizedA.c1,
                    c2=parameterizedA.c0,
                    c3="0.00",
                )
            elif parameterizedB:
                molB.angles[key] = Angle(
                    *key,
                    funct="1",
                    c0=parameterizedB.c0,
                    c1="0.00",
                    c2=parameterizedB.c0,
                    c3=parameterizedB.c1,
                )
            else:
                logger.warning(f"Could not parameterize angle {key}.")

    ## dihedrals
    ## proper dihedrals
    # proper dihedrals have a nested structure and need a different treatment from bonds, angles and improper dihedrals
    # if indices change atomtypes and parameters change because of that, it will ignore these parameter change

    keys = set(molA.proper_dihedrals.keys()) | set(molB.proper_dihedrals.keys())
    for key in keys:
        multiple_dihedralsA = molA.proper_dihedrals.get(key)
        multiple_dihedralsB = molB.proper_dihedrals.get(key)

        if multiple_dihedralsA != multiple_dihedralsB:
            multiple_dihedralsA = get_explicit_MultipleDihedrals(
                key, molA, multiple_dihedralsA, ff
            )
            multiple_dihedralsB = get_explicit_MultipleDihedrals(
                key, molB, multiple_dihedralsB, ff
            )
            keysA = (
                set(multiple_dihedralsA.dihedrals.keys())
                if multiple_dihedralsA
                else set()
            )
            keysB = (
                set(multiple_dihedralsB.dihedrals.keys())
                if multiple_dihedralsB
                else set()
            )

            molB.proper_dihedrals[key] = MultipleDihedrals(*key, "9", {})
            periodicities = keysA | keysB
            for periodicity in periodicities:
                assert isinstance(periodicity, str)
                interactionA = (
                    multiple_dihedralsA.dihedrals.get(periodicity)
                    if multiple_dihedralsA
                    else None
                )
                interactionB = (
                    multiple_dihedralsB.dihedrals.get(periodicity)
                    if multiple_dihedralsB
                    else None
                )

                molB.proper_dihedrals[key].dihedrals[periodicity] = merge_dihedrals(
                    key,
                    interactionA,
                    interactionB,
                    ff.proper_dihedraltypes,
                    ff.proper_dihedraltypes,
                    molA,
                    molB,
                    "9",
                    periodicity,
                )

    ## improper dihedrals
    # all impropers in amber99SB ffbonded.itp have a periodicity of 2
    # but not the ones defined in aminoacids.rtp. For now, I am assuming
    # a periodicity of 2 in this section

    keys = set(molA.improper_dihedrals.keys()) | set(molB.improper_dihedrals.keys())
    for key in keys:
        interactionA = molA.improper_dihedrals.get(key)
        interactionB = molB.improper_dihedrals.get(key)

        if interactionA != interactionB:
            molB.improper_dihedrals[key] = merge_dihedrals(
                key,
                interactionA,
                interactionB,
                ff.improper_dihedraltypes,
                ff.improper_dihedraltypes,
                molA,
                molB,
                "4",
                "2",
            )

    ## update is_radical attribute of Atom objects in topology
    molB._test_for_radicals()

    return molB

def merge_top_parameter_growth(
    topA: Topology, topB: Topology, focus_nr: Union[list[str], None] = None
) -> Topology:
    """Takes two Topologies and joins them for a smooth free-energy like parameter transition simulation.


    TODO: for now this assumes that only one moleculeype (the first, index 0) is of interest.
    """

    molA = list(topA.moleculetypes.values())[0]
    molB = list(topB.moleculetypes.values())[0]
    molB = merge_top_moleculetypes_parameter_growth(molA, molB, topB.ff, focus_nr)

    return topB


