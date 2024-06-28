import logging
import textwrap
from copy import copy, deepcopy
from itertools import permutations
from pathlib import Path
from typing import Callable, Optional, Union

from numpy import empty

from kimmdy.constants import (ATOM_ID_FIELDS, ATOMTYPE_BONDORDER_FLAT,
                              REACTIVE_MOLECULEYPE, RESNR_ID_FIELDS)
from kimmdy.parsing import TopologyDict, empty_section
from kimmdy.plugins import BasicParameterizer, Parameterizer
from kimmdy.recipe import Bind, Break, RecipeStep
from kimmdy.topology.atomic import (Angle, Atom, Bond, Dihedral,
                                    DihedralRestraint, Exclusion,
                                    MoleculeTypeHeader, MultipleDihedrals,
                                    Pair, PositionRestraint,
                                    ResidueImproperSpec, Settle)
from kimmdy.topology.ff import FF
from kimmdy.topology.utils import (attributes_to_list,
                                   get_moleculetype_atomics,
                                   get_moleculetype_header,
                                   get_residue_fragments, get_top_section,
                                   increment_field, is_not_solvent_or_ion,
                                   set_top_section)
from kimmdy.utils import TopologyAtomAddress

logger = logging.getLogger("kimmdy.topology")


class MoleculeType:
    """One moleculetype in the topology

    Attributes
    ----------
    atoms : dict[str, Atom]
    bonds : dict[tuple[str, str], Bond]
    pairs : dict[tuple[str, str], Pair]
    angles : dict[tuple[str, str, str], Angle]
    proper_dihedrals : dict[tuple[str, str, str, str], MultipleDihedrals]
    improper_dihedrals : dict[tuple[str, str, str, str], Dihedral]
    position_restraints : dict[str, PositionRestraint]
    dihedral_restraints : dict[tuple[str, str, str, str], DihedralRestraint]
    radicals : dict[str, Atom]
        dict mapping atom indices to atom objects for storing all radical atoms

    """

    def __init__(
        self, header: MoleculeTypeHeader, atomics: dict, radicals: Optional[str] = None
    ) -> None:
        self.name = header.name
        self.nrexcl = header.nrexcl
        self.atomics = atomics

        logger.debug(f"parsing molecule {self.name}")

        self.atoms: dict[str, Atom] = {}
        self.bonds: dict[tuple[str, str], Bond] = {}
        self.pairs: dict[tuple[str, str], Pair] = {}
        self.angles: dict[tuple[str, str, str], Angle] = {}
        self.proper_dihedrals: dict[tuple[str, str, str, str], MultipleDihedrals] = {}
        self.improper_dihedrals: dict[tuple[str, str, str, str], MultipleDihedrals] = {}
        self.position_restraints: dict[str, PositionRestraint] = {}
        self.dihedral_restraints: dict[tuple[str, str, str, str], DihedralRestraint] = (
            {}
        )
        self.settles: dict[str, Settle] = {}
        self.exclusions: dict[tuple[str, str], Exclusion] = {}

        self._parse_atoms()
        self._parse_bonds()
        self._parse_pairs()
        self._parse_angles()
        self._parse_dihedrals()
        self._parse_restraints()
        self._parse_settles()
        self._parse_exclusions()
        self._initialize_graph()

        # must be after self._parse_atoms
        self.radicals: dict[str, Atom] = {}
        if radicals is not None:
            if self.name == "Reactive":
                logger.debug(
                    f"Using 'radicals' section from config file with entries: '{radicals}'."
                )
            for radical in radicals.split(sep=" "):
                atom = self.atoms.get(radical)
                if atom:
                    self.radicals[radical] = atom
                    atom.is_radical = True
                else:
                    logger.debug(
                        f"Atom nr {radical} from 'radicals' section in config file not in topology. Ignoring this entry."
                    )
        else:
            self.find_radicals()

    def __str__(self) -> str:
        return textwrap.dedent(
            f"""\
        Moleculetype {self.name} with:
        {len(self.atoms)} atoms,
        {len(self.bonds)} bonds,
        {len(self.angles)} angles,
        {len(self.pairs)} pairs,
        {len(self.proper_dihedrals)} proper dihedrals
        {len(self.improper_dihedrals)} improper dihedrals
        {len(self.position_restraints)} position restraints
        {len(self.dihedral_restraints)} dihedral restraints
        """
        )

    def __repr__(self) -> str:
        return f"Molecule({(self.name, self.nrexcl)}, {self.atomics})"

    def _repr_pretty_(self, p, _):
        """A __repr__ for ipython.

        This whill be used if just the name of the object is entered in the ipython shell
        or a jupyter notebook.

        p is an instance of
        [IPython.lib.pretty.PrettyPrinter](https://ipython.org/ipython-doc/3/api/generated/IPython.lib.pretty.html)
        """
        p.text(str(self))

    def _parse_atoms(self):
        """Parse atoms from topology dictionary."""
        ls = self.atomics.get("atoms")
        if ls is None:
            raise ValueError(f"No atoms found in topology for molecule {self.name}.")
        for l in ls:
            atom = Atom.from_top_line(l)
            self.atoms[atom.nr] = atom

    def _parse_bonds(self):
        """Parse bond from topology dictionary."""
        ls = self.atomics.get("bonds")
        if ls is None:
            return
        for l in ls:
            bond = Bond.from_top_line(l)
            self.bonds[(bond.ai, bond.aj)] = bond

    def _parse_pairs(self):
        """Parse pairs from topology dictionary."""
        ls = self.atomics.get("pairs")
        if ls is None:
            return
        for l in ls:
            pair = Pair.from_top_line(l)
            self.pairs[(pair.ai, pair.aj)] = pair

    def _parse_angles(self):
        """Parse angles from topology dictionary."""
        ls = self.atomics.get("angles")
        if ls is None:
            return
        for l in ls:
            angle = Angle.from_top_line(l)
            self.angles[(angle.ai, angle.aj, angle.ak)] = angle

    def _parse_dihedrals(self):
        """Parse improper and proper dihedrals from topology dictionary."""
        ls = self.atomics.get("dihedrals")
        if ls is None:
            return
        for l in ls:
            dihedral = Dihedral.from_top_line(l)
            key = (dihedral.ai, dihedral.aj, dihedral.ak, dihedral.al)
            if dihedral.funct == "4":
                if self.improper_dihedrals.get(key) is None:
                    self.improper_dihedrals[key] = MultipleDihedrals(
                        *key, dihedral.funct, dihedrals={}
                    )
                self.improper_dihedrals[key].dihedrals[dihedral.periodicity] = dihedral
            else:
                if self.proper_dihedrals.get(key) is None:
                    self.proper_dihedrals[key] = MultipleDihedrals(
                        *key, dihedral.funct, dihedrals={}
                    )
                self.proper_dihedrals[key].dihedrals[dihedral.periodicity] = dihedral

    def _parse_restraints(self):
        """Parse restraints from topology dictionary."""
        ls = self.atomics.get("position_restraints")
        if not ls:
            ls = []
        condition = None
        for l in ls:
            restraint = PositionRestraint.from_top_line(l, condition=condition)
            self.position_restraints[restraint.ai] = restraint
        ls = self.atomics.get("dihedral_restraints")
        if not ls:
            ls = []
        for l in ls:
            restraint = DihedralRestraint.from_top_line(l)
            self.dihedral_restraints[
                (restraint.ai, restraint.aj, restraint.ak, restraint.al)
            ] = restraint

    def _parse_settles(self):
        """Parse settles from topology dictionary."""
        ls = self.atomics.get("settles")
        if not ls:
            return
        for l in ls:
            settle = Settle.from_top_line(l)
            self.settles[settle.nr] = settle

    def _parse_exclusions(self):
        """Parse exclusions from topology dictionary."""
        ls = self.atomics.get("exclusions")
        if not ls:
            return
        for _, l in enumerate(ls):
            exclusion = Exclusion.from_top_line(l)
            self.exclusions[(l[0], l[1])] = exclusion

    def _initialize_graph(self):
        """Add a list of atom nrs bound to an atom to each atom."""
        for bond in self.bonds.values():
            i = bond.ai
            j = bond.aj
            self.atoms[i].bound_to_nrs.append(j)
            self.atoms[j].bound_to_nrs.append(i)

    def find_radicals(self):
        """Find atoms that are radicals and update self.radicals.

        Iterate over all atoms and designate them as radicals if they have
        fewer bounds than their natural bond order.
        """
        logger.debug(
            "Trying to infer radical status based on AMBER(!!) atomtype bond order."
        )
        self.radicals = {}
        for atom in self.atoms.values():
            bo = ATOMTYPE_BONDORDER_FLAT.get(atom.type)
            if bo is None:
                logger.warning(
                    f"Atomtype {atom.type} not found in AMBER atomtypes. Cannot infer radical status."
                )
            if bo and bo > len(atom.bound_to_nrs):
                self.radicals[atom.nr] = atom
                atom.is_radical = True
            else:
                atom.is_radical = False
        return None

    def _update_atomics_dict(self):
        self.atomics["atoms"] = [attributes_to_list(x) for x in self.atoms.values()]
        self.atomics["bonds"] = [attributes_to_list(x) for x in self.bonds.values()]
        self.atomics["angles"] = [attributes_to_list(x) for x in self.angles.values()]
        self.atomics["pairs"] = [attributes_to_list(x) for x in self.pairs.values()]
        self.atomics["dihedrals"] = [
            attributes_to_list(x)
            for dihedrals in self.proper_dihedrals.values()
            for x in dihedrals.dihedrals.values()
        ] + [
            attributes_to_list(x)
            for dihedrals in self.improper_dihedrals.values()
            for x in dihedrals.dihedrals.values()
        ]
        self.atomics["position_restraints"] = [
            attributes_to_list(x) for x in self.position_restraints.values()
        ]
        self.atomics["dihedral_restraints"] = [
            attributes_to_list(x) for x in self.dihedral_restraints.values()
        ]
        self.atomics["settles"] = [attributes_to_list(x) for x in self.settles.values()]
        self.atomics["exclusions"] = [
            attributes_to_list(x) for x in self.exclusions.values()
        ]

    def _get_atom_bonds(self, atom_nr: str) -> list[tuple[str, str]]:
        """Get all bonds a particular atom is involved in."""
        ai = atom_nr
        bonds = []
        for aj in self.atoms[ai].bound_to_nrs:
            if int(ai) < int(aj):
                bonds.append((ai, aj))
            else:
                bonds.append((aj, ai))
        return bonds

    def _get_atom_pairs(self, _: str) -> list[tuple[str, str]]:
        raise NotImplementedError(
            "get_atom_pairs is not implementes. Get the pairs as the endpoints of dihedrals instead."
        )

    def _get_atom_angles(self, atom_nr: str) -> list[tuple[str, str, str]]:
        """
        Gets a list of angles that one atom is involved in based on the bond
        information stored in the atom.

        Angles between atoms ai, aj, ak satisfy ai < ak
        """
        return self._get_center_atom_angles(atom_nr) + self._get_margin_atom_angles(
            atom_nr
        )

    def _get_center_atom_angles(self, atom_nr: str) -> list[tuple[str, str, str]]:
        # atom_nr in the middle of an angle
        angles = []
        aj = atom_nr
        partners = self.atoms[aj].bound_to_nrs
        for ai in partners:
            for ak in partners:
                if int(ai) < int(ak):
                    angles.append((ai, aj, ak))
        return angles

    def _get_margin_atom_angles(self, atom_nr: str) -> list[tuple[str, str, str]]:
        # atom_nr at the outer corner of angle
        angles = []
        ai = atom_nr
        for aj in self.atoms[ai].bound_to_nrs:
            for ak in self.atoms[aj].bound_to_nrs:
                if ai == ak:
                    continue
                if int(ai) < int(ak):
                    angles.append((ai, aj, ak))
                else:
                    angles.append((ak, aj, ai))
        return angles

    def _get_atom_proper_dihedrals(
        self, atom_nr: str
    ) -> list[tuple[str, str, str, str]]:
        """
        Gets a list of dihedrals that one atom is involved in based on the bond
        information stored in the atom.
        """
        return self._get_center_atom_dihedrals(
            atom_nr
        ) + self._get_margin_atom_dihedrals(atom_nr)

    def _get_center_atom_dihedrals(
        self, atom_nr: str
    ) -> list[tuple[str, str, str, str]]:
        dihedrals = []
        aj = atom_nr
        partners = self.atoms[aj].bound_to_nrs
        for ai in partners:
            for ak in partners:
                if ai == ak:
                    continue
                for al in self.atoms[ak].bound_to_nrs:
                    if al == ak or aj == al:
                        continue
                    if int(aj) < int(ak):
                        dihedrals.append((ai, aj, ak, al))
                    else:
                        dihedrals.append((al, ak, aj, ai))
        return dihedrals

    def _get_margin_atom_dihedrals(
        self, atom_nr: str
    ) -> list[tuple[str, str, str, str]]:
        dihedrals = []
        ai = atom_nr
        for aj in self.atoms[ai].bound_to_nrs:
            for ak in self.atoms[aj].bound_to_nrs:
                if ai == ak:
                    continue
                for al in self.atoms[ak].bound_to_nrs:
                    if al == ak or aj == al:
                        continue
                    if int(aj) < int(ak):
                        dihedrals.append((ai, aj, ak, al))
                    else:
                        dihedrals.append((al, ak, aj, ai))
        return dihedrals

    def _get_atom_improper_dihedrals(
        self, atom_nr: str, ff
    ) -> list[tuple[tuple[str, str, str, str], ResidueImproperSpec]]:
        # TODO: cleanup and make more efficient
        # which improper dihedrals are used is defined for each residue
        # in aminoacids.rtp
        # get improper diheldrals from FF based on residue
        # TODO: handle impropers defined for the residue that
        # belongs to an adjacent atom, not just the the specied one
        atom = self.atoms[atom_nr]
        residue = ff.residuetypes.get(atom.residue)
        if residue is None:
            return []

        # https://manual.gromacs.org/current/reference-manual/functions/bonded-interactions.html#improper-dihedrals
        # atom in a line, like a regular dihedral:
        dihedrals = []
        dihedral_candidate_keys = self._get_margin_atom_dihedrals(
            atom_nr
        ) + self._get_center_atom_dihedrals(atom_nr)

        # atom in the center of a triangle:
        ai = atom_nr
        partners = self.atoms[ai].bound_to_nrs
        if len(partners) >= 3:
            combs = permutations(partners, 3)
            for comb in combs:
                aj, ak, al = comb
                # center atom is at position 3
                dihedral_candidate_keys.append((aj, ak, ai, al))

        # atom in corner of a triangle:
        for a in self.atoms[atom_nr].bound_to_nrs:
            partners = self.atoms[a].bound_to_nrs
            if len(partners) >= 3:
                combs = permutations(partners + [a], 4)
                for comb in combs:
                    ai, aj, ak, al = comb
                    dihedral_candidate_keys.append((ai, aj, ak, al))

        # residues on aminoacids.rtp specify a dihedral to the next or previous
        # AA with -C and +N as the atomname
        for candidate in dihedral_candidate_keys:
            candidate_key = [self.atoms[atom_nr].atom for atom_nr in candidate]
            for i, nr in enumerate(candidate):
                if self.atoms[nr].resnr != atom.resnr:
                    if candidate_key[i] == "C":
                        candidate_key[i] = "-C"
                    elif candidate_key[i] == "N":
                        candidate_key[i] = "+N"

            dihedral = residue.improper_dihedrals.get(tuple(candidate_key))
            if dihedral:
                dihedrals.append((candidate, dihedral))

        return dihedrals

    def _regenerate_topology_from_bound_to(self, ff):
        # clear all bonds, angles, dihedrals
        self.bonds.clear()
        self.angles.clear()
        self.proper_dihedrals.clear()
        self.proper_dihedrals.clear()
        self.improper_dihedrals.clear()

        # bonds
        keys = []
        for atom in self.atoms.values():
            keys = self._get_atom_bonds(atom.nr)
            for key in keys:
                self.bonds[key] = Bond(key[0], key[1], "1")

        # angles
        for atom in self.atoms.values():
            keys = self._get_atom_angles(atom.nr)
            for key in keys:
                self.angles[key] = Angle(key[0], key[1], key[2], "1")

        # dihedrals and pass
        for atom in self.atoms.values():
            keys = self._get_atom_proper_dihedrals(atom.nr)
            for key in keys:
                self.proper_dihedrals[key] = MultipleDihedrals(
                    *key, "9", dihedrals={"": Dihedral(*key, "9")}
                )
                pairkey: tuple[str, str] = tuple(str(x) for x in sorted([key[0], key[3]], key=int))  # type: ignore
                if self.pairs.get(pairkey) is None:
                    self.pairs[pairkey] = Pair(pairkey[0], pairkey[1], "1")

        for atom in self.atoms.values():
            impropers = self._get_atom_improper_dihedrals(atom.nr, ff)
            for key, improper in impropers:
                periodicity = ""
                if improper.c2 is not None:
                    periodicity = improper.c2
                self.improper_dihedrals[key] = MultipleDihedrals(
                    *key,
                    "4",
                    dihedrals={
                        "": Dihedral(
                            *key,
                            "4",
                            c0=improper.c0,
                            c1=improper.c1,
                            periodicity=periodicity,
                        )
                    },
                )

    def reindex_atomnrs(self) -> dict[str, str]:
        """Reindex atom numbers in topology.

        Starts at index 1.
        This also updates the numbers for bonds, angles, dihedrals and pairs.
        Returns a dict, mapping of old atom number strings to new ones
        """

        update_map = {
            atom_nr: str(i + 1)
            for i, atom_nr in enumerate(sorted(list(self.atoms.keys()), key=int))
        }

        new_atoms = {}
        for atom in self.atoms.values():
            atom.nr = update_map[atom.nr]
            bound_to = []
            for nr in atom.bound_to_nrs:
                new_nr = update_map.get(nr)
                if new_nr is not None:
                    bound_to.append(new_nr)
            atom.bound_to_nrs = bound_to
            new_atoms[atom.nr] = atom
        self.atoms = new_atoms

        new_bonds = {}
        for bond in self.bonds.values():
            ai = update_map.get(bond.ai)
            aj = update_map.get(bond.aj)
            # drop bonds to a deleted atom
            if ai is None or aj is None:
                continue
            bond.ai = ai
            bond.aj = aj
            new_bonds[(ai, aj)] = bond
        self.bonds = new_bonds

        new_angles = {}
        for angle in self.angles.values():
            ai = update_map.get(angle.ai)
            aj = update_map.get(angle.aj)
            ak = update_map.get(angle.ak)
            # drop angles to a deleted atom
            if None in (ai, aj, ak):
                continue
            # pyright does not grok `None in ...`
            angle.ai = ai  # type: ignore
            angle.aj = aj  # type: ignore
            angle.ak = ak  # type: ignore
            new_angles[(angle.ai, angle.aj, angle.ak)] = angle
        self.angles = new_angles

        new_pairs = {}
        new_multiple_dihedrals = {}
        for dihedrals in self.proper_dihedrals.values():
            new_dihedrals = {}
            ai = update_map.get(dihedrals.ai)
            aj = update_map.get(dihedrals.aj)
            ak = update_map.get(dihedrals.ak)
            al = update_map.get(dihedrals.al)
            # drop dihedrals to a deleted atom
            if None in (ai, aj, ak, al):
                continue

            # do pairs before the dihedrals are updated
            pair = self.pairs.pop((dihedrals.ai, dihedrals.al), None)
            if pair is not None:
                pair_ai = update_map.get(pair.ai)
                pair_aj = update_map.get(pair.aj)

                if None not in (pair_ai, pair_aj):
                    pair.ai = pair_ai  # type: ignore (pyright bug)
                    pair.aj = pair_aj  # type: ignore
                    new_pairs[(pair_ai, pair_aj)] = pair

            dihedrals.ai = ai  # type: ignore
            dihedrals.aj = aj  # type: ignore
            dihedrals.ak = ak  # type: ignore
            dihedrals.al = al  # type: ignore

            for dihedral in dihedrals.dihedrals.values():
                dihedral.ai = ai  # type: ignore
                dihedral.aj = aj  # type: ignore
                dihedral.ak = ak  # type: ignore
                dihedral.al = al  # type: ignore
                new_dihedrals[dihedral.periodicity] = dihedral
            dihedrals.dihedrals = new_dihedrals

            new_multiple_dihedrals[
                (
                    dihedrals.ai,
                    dihedrals.aj,
                    dihedrals.ak,
                    dihedrals.al,
                )
            ] = dihedrals

        self.proper_dihedrals = new_multiple_dihedrals
        self.pairs = new_pairs

        new_multiple_dihedrals = {}
        for dihedrals in self.improper_dihedrals.values():
            new_dihedrals = {}
            ai = update_map.get(dihedrals.ai)
            aj = update_map.get(dihedrals.aj)
            ak = update_map.get(dihedrals.ak)
            al = update_map.get(dihedrals.al)
            # drop dihedrals to a deleted atom
            if None in (ai, aj, ak, al):
                continue
            dihedrals.ai = ai  # type: ignore
            dihedrals.aj = aj  # type: ignore
            dihedrals.ak = ak  # type: ignore
            dihedrals.al = al  # type: ignore

            for dihedral in dihedrals.dihedrals.values():
                dihedral.ai = ai  # type: ignore
                dihedral.aj = aj  # type: ignore
                dihedral.ak = ak  # type: ignore
                dihedral.al = al  # type: ignore
                new_dihedrals[dihedral.periodicity] = dihedral
            dihedrals.dihedrals = new_dihedrals

            new_multiple_dihedrals[(ai, aj, ak, al)] = dihedrals
        self.improper_dihedrals = new_multiple_dihedrals

        return update_map


