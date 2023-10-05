from kimmdy.constants import ATOMTYPE_BONDORDER_FLAT
from kimmdy.parsing import TopologyDict
from kimmdy.topology.atomic import *
from kimmdy.topology.utils import (
    get_moleculetype_atomics,
    get_moleculetype_header,
    attributes_to_list,
    get_top_section,
    set_moleculetype_atomics,
    set_top_section,
)
from kimmdy.topology.ff import FF
from kimmdy.plugins import BasicParameterizer, Parameterizer
from itertools import permutations, combinations
import textwrap
import logging
from copy import copy

from kimmdy.utils import TopologyAtomAddress

logger = logging.getLogger("kimmdy")


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

    def __init__(self, header: tuple[str, str], atomics: dict) -> None:
        self.name, self.nrexcl = header
        self.atomics = atomics

        logger.info(f"parsing molecule {self.name}")

        self.atoms: dict[str, Atom] = {}
        self.bonds: dict[tuple[str, str], Bond] = {}
        self.pairs: dict[tuple[str, str], Pair] = {}
        self.angles: dict[tuple[str, str, str], Angle] = {}
        self.proper_dihedrals: dict[tuple[str, str, str, str], MultipleDihedrals] = {}
        self.improper_dihedrals: dict[tuple[str, str, str, str], Dihedral] = {}
        self.position_restraints: dict[str, PositionRestraint] = {}
        self.dihedral_restraints: dict[
            tuple[str, str, str, str], DihedralRestraint
        ] = {}
        # TODO: self.settles = {}
        self.radicals: dict[str, Atom] = {}

        self._parse_atoms()
        self._parse_bonds()
        self._parse_pairs()
        self._parse_angles()
        self._parse_dihedrals()
        self._parse_restraints()
        self._initialize_graph()
        self.test_for_radicals()

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
                self.improper_dihedrals[key] = dihedral
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
            return
        condition = None
        for l in ls:
            restraint = PositionRestraint.from_top_line(l, condition=condition)
            self.position_restraints[restraint.ai] = restraint
        ls = self.atomics.get("dihedral_restraints")
        if not ls:
            return
        for l in ls:
            restraint = DihedralRestraint.from_top_line(l)
            self.dihedral_restraints[
                (restraint.ai, restraint.aj, restraint.ak, restraint.al)
            ] = restraint

    def _initialize_graph(self):
        """Add a list of atom nrs bound to an atom to each atom."""
        for bond in self.bonds.values():
            i = bond.ai
            j = bond.aj
            self.atoms[i].bound_to_nrs.append(j)
            self.atoms[j].bound_to_nrs.append(i)

    def test_for_radicals(self):
        """Updates radical status per atom and in topology.

        Iterate over all atoms and designate them as radicals if they have
        fewer bounds than their natural bond order.
        """
        for atom in self.atoms.values():
            bo = ATOMTYPE_BONDORDER_FLAT.get(atom.type)
            if bo and bo > len(atom.bound_to_nrs):
                atom.is_radical = True
                self.radicals[atom.nr] = atom
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
        ] + [attributes_to_list(x) for x in self.improper_dihedrals.values()]
        self.atomics["position_restraints"] = [
            attributes_to_list(x) for x in self.position_restraints.values()
        ]
        self.atomics["dihedral_restraints"] = [
            attributes_to_list(x) for x in self.proper_dihedrals.values()
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
            for aj, ak, al in combinations(partners, 3):
                dihedral_candidate_keys.append((ai, aj, ak, al))

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

            candidate_key = tuple(candidate_key)
            dihedral = residue.improper_dihedrals.get(candidate_key)
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
                self.improper_dihedrals[key] = Dihedral(
                    improper.atom1,
                    improper.atom2,
                    improper.atom3,
                    improper.atom4,
                    "4",
                    improper.cq,
                )

    def reindex_atomnrs(self) -> dict[str, str]:
        """Reindex atom numbers in topology.

        Starts at index 1.
        This also updates the numbers for bonds, angles, dihedrals and pairs.
        Returns a dict, mapping of old atom number strings to new ones
        """

        update_map = {
            atom_nr: str(i + 1) for i, atom_nr in enumerate(self.atoms.keys())
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
        new_dihedrals = {}
        for dihedrals in self.proper_dihedrals.values():
            ai = update_map.get(dihedrals.ai)
            aj = update_map.get(dihedrals.aj)
            ak = update_map.get(dihedrals.ak)
            al = update_map.get(dihedrals.al)
            # drop dihedrals to a deleted atom
            if None in (ai, aj, ak, al):
                continue

            # do pairs before the dihedrals are updated
            if pair := self.pairs.get((dihedrals.ai, dihedrals.al)):
                pair_ai = update_map.get(pair.ai)
                pair_aj = update_map.get(pair.aj)
                if not None in (pair_ai, pair_aj):
                    pair.ai = pair_ai
                    pair.aj = pair_aj
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

        new_impropers = {}
        for dihedral in self.improper_dihedrals.values():
            ai = update_map.get(dihedral.ai)
            aj = update_map.get(dihedral.aj)
            ak = update_map.get(dihedral.ak)
            al = update_map.get(dihedral.al)
            # drop dihedrals to a deleted atom
            if None in (ai, aj, ak, al):
                continue
            dihedral.ai = ai  # type: ignore
            dihedral.aj = aj  # type: ignore
            dihedral.ak = ak  # type: ignore
            dihedral.al = al  # type: ignore
            new_impropers[(ai, aj, ak, al)] = dihedral
        self.improper_dihedrals = new_impropers

        return update_map


class Topology:
    """Smart container for parsed topology data.

    A topology keeps track of connections when bonds are broken or formed.
    Reparametrization is triggerd automatically if `to_dict` is called
    after bonds have changed.

    Assumptions:

    - the topology of interest (the protein) is in section 'moleculetype_0'.

    Parameters
    ----------
    top
        A dictionary containing the parsed topology data, produced by
        [](`kimmdy.parsing.read_top`)
    """

    def __init__(
        self, top: TopologyDict, parametrizer: Parameterizer = BasicParameterizer()
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
        self.molecules = {l[0]: l[1] for l in molecules}

        self.ff = FF(top)
        self._parse_molecules()
        self.parametrizer = parametrizer
        self.needs_parameterization = False

        # link atoms, bonds etc. to the main moleculeype, assumed to be the first
        # "moleculetype_0"
        self.main_molecule_ix = 0
        self.main_molecule_name = list(self.moleculetypes.keys())[self.main_molecule_ix]
        self.main_molecule = self.moleculetypes[self.main_molecule_name]
        self.atoms = self.main_molecule.atoms
        self.bonds = self.main_molecule.bonds
        self.angles = self.main_molecule.angles
        self.proper_dihedrals = self.main_molecule.proper_dihedrals
        self.improper_dihedrals = self.moleculetypes[
            self.main_molecule_name
        ].improper_dihedrals
        self.pairs = self.main_molecule.pairs
        self.position_restraints = self.moleculetypes[
            self.main_molecule_name
        ].position_restraints
        self.dihedral_restraints = self.moleculetypes[
            self.main_molecule_name
        ].dihedral_restraints
        self.radicals = self.main_molecule.radicals

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
            name = header[0]
            self.moleculetypes[name] = MoleculeType(header, atomics)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Topology):
            return False
        return self.to_dict() == other.to_dict()

    def _update_dict(self):
        """Update the topology dictionary with the current state of the topology."""
        for i, moleculetype in enumerate(self.moleculetypes.values()):
            moleculetype._update_atomics_dict()
            set_moleculetype_atomics(
                self.top, f"moleculetype_{i}", moleculetype.atomics
            )

        set_top_section(
            self.top,
            "molecules",
            [[name, n] for name, n in self.molecules.items()],
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

        set_top_section(
            self.top,
            "angletypes",
            [attributes_to_list(x) for x in self.ff.angletypes.values()],
        )

    def _update_parameters(self):
        if self.needs_parameterization:
            if self.parametrizer is not None:
                logger.info(
                    f"Starting parametrization using {self.parametrizer.__class__.__name__}"
                )
                self.parametrizer.parameterize_topology(self)
            else:
                raise RuntimeError("No Parametrizer was initialized in this topology!")
            self.needs_parameterization = False

    def to_dict(self) -> TopologyDict:
        self._update_parameters()
        self._update_dict()
        return self.top

    def del_atom(self, atom_nr: str, parameterize: bool = True) -> dict[str, str]:
        """Deletes atom

        Deletes atom and all attached bonds. Reindexes the top and updates the
        parameters if requested.

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
        atom = self.atoms[atom_nr]
        logger.debug(
            f"Deleting Atom nr {atom.nr}, type {atom.type}, res {atom.residue}"
        )

        # break all bonds and delete all pairs, diheadrals etc
        for bound_nr in copy(atom.bound_to_nrs):
            self.break_bond((bound_nr, atom_nr))
        self.radicals.pop(atom_nr)

        self.atoms.pop(atom_nr)
        update_map_all = self.reindex_atomnrs()
        update_map = update_map_all[self.main_molecule_name]

        # temporary fix
        self.atoms = self.main_molecule.atoms
        self.bonds = self.main_molecule.bonds
        self.angles = self.main_molecule.angles
        self.proper_dihedrals = self.main_molecule.proper_dihedrals
        self.improper_dihedrals = self.moleculetypes[
            self.main_molecule_name
        ].improper_dihedrals
        self.pairs = self.main_molecule.pairs
        self.position_restraints = self.moleculetypes[
            self.main_molecule_name
        ].position_restraints
        self.dihedral_restraints = self.moleculetypes[
            self.main_molecule_name
        ].dihedral_restraints
        self.radicals = self.main_molecule.radicals
        #

        if parameterize:
            self._update_parameters()
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
        return update_map_all

    def _regenerate_topology_from_bound_to(self):
        """Regenerate the topology from the bound_to lists of the atoms."""
        for moleculetype in self.moleculetypes.values():
            moleculetype._regenerate_topology_from_bound_to(self.ff)

    def __str__(self) -> str:
        molecules = "\n".join([name + ": " + n for name, n in self.molecules.items()])
        main_molecule = list(self.moleculetypes.values())[0]
        text = (
            textwrap.dedent(f"Topology with the following molecules: \n{molecules}\n\n")
            + textwrap.dedent(f"With {main_molecule}")
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
        if type(atompair_addresses[0]) == str and type(atompair_addresses[1]) == str:
            # old style atompair_nrs with only atom numbers
            # thus refers to the first moleculeype, moleculetype_0
            # with the name Protein
            atompair_nrs = (atompair_addresses[0], atompair_addresses[1])
        else:
            raise NotImplementedError(
                "Breaking/Binding bonds in topology between atoms with "
                "different moleculetypes is not implemented."
            )

        moleculetype = self.main_molecule

        # tuple -> list -> sorted -> tuple still makes it a tuple of two strings
        # so pyright can chill.
        atompair_nrs: tuple[str, str] = tuple(sorted(atompair_nrs, key=int))  # type: ignore

        atompair = [
            moleculetype.atoms[atompair_nrs[0]],
            moleculetype.atoms[atompair_nrs[1]],
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
            moleculetype.radicals[atom.nr] = atom

        # bonds
        # remove bond
        removed_bond = moleculetype.bonds.pop(atompair_nrs)
        logging.debug(f"removed bond: {removed_bond}")

        # remove angles
        angle_keys = moleculetype._get_center_atom_angles(
            atompair_nrs[0]
        ) + moleculetype._get_center_atom_angles(atompair_nrs[1])
        for key in angle_keys:
            if all([x in key for x in atompair_nrs]):
                # angle contained a now deleted bond because
                # it had both atoms of the broken bond
                moleculetype.angles.pop(key, None)

        # remove proper dihedrals
        # and pairs
        dihedral_keys = moleculetype._get_atom_proper_dihedrals(
            atompair_nrs[0]
        ) + moleculetype._get_atom_proper_dihedrals(atompair_nrs[1])
        for key in dihedral_keys:
            # don't use periodicity in key for checking atompair_nrs
            if all([x in key for x in atompair_nrs]):
                # dihedral contained a now deleted bond because
                # it had both atoms of the broken bond
                moleculetype.proper_dihedrals.pop(key, None)
                pairkey: tuple[str, str] = tuple(sorted((key[0], key[3]), key=int))  # type: ignore
                moleculetype.pairs.pop(pairkey, None)

        # and improper dihedrals
        dihedral_k_v = moleculetype._get_atom_improper_dihedrals(
            atompair_nrs[0], self.ff
        ) + moleculetype._get_atom_improper_dihedrals(atompair_nrs[1], self.ff)
        for key, _ in dihedral_k_v:
            if all([x in key for x in atompair_nrs]):
                moleculetype.improper_dihedrals.pop(key, None)

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
        if type(atompair_addresses[0]) == str and type(atompair_addresses[1]) == str:
            # old style atompair_nrs with only atom numbers
            # thus refers to the first moleculeype, moleculetype_0
            # with the name Protein
            atompair_nrs = (atompair_addresses[0], atompair_addresses[1])
        else:
            raise NotImplementedError(
                "Breaking/Binding bonds in topology between atoms with "
                "different moleculetypes is not implemented."
            )

        moleculetype = self.main_molecule

        atompair_nrs: tuple[str, str] = tuple(sorted(atompair_nrs, key=int))  # type: ignore
        atompair = [
            moleculetype.atoms[atompair_nrs[0]],
            moleculetype.atoms[atompair_nrs[1]],
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
                if moleculetype.radicals.pop(a.nr) is None:
                    logging.warning(
                        f"Atom {a} should have been a radical but was "
                        "not registed in moleculetype.radicals"
                    )

        # quickfix for jumping hydrogens
        # to make them adopt the correct residuetype and atomtype
        # when bound to a new heavy atom
        for i, atom in enumerate(atompair):
            if atom.type.startswith("H"):
                other_i = abs(i - 1)
                other_atom = atompair[other_i]
                other_res = other_atom.residue
                atom.residue = other_res
                aa = self.ff.residuetypes.get(other_res)
                if not aa:
                    logging.warning(
                        f"No AA found for {other_res}, to which a H would jump"
                    )
                    continue

                residue_atomnames_current = [
                    a.atom
                    for a in moleculetype.atoms.values()
                    if a.resnr == other_atom.resnr
                ]
                type_set = False
                for key, bond in aa.bonds.items():
                    if other_atom.atom in key and any(k.startswith("H") for k in key):
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
                        else:
                            atom.atom = "HX"
                            type_set = True
                            continue
                        logging.info(f"HAT hydrogen will be bound to {other_atom}.")
                        break
                else:
                    if type_set:
                        logging.warning(
                            "Found new atomtype but not atomname for HAT hydrogen!"
                        )
                    else:
                        logging.warning(
                            "Found neither new atomtype nor atomname for HAT hydrogen!"
                        )

        # update bound_to
        atompair[0].bound_to_nrs.append(atompair[1].nr)
        atompair[1].bound_to_nrs.append(atompair[0].nr)

        # bonds
        # add bond
        bond = Bond(atompair_nrs[0], atompair_nrs[1], "1")
        moleculetype.bonds[atompair_nrs] = bond
        logging.info(f"added bond: {bond}")

        # add angles
        angle_keys = moleculetype._get_atom_angles(
            atompair_nrs[0]
        ) + moleculetype._get_atom_angles(atompair_nrs[1])
        for key in angle_keys:
            if moleculetype.angles.get(key) is None:
                moleculetype.angles[key] = Angle(key[0], key[1], key[2], "1")

        # add proper and improper dihedrals
        # add proper dihedrals and pairs
        # TODO; for now this assumes function type 9 for all dihedrals
        # and adds all possible dihedrals (and pairs)
        # later we should check the ff if there are multiple
        # dihedrals for the same atoms with different periodicities.
        dihedral_keys = moleculetype._get_atom_proper_dihedrals(
            atompair_nrs[0]
        ) + moleculetype._get_atom_proper_dihedrals(atompair_nrs[1])
        for key in dihedral_keys:
            if moleculetype.proper_dihedrals.get(key) is None:
                moleculetype.proper_dihedrals[key] = MultipleDihedrals(
                    *key, "9", dihedrals={"": Dihedral(*key, "9")}
                )
            pairkey: tuple[str, str] = tuple(str(x) for x in sorted([key[0], key[3]], key=int))  # type: ignore
            if moleculetype.pairs.get(pairkey) is None:
                moleculetype.pairs[pairkey] = Pair(pairkey[0], pairkey[1], "1")

        # improper dihedral
        dihedral_k_v = moleculetype._get_atom_improper_dihedrals(
            atompair_nrs[0], self.ff
        ) + moleculetype._get_atom_improper_dihedrals(atompair_nrs[1], self.ff)
        for key, value in dihedral_k_v:
            if moleculetype.improper_dihedrals.get(key) is None:
                # TODO: fix this
                c2 = ""
                if value.q0 is not None:
                    c2 = "1"
                moleculetype.improper_dihedrals[key] = Dihedral(
                    key[0], key[1], key[2], key[3], "4", value.q0, value.cq, c2
                )