class Topology:
    """Smart container for parsed topology data.

    A topology keeps track of connections when bonds are broken or formed.
    Reparametrization is triggerd automatically if `to_dict` is called
    after bonds have changed.

    Assumptions:

    - the topology of interest (the Reactive moleculetype) consists of the first moleculetypes (non-solvent).

    Parameters
    ----------
    top
        A dictionary containing the parsed topology data, produced by
        [](`kimmdy.parsing.read_top`)
    parametrizer
        The parametrizer to use when reparametrizing the topology.
    is_reactive_predicate_f
        A function that takes a moleculetype name and returns True if the moleculetype
        should be merged into the reactive moleculetype.
    radicals
        A string of atom numbers that are radicals.
    residuetypes_path
        Path to the residue types file.
    reactive_nrexcl
        Overwrite nrexcl value for the reactive moleculetype. Otherwise takes the nrexcl of the first reactive moleculetype.
    """

    def __init__(
        self,
        top: TopologyDict,
        parametrizer: Parameterizer = BasicParameterizer(),
        is_reactive_predicate_f: Callable[[str], bool] = is_not_solvent_or_ion,
        radicals: Optional[str] = None,
        residuetypes_path: Optional[Path] = None,
        reactive_nrexcl: Optional[str] = None,
    ) -> None:
        if top == {}:
            raise NotImplementedError(
                "Generating an empty Topology from an empty TopologyDict is not implemented."
            )

        self.top = top
        self.moleculetypes: dict[str, MoleculeType] = {}
        molecules = get_top_section(top, "molecules")
        if molecules is None:
            raise ValueError("molecules not found in top file")
        self.molecules = [(l[0], l[1]) for l in molecules]

        self.ff = FF(top, residuetypes_path)
        self._parse_molecules()
        self.parametrizer = parametrizer
        self._check_is_reactive_molecule = is_reactive_predicate_f

        self.needs_parameterization = False

        self._merge_moleculetypes(radicals=radicals, nrexcl=reactive_nrexcl)
        self._link_atomics()

    def _link_atomics(self):
        """
        link atoms, bonds etc. to the main moleculeype, REACTIVE_MOLECULEYPE
        """
        self.reactive_molecule = self.moleculetypes[REACTIVE_MOLECULEYPE]
        self.atoms = self.reactive_molecule.atoms
        self.bonds = self.reactive_molecule.bonds
        self.angles = self.reactive_molecule.angles
        self.exclusions = self.reactive_molecule.exclusions
        self.proper_dihedrals = self.reactive_molecule.proper_dihedrals
        self.improper_dihedrals = self.moleculetypes[
            REACTIVE_MOLECULEYPE
        ].improper_dihedrals
        self.pairs = self.reactive_molecule.pairs
        self.position_restraints = self.moleculetypes[
            REACTIVE_MOLECULEYPE
        ].position_restraints
        self.dihedral_restraints = self.moleculetypes[
            REACTIVE_MOLECULEYPE
        ].dihedral_restraints
        self.radicals = self.reactive_molecule.radicals
        self.nrexcl = self.reactive_molecule.nrexcl

    def _extract_mergable_molecules(self) -> dict[str, int]:
        """Extract all molecules that are to be merged into one moleculetype.
        And replaces them with a single moleculetype with the name REACTIVE_MOLECULEYPE
        """
        reactive_molecules = {}
        new_molecules = []
        started_merging = False
        stopped_merging = False
        added_reactive_molecule = False
        for m, n in self.molecules:
            if self._check_is_reactive_molecule(m):
                if stopped_merging:
                    m = f"""Attempting to merge a moleculetype {m} interspersed with non-merging moleculetypes.
            Please make sure that all moleculetypes to be merged (all non-solvent molecules or ions by default)
            are listed consecutively in the [molecules] section of the topology and in the coordinates file (.gro)
            """
                    logger.error(m)
                    raise ValueError(m)
                started_merging = True
                reactive_molecules[m] = int(n)
                if not added_reactive_molecule:
                    new_molecules += [(REACTIVE_MOLECULEYPE, "1")]
                    added_reactive_molecule = True
            else:
                if started_merging:
                    stopped_merging = True
                new_molecules += [(m, n)]

        self.molecules = new_molecules
        logger.info(
            "Merging the following molecules into the Reactive moleculetype and making their multiples explicit:"
        )
        for m, n in reactive_molecules.items():
            logger.info(f"\t{m} {n}")
        return reactive_molecules

    def _merge_moleculetypes(
        self, radicals: Optional[str] = None, nrexcl: Optional[str] = None
    ):
        """
        Merge all moleculetypes within which reactions can happen into one moleculetype.
        This also makes multiples explicit.
        Reactive molecules have to be in a continuous block in the [molecules] section of the topology
        and the gro file, preferably the start.
        Atom ids (gromacs, 1-based) are updated accordingly and thus correspond directly
        to the atom numbers in the coordinates file (.gro) (ignoring gromacs overflow
        problems in the atomnr column, the correct internal atom nr is always "gro file line number" - 2).
        """
        molecules = self._extract_mergable_molecules()
        first_reactive_molecule = list(molecules.keys())[0]
        if nrexcl is None:
            nrexcl = self.moleculetypes[first_reactive_molecule].nrexcl
        reactive_atomics = {}
        atomnr_offset = 0
        resnr_offset = 0
        for name, n in molecules.items():
            add_atomics = self.moleculetypes[name].atomics
            n_atoms = len(add_atomics["atoms"])
            highest_resnr = int(add_atomics["atoms"][-1][RESNR_ID_FIELDS["atoms"][0]])
            for _ in range(n):
                for section_name, section in add_atomics.items():
                    section = deepcopy(section)
                    atomnr_fields = ATOM_ID_FIELDS.get(section_name, [])
                    resnr_fields = RESNR_ID_FIELDS.get(section_name, [])
                    for line in section:
                        for field in atomnr_fields:
                            increment_field(line, field, atomnr_offset)
                        for field in resnr_fields:
                            increment_field(line, field, resnr_offset)
                    if reactive_atomics.get(section_name) is None:
                        reactive_atomics[section_name] = []
                    reactive_atomics[section_name] += section

                atomnr_offset += n_atoms
                resnr_offset += highest_resnr
            # remove now merged moleculetype from topology
            # and the topology dict
            del self.moleculetypes[name]
            del self.top[f"moleculetype_{name}"]

        # add merged moleculetype to topology
        reactive_moleculetype = MoleculeType(
            MoleculeTypeHeader(name=REACTIVE_MOLECULEYPE, nrexcl=nrexcl),
            reactive_atomics,
            radicals,
        )
        self.moleculetypes[REACTIVE_MOLECULEYPE] = reactive_moleculetype
        # update topology dict
        self._update_dict()

    def validate_bond(self, atm1: Atom, atm2: Atom) -> bool:
        """Validates bond consistency between both atoms and top
        Returns True if bond exists, False if not.
        Raises RuntimeError if bond is not consistent.
        """

        counter = 0
        if (atm1.nr, atm2.nr) in self.bonds.keys():
            counter += 1
        if atm1.nr in atm2.bound_to_nrs:
            counter += 1
        if atm2.nr in atm1.bound_to_nrs:
            counter += 1
        if counter == 3:
            return True
        elif counter == 0:
            return False
        raise RuntimeError(
            f"Bond between atom {atm1.nr} {atm1.type} and {atm2.nr} {atm2.type} "
            "(1-based) is ill-defined!\n"
            f"\tatm1.bound_to_nrs {atm1.bound_to_nrs}\n"
            f"\tatm2.bound_to_nrs {atm2.bound_to_nrs}\n"
        )

    def _parse_molecules(self):
        moleculetypes = [k for k in self.top.keys() if k.startswith("moleculetype")]
        for moleculetype in moleculetypes:
            header = get_moleculetype_header(self.top, moleculetype)
            if header is None:
                logger.warning(f"moleculetype {moleculetype} has no header. Skipping.")
                continue
            atomics = get_moleculetype_atomics(self.top, moleculetype)
            if atomics is None:
                logger.warning(
                    f"moleculetype {moleculetype} has no atoms, bonds, angles etc. Skipping."
                )
                continue
            name = header.name
            self.moleculetypes[name] = MoleculeType(header, atomics, radicals="")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Topology):
            return False
        return self.to_dict() == other.to_dict()

    def _update_dict_from_moleculetype_atomics(
        self, moleculetype: MoleculeType, create: bool = False
    ):
        """Set content of the atomics (atoms/bonds/angles etc.) in the a topology dict from of a moleculetype.

        Resolves any `#ifdef` statements by check in the top['define'] dict
        and chooses the 'content' or 'else_content' depending on the result.

        If create is True, a new section is created if it does not exist.
        """
        name = moleculetype.name
        atomics = moleculetype.atomics
        top = self.top
        moleculetype_name = f"moleculetype_{name}"
        section = top.get(moleculetype_name)
        if section is None:
            if create:
                logger.info(
                    f"topology does not contain {moleculetype_name}. Creating new section."
                )
                section = empty_section()
                section["content"] = [[name, moleculetype.nrexcl]]
                section["subsections"] = {k: empty_section() for k in atomics.keys()}
                top[moleculetype_name] = section
            else:
                logger.warning(
                    f"topology does not contain {moleculetype_name} and create=False. Not creating new section."
                )
                return None

        subsections = section["subsections"]
        for k in list(subsections.keys()):
            # # if atomics section is empty,
            # # remove the subsection from the topology
            # if atomics[k] == []:
            #     logger.debug(f"Removing subsection {k} from {moleculetype_name}")
            #     del subsections[k]
            #     continue

            v = subsections[k]
            condition = v.get("condition")
            if condition is not None:
                condition_type = condition.get("type")
                condition_value = condition.get("value")
                if condition_type == "ifdef":
                    if condition_value in top["define"].keys():
                        v["content"] = atomics[k]
                    else:
                        v["else_content"] = atomics[k]
                elif condition_type == "ifndef":
                    if condition_value not in top["define"].keys():
                        v["content"] = atomics[k]
                    else:
                        v["else_content"] = atomics[k]
                else:
                    raise NotImplementedError(
                        f"condition type {condition_type} is not supported"
                    )
            else:
                v["content"] = atomics[k]

    def _update_dict(self):
        """Update the topology dictionary with the current state of the topology."""
        for name, moleculetype in self.moleculetypes.items():
            moleculetype._update_atomics_dict()
            if name == REACTIVE_MOLECULEYPE:
                self._update_dict_from_moleculetype_atomics(moleculetype, create=True)
            else:
                self._update_dict_from_moleculetype_atomics(moleculetype)

        set_top_section(
            self.top,
            "molecules",
            [[name, n] for name, n in self.molecules],
        )

        set_top_section(
            self.top,
            "atomtypes",
            [attributes_to_list(x) for x in self.ff.atomtypes.values()],
        )

        set_top_section(
            self.top,
            "bondtypes",
            [attributes_to_list(x) for x in self.ff.bondtypes.values()],
        )
        if self.ff.nonbond_params != {}:
            set_top_section(
                self.top,
                "nonbond_params",
                [attributes_to_list(x) for x in self.ff.nonbond_params.values()],
            )
        set_top_section(
            self.top,
            "angletypes",
            [attributes_to_list(x) for x in self.ff.angletypes.values()],
        )

    def update_parameters(self, focus_nrs: Optional[set[str]] = None):
        if self.needs_parameterization:
            if self.parametrizer is not None:
                logger.info(
                    f"Starting parametrization using {self.parametrizer.__class__.__name__}"
                )
                self.parametrizer.parameterize_topology(self, focus_nrs)
            else:
                raise RuntimeError("No Parametrizer was initialized in this topology!")
            self.needs_parameterization = False

    def update_partial_charges(self, recipe_steps: list[RecipeStep]) -> None:
        """Update the topology atom partial charges.

        This function must be called after the recipe_steps are applied.
        Changes are based on the recipe_steps. Update rules follow a simple
        assignment scheme that works well with grappa. If fragments are created,
        their partial charges are kept integers. If previously broken bonds are
        formed again, the original partial charges are restored.
        """

        # build updated list of atoms by residue for topology so that this does
        # not need to be repeated
        # Warning: resnr not unique for each residue, 'residues' can map to
        # more than one real residue
        residues = {}
        for atom in self.atoms.values():
            if residues.get(atom.resnr) is None:
                residues[atom.resnr] = []
            residues[atom.resnr].append(atom)

        for step in recipe_steps:
            if isinstance(step, Break):
                # make partial charges on either side of the break integer
                if self.atoms[step.atom_id_1].resnr == self.atoms[step.atom_id_2].resnr:
                    atom1 = self.atoms[step.atom_id_1]
                    atom2 = self.atoms[step.atom_id_2]
                    fragment1, fragment2 = get_residue_fragments(
                        self, residues[atom1.resnr], atom1, atom2
                    )
                    if len(fragment2) in (0, 1):
                        # intra-residue HAT case (or similar)
                        if atom1.type.upper().startswith("H"):
                            atom2.charge = (
                                f"{float(atom2.charge) + float(atom1.charge):7.4f}"
                            )
                            atom1.charge = "0.0"
                        elif atom2.type.upper().startswith("H"):
                            atom1.charge = (
                                f"{float(atom1.charge) + float(atom2.charge):7.4f}"
                            )
                            atom2.charge = "0.0"
                        else:
                            logger.warning(
                                "Residue is not fragmented and no break atom is hydrogen. Doing nothing!"
                            )
                            continue
                    else:
                        # homolysis case (or similar)

                        charge_fragment1 = [
                            float(self.atoms[nr].charge) for nr in fragment1
                        ]
                        charge_fragment2 = [
                            float(self.atoms[nr].charge) for nr in fragment2
                        ]
                        diff1 = sum(charge_fragment1) - round(sum(charge_fragment1))
                        diff2 = sum(charge_fragment2) - round(sum(charge_fragment2))
                        atom1.charge = f"{float(atom1.charge) - diff1:7.4f}"
                        atom2.charge = f"{float(atom2.charge) - diff2:7.4f}"
                else:
                    # no change in partial charges necessary for break between residues
                    continue
            elif isinstance(step, Bind):
                if self.atoms[step.atom_id_1].resnr == self.atoms[step.atom_id_2].resnr:
                    atom1 = self.atoms[step.atom_id_1]
                    atom2 = self.atoms[step.atom_id_2]
                    # check whether bond exists in topology
                    residue = atom1.residue
                    bondtype = (atom1.atom, atom2.atom)
                    residue_bond_spec = self.ff.residuetypes[residue].bonds.get(
                        bondtype,
                        self.ff.residuetypes[residue].bonds.get(
                            (bondtype[-1], bondtype[-2])
                        ),
                    )
                    if residue_bond_spec:
                        atom1.charge = (
                            self.ff.residuetypes[residue].atoms[atom1.atom].charge
                        )
                        atom2.charge = (
                            self.ff.residuetypes[residue].atoms[atom2.atom].charge
                        )
                    else:
                        logger.warning(
                            f"New bond defined in {step} but can't be found in residuetypes definition. Not changing the partial charges!"
                        )
                else:
                    # no change in partial charges necessary
                    continue
            else:
                continue

    def to_dict(self) -> TopologyDict:
        self.update_parameters()
        self._update_dict()
        return self.top

    def del_atom(
        self, atom_nr: Union[list[str], str], parameterize: bool = True
    ) -> dict[str, str]:
        """Deletes atom

        Deletes atom and all attached bonds. Reindexes the top and updates the
        parameters if requested. Also moves charges to first bound_nrs atom.

        Parameters
        ----------
        atom_nr
            1-based atom number as string to delete
        parameterize
            If true and bonds are removed triggers reparameterization,
            by default True

        Returns
        -------
        update_map
            Dict, mapping of old atom number strings to new ones.
        """
        if not isinstance(atom_nr, list):
            atom_nr = [atom_nr]
        for _atom_nr in atom_nr:
            atom = self.atoms[_atom_nr]
            logger.debug(
                f"Deleting Atom nr {atom.nr}, type {atom.type}, res {atom.residue}"
            )
            # move charge to first neighbor
            self.atoms[atom.bound_to_nrs[0]].charge = (
                f"{float(self.atoms[atom.bound_to_nrs[0]].charge) + float(atom.charge):7.4f}"
            )

            # break all bonds and delete all pairs, diheadrals with these bonds
            for bound_nr in copy(atom.bound_to_nrs):
                self.break_bond((bound_nr, _atom_nr))

            for an in tuple(self.angles.keys()):
                if _atom_nr in an:
                    self.angles.pop(an)

            for pd in tuple(self.proper_dihedrals.keys()):
                if _atom_nr in pd:
                    self.proper_dihedrals.pop(pd)

            for id in tuple(self.improper_dihedrals.keys()):
                if _atom_nr in id:
                    self.improper_dihedrals.pop(id)

            self.radicals.pop(_atom_nr)
            self.atoms.pop(_atom_nr)

        update_map_all = self.reindex_atomnrs()
        update_map = update_map_all[REACTIVE_MOLECULEYPE]

        if parameterize:
            self.update_parameters()
        # Overwriting in case of no parameterization wanted
        self.needs_parameterization = False

        return update_map

    def reindex_atomnrs(self) -> dict[str, dict[str, str]]:
        """Reindex atom numbers in topology.

        Starts at index 1.
        This also updates the numbers for bonds, angles, dihedrals and pairs.
        Returns a dict of all moleculetypes to their update maps (old -> new).
        """
        update_map_all = {}
        for id, moleculetype in self.moleculetypes.items():
            update_map_all[id] = moleculetype.reindex_atomnrs()
        self._link_atomics()  # necessary because reindex_atomnrs creates a new dict
        return update_map_all

    def _regenerate_topology_from_bound_to(self):
        """Regenerate the topology from the bound_to lists of the atoms."""
        for moleculetype in self.moleculetypes.values():
            moleculetype._regenerate_topology_from_bound_to(self.ff)

    def __str__(self) -> str:
        molecules = "\n".join([name + ": " + n for name, n in self.molecules])
        reactive_moleculetype = self.moleculetypes.get(REACTIVE_MOLECULEYPE)
        text = (
            textwrap.dedent(f"Topology with the following molecules: \n{molecules}\n\n")
            + textwrap.dedent(f"With {reactive_moleculetype}")
            + textwrap.dedent(f"{self.ff}")
        )
        return text

    def __repr__(self) -> str:
        self._update_dict()
        return f"Topology({self.top})"

    def _repr_pretty_(self, p, _):
        """A __repr__ for ipython.

        This whill be used if just the name of the object is entered in the ipython shell
        or a jupyter notebook.

        p is an instance of
        [IPython.lib.pretty.PrettyPrinter](https://ipython.org/ipython-doc/3/api/generated/IPython.lib.pretty.html)
        """
        p.text(str(self))

    def get_neighbors(self, initial_atoms: set[str], order: int):
        explored_atoms = initial_atoms
        frontier = initial_atoms
        for _ in range(order):
            new_frontier = set()
            for atom in frontier:
                new_frontier.update(self.atoms[atom].bound_to_nrs)
            frontier = new_frontier - explored_atoms
            explored_atoms |= new_frontier
        return explored_atoms

    def break_bond(
        self, atompair_addresses: tuple[TopologyAtomAddress, TopologyAtomAddress]
    ):
        """Break bonds in topology homolytically.

        Removes bond, angles and dihedrals where atompair was involved.
        Modifies the topology dictionary in place.
        Atom pairs become radicals.

        Parameters
        ----------
        atompair_addresses
            Between which atoms to break the bond.
        """
        logger.debug(f"Breaking bond between {atompair_addresses}")
        reactive_moleculetype = self.reactive_molecule

        # tuple -> list -> sorted -> tuple still makes it a tuple of two strings
        # so pyright can chill.
        atompair_nrs: tuple[str, str] = tuple(sorted(atompair_addresses, key=int))  # type: ignore

        atompair = [
            reactive_moleculetype.atoms[atompair_nrs[0]],
            reactive_moleculetype.atoms[atompair_nrs[1]],
        ]

        if not self.validate_bond(*atompair):
            raise ValueError(
                "Trying to break non-existing bond!"
                f"\n\tatom 1, nr {atompair[0].nr}, type {atompair[0].type}, res {atompair[0].residue}"
                f"\n\tatom 2, nr {atompair[1].nr}, type {atompair[1].type}, res {atompair[1].residue}"
            )

        self.needs_parameterization = True

        # mark atoms as radicals
        for atom in atompair:
            atom.is_radical = True
            reactive_moleculetype.radicals[atom.nr] = atom

        # bonds
        # remove bond
        removed_bond = reactive_moleculetype.bonds.pop(atompair_nrs)
        logging.debug(f"removed bond: {removed_bond}")

        # remove angles
        angle_keys = reactive_moleculetype._get_center_atom_angles(
            atompair_nrs[0]
        ) + reactive_moleculetype._get_center_atom_angles(atompair_nrs[1])
        for key in angle_keys:
            if all([x in key for x in atompair_nrs]):
                # angle contained a now deleted bond because
                # it had both atoms of the broken bond
                reactive_moleculetype.angles.pop(key, None)

        # remove proper dihedrals
        # and pairs
        dihedral_keys = reactive_moleculetype._get_atom_proper_dihedrals(
            atompair_nrs[0]
        ) + reactive_moleculetype._get_atom_proper_dihedrals(atompair_nrs[1])
        for key in dihedral_keys:
            # don't use periodicity in key for checking atompair_nrs
            if all([x in key for x in atompair_nrs]):
                # dihedral contained a now deleted bond because
                # it had both atoms of the broken bond
                reactive_moleculetype.proper_dihedrals.pop(key, None)
                pairkey: tuple[str, str] = tuple(sorted((key[0], key[3]), key=int))  # type: ignore
                reactive_moleculetype.pairs.pop(pairkey, None)

        # and improper dihedrals
        keys_remove = []
        for key in reactive_moleculetype.improper_dihedrals:
            if all([x in key for x in atompair_nrs]):
                keys_remove.append(key)
        for key in keys_remove:
            reactive_moleculetype.improper_dihedrals.pop(key, None)

        # warn if atom is part of restraint
        for atom in atompair:
            posres = atom.nr in self.position_restraints.keys()
            dihres = any([atom.nr in atms for atms in self.position_restraints.keys()])
            if posres or dihres:
                logging.warning(
                    "Deleting bond with atom involved in a restraint!"
                    "This may causes unwanted behaviour.\n"
                    f"\n\tatom 1, nr {atompair[0].nr}, type {atompair[0].type}, res {atompair[0].residue}"
                    f"\n\tatom 2, nr {atompair[1].nr}, type {atompair[1].type}, res {atompair[1].residue}"
                )

        # update bound_to
        atompair[0].bound_to_nrs.remove(atompair[1].nr)
        atompair[1].bound_to_nrs.remove(atompair[0].nr)

    def bind_bond(
        self, atompair_addresses: tuple[TopologyAtomAddress, TopologyAtomAddress]
    ):
        """Add a bond in topology.

        Modifies the topology dictionary in place.
        It keeps track of affected terms in the topology via a graph representation of the topology
        and applies the necessary changes to bonds, angles and dihedrals (proper and improper).
        Furthermore, it modifies to function types in the topology to account for radicals.

        Parameters
        ----------
        atompair_addresses
            Atoms to bind together.
        """
        logger.debug(f"Creating bond between {atompair_addresses}")

        reactive_moleculetype = self.reactive_molecule

        atompair_nrs: tuple[str, str] = tuple(sorted(atompair_addresses, key=int))  # type: ignore
        atompair = [
            reactive_moleculetype.atoms[atompair_nrs[0]],
            reactive_moleculetype.atoms[atompair_nrs[1]],
        ]

        # check whether they are bound already
        if self.validate_bond(*atompair):
            raise ValueError(
                "Trying to bind to atoms already bound!"
                f"\n\tatom 1, nr {atompair[0].nr}, type {atompair[0].type}, res {atompair[0].residue}"
                f"\n\tatom 2, nr {atompair[1].nr}, type {atompair[1].type}, res {atompair[1].residue}"
            )

        self.needs_parameterization = True

        # de-radialize if re-combining two radicals
        if all(map(lambda x: x.is_radical, atompair)):
            atompair[0].is_radical = False
            atompair[1].is_radical = False
            for a in atompair:
                if reactive_moleculetype.radicals.pop(a.nr) is None:
                    logging.warning(
                        f"Atom {a} should have been a radical but was "
                        "not registed in moleculetype.radicals"
                    )

        # quickfix for jumping hydrogens
        # to make them adopt the correct residuetype and atomtype
        # when bound to a new heavy atom
        for i, atom in enumerate(atompair):
            if atom.type.upper().startswith("H"):
                other_i = abs(i - 1)
                other_atom = atompair[other_i]
                other_res = other_atom.residue
                atom.residue = other_res
                atom.resnr = other_atom.resnr
                aa = self.ff.residuetypes.get(other_res)
                if not aa:
                    logging.warning(
                        f"No AA found for {other_res}, to which a H would jump"
                    )
                    continue

                residue_atomnames_current = [
                    a.atom
                    for a in reactive_moleculetype.atoms.values()
                    if a.resnr == other_atom.resnr
                ]
                name_set = False
                for key, bond in aa.bonds.items():
                    if other_atom.atom in key and any(
                        k.upper().startswith("H") for k in key
                    ):
                        if key[0] == other_atom.atom:
                            h_name = bond.atom2
                        else:
                            h_name = bond.atom1

                        atom.type = aa.atoms[h_name].type
                        if (
                            not h_name in residue_atomnames_current
                            or h_name == atom.atom
                        ):
                            atom.atom = h_name
                            name_set = True
                        else:
                            atom.atom = "HX"
                            name_set = True
                            continue
                        logger.info(f"Hydrogen will be bound to {other_atom}.")
                        break
                else:
                    if name_set:
                        logger.warning(
                            "Found new atomtype but not atomname for HAT hydrogen!"
                        )
                    else:
                        logger.warning(
                            "Found neither new atomtype nor atomname for HAT hydrogen!"
                        )
                if not name_set:
                    atom.atom = "HX"
                    logger.warning(f"Named newly bonded hydrogen 'HX'")

        # update bound_to
        atompair[0].bound_to_nrs.append(atompair[1].nr)
        atompair[1].bound_to_nrs.append(atompair[0].nr)

        # bonds
        # add bond
        bond = Bond(atompair_nrs[0], atompair_nrs[1], "1")
        reactive_moleculetype.bonds[atompair_nrs] = bond
        logging.info(f"added bond: {bond}")

        # add angles
        angle_keys = reactive_moleculetype._get_atom_angles(
            atompair_nrs[0]
        ) + reactive_moleculetype._get_atom_angles(atompair_nrs[1])
        for key in angle_keys:
            if reactive_moleculetype.angles.get(key) is None:
                reactive_moleculetype.angles[key] = Angle(key[0], key[1], key[2], "1")

        # add proper and improper dihedrals
        # add proper dihedrals and pairs
        # TODO; for now this assumes function type 9 for all dihedrals
        # and adds all possible dihedrals (and pairs)
        # later we should check the ff if there are multiple
        # dihedrals for the same atoms with different periodicities.
        dihedral_keys = reactive_moleculetype._get_atom_proper_dihedrals(
            atompair_nrs[0]
        ) + reactive_moleculetype._get_atom_proper_dihedrals(atompair_nrs[1])
        for key in dihedral_keys:
            if reactive_moleculetype.proper_dihedrals.get(key) is None:
                reactive_moleculetype.proper_dihedrals[key] = MultipleDihedrals(
                    *key, "9", dihedrals={"": Dihedral(*key, "9")}
                )
            pairkey: tuple[str, str] = tuple(str(x) for x in sorted([key[0], key[3]], key=int))  # type: ignore
            if reactive_moleculetype.pairs.get(pairkey) is None:
                reactive_moleculetype.pairs[pairkey] = Pair(pairkey[0], pairkey[1], "1")

        # improper dihedral
        dihedral_k_v = reactive_moleculetype._get_atom_improper_dihedrals(
            atompair_nrs[0], self.ff
        ) + reactive_moleculetype._get_atom_improper_dihedrals(atompair_nrs[1], self.ff)
        for key, value in dihedral_k_v:
            if value.c2 is None:
                value.c2 = ""
            if reactive_moleculetype.improper_dihedrals.get(key) is None:
                reactive_moleculetype.improper_dihedrals[key] = MultipleDihedrals(
                    *key, "4", dihedrals={}
                )
            reactive_moleculetype.improper_dihedrals[key].dihedrals[value.c2] = (
                Dihedral(*key, "4", c0=value.c0, c1=value.c1, periodicity=value.c2)
            )
